package com.studydaily.app;

import android.content.res.AssetManager;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.RandomAccessFile;
import java.io.UnsupportedEncodingException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URLDecoder;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/**
 * 极简本地 HTTP 服务：
 *   - 只绑定 127.0.0.1，不对外暴露
 *   - 支持 HTTP Range / 206（视频拖动进度条必需，WebView 直接读文件会因 Range 偏移而卡住）
 *   - 静态文件优先从平板上的课程目录读，找不到再回落到 APK 内置的 assets/www
 *   - /api/progress 读写学习进度，存在应用内部目录，清浏览器数据也不会丢
 */
public class MiniServer {

    private static final String ASSET_PREFIX = "www";
    private static final int MAX_HEADER = 32 * 1024;

    private final AssetManager assets;
    private final File progressFile;
    private volatile File root;
    private ServerSocket serverSocket;
    private ExecutorService pool;
    private int port = 0;
    private volatile boolean running = false;

    public MiniServer(AssetManager assets, File progressFile) {
        this.assets = assets;
        this.progressFile = progressFile;
    }

    public void setRoot(File dir) {
        this.root = dir;
    }

    public File getRoot() {
        return root;
    }

    public int getPort() {
        return port;
    }

    public boolean isRunning() {
        return running;
    }

    /** 从 8720 起找一个能绑定的端口 */
    public void start() throws IOException {
        if (running) return;
        ServerSocket ss = new ServerSocket();
        ss.setReuseAddress(true);
        int p = 8720;
        IOException last = null;
        while (p <= 8760) {
            try {
                ss.bind(new InetSocketAddress(InetAddress.getByName("127.0.0.1"), p), 64);
                last = null;
                break;
            } catch (IOException e) {
                last = e;
                p++;
            }
        }
        if (last != null) throw last;
        serverSocket = ss;
        port = p;
        running = true;
        pool = Executors.newFixedThreadPool(8);
        Thread t = new Thread(new Runnable() {
            @Override
            public void run() {
                acceptLoop();
            }
        }, "mini-http-accept");
        t.setDaemon(true);
        t.start();
    }

    public void stop() {
        running = false;
        try {
            if (serverSocket != null) serverSocket.close();
        } catch (IOException ignored) {
        }
        if (pool != null) {
            pool.shutdownNow();
            try {
                pool.awaitTermination(1, TimeUnit.SECONDS);
            } catch (InterruptedException ignored) {
            }
        }
    }

    private void acceptLoop() {
        while (running) {
            Socket sock;
            try {
                sock = serverSocket.accept();
            } catch (IOException e) {
                if (!running) return;
                continue;
            }
            final Socket s = sock;
            try {
                pool.execute(new Runnable() {
                    @Override
                    public void run() {
                        handle(s);
                    }
                });
            } catch (RuntimeException e) {
                closeQuietly(s);
            }
        }
    }

    private static void closeQuietly(Socket s) {
        try {
            s.close();
        } catch (IOException ignored) {
        }
    }

    // ---------------------------------------------------------------- 请求处理

    private void handle(Socket sock) {
        try {
            sock.setSoTimeout(20000);
            InputStream in = sock.getInputStream();
            OutputStream out = sock.getOutputStream();

            byte[] head = readHead(in);
            if (head == null) {
                closeQuietly(sock);
                return;
            }
            String text = new String(head, "UTF-8");
            int lineEnd = text.indexOf("\r\n");
            if (lineEnd < 0) {
                closeQuietly(sock);
                return;
            }
            String requestLine = text.substring(0, lineEnd);
            String[] parts = requestLine.split(" ");
            if (parts.length < 2) {
                closeQuietly(sock);
                return;
            }
            String method = parts[0];
            String target = parts[1];

            Map<String, String> headers = parseHeaders(text.substring(lineEnd + 2));

            String path = target;
            int q = path.indexOf('?');
            if (q >= 0) path = path.substring(0, q);
            path = urlDecode(path);

            if (path.startsWith("/api/")) {
                handleApi(method, path, headers, in, out);
            } else if ("GET".equals(method) || "HEAD".equals(method)) {
                boolean headOnly = "HEAD".equals(method);
                serveStatic(path, headers, out, headOnly);
            } else {
                sendText(out, 405, "Method Not Allowed", "text/plain; charset=utf-8", "405");
            }
            safeFlush(out);
        } catch (Exception e) {
            // 客户端主动断开（视频拖动时会大量发生）属正常，不打印
        } finally {
            closeQuietly(sock);
        }
    }

    private static void safeFlush(OutputStream out) {
        try {
            out.flush();
        } catch (IOException ignored) {
        }
    }

    /** 读到请求头结束（CRLFCRLF 或 LFLF）为止；逐字节读，保证后面的 body 不被吞掉 */
    private byte[] readHead(InputStream in) throws IOException {
        byte[] buf = new byte[MAX_HEADER];
        int n = 0;
        while (n < MAX_HEADER) {
            int b = in.read();
            if (b < 0) break;
            buf[n++] = (byte) b;
            if (n >= 4 && buf[n - 4] == 13 && buf[n - 3] == 10 && buf[n - 2] == 13 && buf[n - 1] == 10) break;
            if (n >= 2 && buf[n - 2] == 10 && buf[n - 1] == 10) break;
        }
        if (n == 0) return null;
        byte[] out = new byte[n];
        System.arraycopy(buf, 0, out, 0, n);
        return out;
    }

    private Map<String, String> parseHeaders(String block) {
        Map<String, String> map = new HashMap<String, String>();
        String[] lines = block.split("\r\n");
        for (String line : lines) {
            int c = line.indexOf(':');
            if (c <= 0) continue;
            map.put(line.substring(0, c).trim().toLowerCase(), line.substring(c + 1).trim());
        }
        return map;
    }

    private String urlDecode(String s) {
        try {
            return URLDecoder.decode(s, "UTF-8");
        } catch (UnsupportedEncodingException e) {
            return s;
        } catch (IllegalArgumentException e) {
            return s;
        }
    }

    // ---------------------------------------------------------------- API

    private void handleApi(String method, String path, Map<String, String> headers,
                           InputStream in, OutputStream out) throws IOException {
        if (path.equals("/api/ping")) {
            String body = "{\"ok\":true,\"server\":\"StudyDaily-App\",\"progressApi\":true}";
            sendText(out, 200, "OK", "application/json; charset=utf-8", body);
            return;
        }
        if (path.equals("/api/progress")) {
            if ("GET".equals(method)) {
                String body = readProgress();
                sendText(out, 200, "OK", "application/json; charset=utf-8", body);
                return;
            }
            if ("POST".equals(method)) {
                int len = parseInt(headers.get("content-length"), 0);
                if (len <= 0 || len > 8 * 1024 * 1024) {
                    sendText(out, 400, "Bad Request", "application/json; charset=utf-8", "{\"ok\":false}");
                    return;
                }
                byte[] data = new byte[len];
                int read = 0;
                while (read < len) {
                    int n = in.read(data, read, len - read);
                    if (n < 0) break;
                    read += n;
                }
                writeProgress(new String(data, 0, read, "UTF-8"));
                sendText(out, 200, "OK", "application/json; charset=utf-8", "{\"ok\":true}");
                return;
            }
        }
        sendText(out, 404, "Not Found", "application/json; charset=utf-8", "{\"ok\":false}");
    }

    private String readProgress() {
        try {
            if (!progressFile.exists()) return "{}";
            RandomAccessFile raf = new RandomAccessFile(progressFile, "r");
            try {
                byte[] b = new byte[(int) raf.length()];
                raf.readFully(b);
                String s = new String(b, "UTF-8").trim();
                return s.length() == 0 ? "{}" : s;
            } finally {
                raf.close();
            }
        } catch (Exception e) {
            return "{}";
        }
    }

    private synchronized void writeProgress(String json) {
        File tmp = new File(progressFile.getParentFile(), progressFile.getName() + ".tmp");
        try {
            FileOutputStream fos = new FileOutputStream(tmp);
            try {
                fos.write(json.getBytes("UTF-8"));
                fos.flush();
                fos.getFD().sync();
            } finally {
                fos.close();
            }
            if (progressFile.exists() && !progressFile.delete()) {
                // 删不掉就覆盖写
            }
            if (!tmp.renameTo(progressFile)) {
                // rename 失败时退化为直接覆盖
                FileOutputStream fos2 = new FileOutputStream(progressFile);
                try {
                    fos2.write(json.getBytes("UTF-8"));
                } finally {
                    fos2.close();
                }
                tmp.delete();
            }
        } catch (Exception e) {
            try {
                if (tmp.exists()) tmp.delete();
            } catch (Exception ignored) {
            }
        }
    }

    // ---------------------------------------------------------------- 静态文件

    private void serveStatic(String rawPath, Map<String, String> headers,
                             OutputStream out, boolean headOnly) throws IOException {
        String path = rawPath;
        if (path.length() == 0 || path.equals("/")) path = "/index.html";
        if (path.indexOf("..") >= 0) {
            sendText(out, 403, "Forbidden", "text/plain; charset=utf-8", "403");
            return;
        }

        // 1) 平板上的课程目录
        File file = null;
        File r = root;
        if (r != null) {
            File f = new File(r, path.substring(1));
            if (f.isFile()) file = f;
        }
        if (file != null) {
            serveFile(file, headers, out, headOnly);
            return;
        }

        // 2) APK 内置资源
        InputStream asset = null;
        if (assets != null) {
            try {
                asset = assets.open(ASSET_PREFIX + path);
            } catch (IOException ignored) {
            } catch (RuntimeException ignored) {
            }
        }
        if (asset != null) {
            serveAsset(asset, path, headers, out, headOnly);
            return;
        }

        // 3) 单页应用兜底：找不到静态文件时返回首页，避免刷新 404
        if (path.lastIndexOf('.') < 0 && assets != null) {
            try {
                InputStream idx = assets.open(ASSET_PREFIX + "/index.html");
                serveAsset(idx, "/index.html", headers, out, headOnly);
                return;
            } catch (IOException ignored) {
            } catch (RuntimeException ignored) {
            }
        }

        sendText(out, 404, "Not Found", "text/plain; charset=utf-8", "找不到文件：" + path);
    }

    private void serveFile(File file, Map<String, String> headers,
                           OutputStream out, boolean headOnly) throws IOException {
        long len = file.length();
        String mime = mimeOf(file.getName());
        String range = headers.get("range");
        long start = 0, end = len - 1;
        boolean partial = false;

        if (range != null && range.startsWith("bytes=") && len > 0) {
            long[] rp = parseRange(range, len);
            if (rp == null) {
                writeHead(out, "416 Requested Range Not Satisfiable",
                        "Content-Range", "bytes */" + len,
                        "Content-Type", mime,
                        "Accept-Ranges", "bytes");
                return;
            }
            start = rp[0];
            end = rp[1];
            partial = true;
        }

        long contentLen = (len == 0) ? 0 : (end - start + 1);

        Map<String, String> h = new LinkedHashMap<String, String>();
        h.put("Content-Type", mime);
        h.put("Accept-Ranges", "bytes");
        h.put("Content-Length", String.valueOf(contentLen));
        h.put("Cache-Control", "no-cache");
        if (partial) h.put("Content-Range", "bytes " + start + "-" + end + "/" + len);

        writeHead(out, partial ? "206 Partial Content" : "200 OK", h);
        if (headOnly || contentLen == 0) {
            safeFlush(out);
            return;
        }

        RandomAccessFile raf = new RandomAccessFile(file, "r");
        try {
            raf.seek(start);
            byte[] buf = new byte[64 * 1024];
            long remaining = contentLen;
            while (remaining > 0) {
                int want = (int) Math.min(buf.length, remaining);
                int n = raf.read(buf, 0, want);
                if (n < 0) break;
                out.write(buf, 0, n);
                remaining -= n;
            }
            safeFlush(out);
        } finally {
            raf.close();
        }
    }

    private void serveAsset(InputStream asset, String path, Map<String, String> headers,
                            OutputStream out, boolean headOnly) throws IOException {
        byte[] data;
        try {
            ByteArrayOutputStream bos = new ByteArrayOutputStream(32 * 1024);
            byte[] buf = new byte[32 * 1024];
            int n;
            while ((n = asset.read(buf)) > 0) bos.write(buf, 0, n);
            data = bos.toByteArray();
        } finally {
            try {
                asset.close();
            } catch (IOException ignored) {
            }
        }
        long len = data.length;
        String mime = mimeOf(path);
        long start = 0, end = len - 1;
        boolean partial = false;
        String range = headers.get("range");
        if (range != null && range.startsWith("bytes=") && len > 0) {
            long[] rp = parseRange(range, len);
            if (rp == null) {
                writeHead(out, "416 Requested Range Not Satisfiable",
                        "Content-Range", "bytes */" + len,
                        "Content-Type", mime,
                        "Accept-Ranges", "bytes");
                return;
            }
            start = rp[0];
            end = rp[1];
            partial = true;
        }
        long contentLen = (len == 0) ? 0 : (end - start + 1);
        Map<String, String> h = new LinkedHashMap<String, String>();
        h.put("Content-Type", mime);
        h.put("Accept-Ranges", "bytes");
        h.put("Content-Length", String.valueOf(contentLen));
        h.put("Cache-Control", "no-cache");
        if (partial) h.put("Content-Range", "bytes " + start + "-" + end + "/" + len);
        writeHead(out, partial ? "206 Partial Content" : "200 OK", h);
        if (headOnly || contentLen == 0) {
            safeFlush(out);
            return;
        }
        out.write(data, (int) start, (int) contentLen);
        safeFlush(out);
    }

    /** 解析 bytes=start-end / bytes=start- / bytes=-suffix，越界返回 null */
    private long[] parseRange(String header, long len) {
        try {
            String spec = header.substring(6).trim();
            int comma = spec.indexOf(',');
            if (comma > 0) spec = spec.substring(0, comma).trim();
            int dash = spec.indexOf('-');
            if (dash < 0) return null;
            String s = spec.substring(0, dash).trim();
            String e = spec.substring(dash + 1).trim();
            long start, end;
            if (s.length() == 0) {
                if (e.length() == 0) return null;
                long suffix = Long.parseLong(e);
                if (suffix <= 0) return null;
                start = Math.max(0, len - suffix);
                end = len - 1;
            } else {
                start = Long.parseLong(s);
                end = e.length() == 0 ? len - 1 : Long.parseLong(e);
            }
            if (start < 0) start = 0;
            if (end >= len) end = len - 1;
            if (start > end || start >= len) return null;
            return new long[]{start, end};
        } catch (Exception ex) {
            return null;
        }
    }

    // ---------------------------------------------------------------- 输出帮助

    private void sendText(OutputStream out, int code, String reason, String mime, String body)
            throws IOException {
        byte[] b = body.getBytes("UTF-8");
        Map<String, String> h = new LinkedHashMap<String, String>();
        h.put("Content-Type", mime);
        h.put("Content-Length", String.valueOf(b.length));
        h.put("Cache-Control", "no-store");
        writeHead(out, code + " " + reason, h);
        out.write(b);
        safeFlush(out);
    }

    private void writeHead(OutputStream out, String status, Map<String, String> headers)
            throws IOException {
        StringBuilder sb = new StringBuilder();
        sb.append("HTTP/1.1 ").append(status).append("\r\n");
        for (Map.Entry<String, String> e : headers.entrySet()) {
            sb.append(e.getKey()).append(": ").append(e.getValue()).append("\r\n");
        }
        sb.append("Connection: close\r\n\r\n");
        out.write(sb.toString().getBytes("UTF-8"));
    }

    private void writeHead(OutputStream out, String status, String... kv) throws IOException {
        Map<String, String> h = new LinkedHashMap<String, String>();
        for (int i = 0; i + 1 < kv.length; i += 2) h.put(kv[i], kv[i + 1]);
        writeHead(out, status, h);
    }

    private static int parseInt(String s, int def) {
        try {
            return Integer.parseInt(s.trim());
        } catch (Exception e) {
            return def;
        }
    }

    private static String mimeOf(String name) {
        String n = name.toLowerCase();
        int dot = n.lastIndexOf('.');
        String ext = dot >= 0 ? n.substring(dot + 1) : "";
        if (ext.equals("html") || ext.equals("htm")) return "text/html; charset=utf-8";
        if (ext.equals("js")) return "application/javascript; charset=utf-8";
        if (ext.equals("css")) return "text/css; charset=utf-8";
        if (ext.equals("json")) return "application/json; charset=utf-8";
        if (ext.equals("webmanifest") || ext.equals("json5")) return "application/manifest+json; charset=utf-8";
        if (ext.equals("png")) return "image/png";
        if (ext.equals("jpg") || ext.equals("jpeg")) return "image/jpeg";
        if (ext.equals("gif")) return "image/gif";
        if (ext.equals("svg")) return "image/svg+xml";
        if (ext.equals("webp")) return "image/webp";
        if (ext.equals("mp4") || ext.equals("m4v")) return "video/mp4";
        if (ext.equals("webm")) return "video/webm";
        if (ext.equals("ts")) return "video/mp2t";
        if (ext.equals("mkv")) return "video/x-matroska";
        if (ext.equals("mp3")) return "audio/mpeg";
        if (ext.equals("m4a") || ext.equals("aac")) return "audio/mp4";
        if (ext.equals("ogg") || ext.equals("opus")) return "audio/ogg";
        if (ext.equals("wav")) return "audio/wav";
        if (ext.equals("pdf")) return "application/pdf";
        if (ext.equals("txt")) return "text/plain; charset=utf-8";
        if (ext.equals("woff2")) return "font/woff2";
        if (ext.equals("ico")) return "image/x-icon";
        return "application/octet-stream";
    }
}
