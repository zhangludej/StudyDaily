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
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URL;
import java.net.URLDecoder;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
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

    /** 云端课件源，按实测速度排序；启动时还会再测一次把最快的提到第一位 */
    private static final String[] REMOTE_BASES = new String[]{
            "https://gh-proxy.com/https://raw.githubusercontent.com/zhangludej/StudyDaily-courseware/main/",
            "https://ghfast.top/https://raw.githubusercontent.com/zhangludej/StudyDaily-courseware/main/",
            "https://raw.githubusercontent.com/zhangludej/StudyDaily-courseware/main/",
            "https://ghproxy.net/https://raw.githubusercontent.com/zhangludej/StudyDaily-courseware/main/"
    };
    /** 缓存上限：超过就按最久未看淘汰 */
    private static final long CACHE_LIMIT = 2L * 1024 * 1024 * 1024;
    /** 单集下载上限，防止异常响应写爆磁盘 */
    private static final long SINGLE_FILE_LIMIT = 260L * 1024 * 1024;

    private final AssetManager assets;
    private final File progressFile;
    private volatile File root;
    private volatile File cacheDir;
    private final java.util.Set<String> downloading =
            java.util.Collections.synchronizedSet(new java.util.HashSet<String>());
    /** 实测最快的源，联网正常时会在启动后几秒内确定 */
    private volatile String fastestBase = null;
    private volatile boolean probed = false;
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

    /** 视频缓存目录：放在应用私有外部目录，不需要任何存储权限 */
    public void setCacheDir(File dir) {
        this.cacheDir = dir;
        if (dir != null && !dir.isDirectory()) dir.mkdirs();
    }

    public File getCacheDir() {
        return cacheDir;
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
        probeAsync();
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
        if (path.equals("/api/cache")) {
            if ("GET".equals(method)) {
                sendText(out, 200, "OK", "application/json; charset=utf-8", cacheStatsJson());
                return;
            }
            if ("POST".equals(method)) {
                clearCache();
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

        // 2) 云端看过的课：缓存里有一份就直接播（离线可用，秒开）
        File cached = cacheFileOf(path);
        if (cached != null && cached.isFile()) {
            cached.setLastModified(System.currentTimeMillis());
            serveFile(cached, headers, out, headOnly);
            return;
        }

        // 3) 视频 / 练习册页：本地和缓存都没有就走云端，边用边缓存
        if (path.startsWith("/videos/") || path.startsWith("/workbooks/")) {
            serveRemoteVideo(path, headers, out, headOnly);
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

    // ---------------------------------------------------------------- 云端回源 + 边看边缓存

    /** 公益镜像在墙内通常比 GitHub 直连稳，启动后测速挑最快的排最前 */
    private String[] orderedBases() {
        String best = fastestBase;
        if (best == null) return REMOTE_BASES;
        String[] arr = new String[REMOTE_BASES.length];
        arr[0] = best;
        int k = 1;
        for (int i = 0; i < REMOTE_BASES.length; i++) {
            if (!REMOTE_BASES[i].equals(best)) arr[k++] = REMOTE_BASES[i];
        }
        return arr;
    }

    /** 取每源各 128KB 粗测速，几秒内定出首选源 */
    public void probeAsync() {
        if (probed) return;
        probed = true;
        Thread t = new Thread(new Runnable() {
            @Override
            public void run() {
                String probePath = "videos/u01/001.mp4";
                String best = null;
                long bestSpeed = 0;
                for (int i = 0; i < REMOTE_BASES.length; i++) {
                    HttpURLConnection conn = null;
                    try {
                        conn = (HttpURLConnection) new URL(REMOTE_BASES[i] + probePath).openConnection();
                        conn.setConnectTimeout(4000);
                        conn.setReadTimeout(4000);
                        conn.setRequestProperty("User-Agent", "StudyDaily/1.0");
                        conn.setRequestProperty("Range", "bytes=0-131071");
                        int code = conn.getResponseCode();
                        if (code != 206 && code != 200) {
                            conn.disconnect();
                            continue;
                        }
                        long t0 = System.currentTimeMillis();
                        InputStream is = conn.getInputStream();
                        byte[] buf = new byte[16 * 1024];
                        long got = 0;
                        int n;
                        while (got < 131072 && (n = is.read(buf)) > 0) got += n;
                        long dt = Math.max(1L, System.currentTimeMillis() - t0);
                        long speed = got * 1000L / dt;
                        try { is.close(); } catch (IOException ignored) { }
                        conn.disconnect();
                        if (speed > bestSpeed) {
                            bestSpeed = speed;
                            best = REMOTE_BASES[i];
                        }
                    } catch (Exception e) {
                        if (conn != null) conn.disconnect();
                    }
                }
                if (best != null) fastestBase = best;
            }
        }, "cw-probe");
        t.setDaemon(true);
        t.start();
    }

    private File cacheFileOf(String path) {        File dir = cacheDir;
        if (dir == null) return null;
        if (path.indexOf("..") >= 0) return null;
        return new File(dir, path.substring(1));
    }

    /**
     * 云端点播：把 WebView 的 Range 请求原样转发给云端，边收边吐，
     * 同时后台拉一份完整文件进缓存，下次直接本地播（离线可用）。
     */
    private void serveRemoteVideo(String path, Map<String, String> headers,
                                  OutputStream out, boolean headOnly) {
        File target = cacheFileOf(path);
        if (target != null && !target.isFile()) {
            startBackgroundDownload(path, target);
        }

        String range = headers.get("range");
        Exception last = null;
        String[] bases = orderedBases();
        for (int i = 0; i < bases.length; i++) {
            HttpURLConnection conn = null;
            try {
                conn = (HttpURLConnection) new URL(bases[i] + path.substring(1)).openConnection();
                conn.setConnectTimeout(8000);
                conn.setReadTimeout(20000);
                conn.setInstanceFollowRedirects(true);
                conn.setRequestProperty("User-Agent", "StudyDaily/1.0");
                conn.setRequestProperty("Accept-Encoding", "identity");
                if (range != null) conn.setRequestProperty("Range", range);

                int code = conn.getResponseCode();
                if (code != 200 && code != 206) {
                    last = new IOException("HTTP " + code);
                    conn.disconnect();
                    continue;
                }
                String mime = conn.getContentType();
                if (mime == null || mime.length() == 0) mime = mimeOf(path);
                Map<String, String> h = new LinkedHashMap<String, String>();
                h.put("Content-Type", mime);
                h.put("Accept-Ranges", "bytes");
                h.put("Cache-Control", "no-cache");
                String cl = conn.getHeaderField("Content-Length");
                if (cl != null) h.put("Content-Length", cl);
                String cr = conn.getHeaderField("Content-Range");
                if (cr != null) h.put("Content-Range", cr);
                writeHead(out, code == 206 ? "206 Partial Content" : "200 OK", h);
                if (headOnly) {
                    safeFlush(out);
                    conn.disconnect();
                    return;
                }
                InputStream is = conn.getInputStream();
                try {
                    byte[] buf = new byte[64 * 1024];
                    int n;
                    while ((n = is.read(buf)) > 0) out.write(buf, 0, n);
                } finally {
                    try { is.close(); } catch (IOException ignored) { }
                    conn.disconnect();
                }
                safeFlush(out);
                return;
            } catch (Exception e) {
                last = e;
                if (conn != null) conn.disconnect();
            }
        }
        // 所有源都失败：多半是没联网，且这集还没缓存
        try {
            sendText(out, 504, "Gateway Timeout", "text/plain; charset=utf-8",
                    "这一集还没存到本机，需要连接网络才能看" + (last == null ? "" : "（" + last.getMessage() + "）"));
        } catch (IOException ignored) {
        }
    }

    /** 后台整集下载：单线程、先写 .part、完成再改名，避免半截文件被当成缓存 */
    private void startBackgroundDownload(final String path, final File target) {
        if (cacheDir == null) return;
        if (!downloading.add(path)) return;
        Thread t = new Thread(new Runnable() {
            @Override
            public void run() {
                File parent = target.getParentFile();
                if (parent != null) parent.mkdirs();
                File part = new File(target.getPath() + ".part");
                try {
                    String[] bases = orderedBases();
                    for (int i = 0; i < bases.length; i++) {
                        HttpURLConnection conn = null;
                        OutputStream os = null;
                        try {
                            conn = (HttpURLConnection) new URL(bases[i] + path.substring(1)).openConnection();
                            conn.setConnectTimeout(8000);
                            conn.setReadTimeout(25000);
                            conn.setInstanceFollowRedirects(true);
                            conn.setRequestProperty("User-Agent", "StudyDaily/1.0");
                            conn.setRequestProperty("Accept-Encoding", "identity");
                            if (conn.getResponseCode() != 200) {
                                conn.disconnect();
                                continue;
                            }
                            os = new FileOutputStream(part);
                            InputStream is = conn.getInputStream();
                            byte[] buf = new byte[64 * 1024];
                            int n;
                            long total = 0;
                            boolean tooBig = false;
                            while ((n = is.read(buf)) > 0) {
                                os.write(buf, 0, n);
                                total += n;
                                if (total > SINGLE_FILE_LIMIT) { tooBig = true; break; }
                            }
                            try { is.close(); } catch (IOException ignored) { }
                            try { os.close(); } catch (IOException ignored) { }
                            os = null;
                            conn.disconnect();
                            if (tooBig) { part.delete(); break; }
                            if (part.length() > 4096) {
                                if (target.exists()) target.delete();
                                part.renameTo(target);
                                evictIfNeeded();
                            } else {
                                part.delete();
                            }
                            return;
                        } catch (Exception e) {
                            try { if (os != null) os.close(); } catch (IOException ignored) { }
                            if (conn != null) conn.disconnect();
                            if (part.exists()) part.delete();
                        }
                    }
                } finally {
                    downloading.remove(path);
                }
            }
        }, "cw-download");
        t.setDaemon(true);
        t.start();
    }

    /** 超上限就按「最久未看」淘汰 */
    private synchronized void evictIfNeeded() {
        File dir = cacheDir;
        if (dir == null) return;
        List<File> files = new ArrayList<File>();
        long total = 0;
        collectCacheFiles(dir, files);
        for (int i = 0; i < files.size(); i++) total += files.get(i).length();
        if (total <= CACHE_LIMIT) return;
        // 按最后访问时间升序删
        java.util.Collections.sort(files, new java.util.Comparator<File>() {
            @Override
            public int compare(File a, File b) {
                return Long.valueOf(a.lastModified()).compareTo(b.lastModified());
            }
        });
        for (int i = 0; i < files.size() && total > CACHE_LIMIT; i++) {
            long len = files.get(i).length();
            if (files.get(i).delete()) total -= len;
        }
    }

    private void collectCacheFiles(File dir, List<File> out) {
        if (dir == null || !dir.isDirectory()) return;
        File[] fs = dir.listFiles();
        if (fs == null) return;
        for (int i = 0; i < fs.length; i++) {
            File f = fs[i];
            if (f.isDirectory()) collectCacheFiles(f, out);
            else if (!f.getName().endsWith(".part")) out.add(f);
        }
    }

    private String cacheStatsJson() {
        File dir = cacheDir;
        List<File> files = new ArrayList<File>();
        long total = 0;
        if (dir != null) {
            collectCacheFiles(dir, files);
            for (int i = 0; i < files.size(); i++) total += files.get(i).length();
        }
        StringBuilder sb = new StringBuilder();
        sb.append("{\"count\":").append(files.size())
                .append(",\"bytes\":").append(total)
                .append(",\"limit\":").append(CACHE_LIMIT)
                .append(",\"downloading\":").append(downloading.size())
                .append(",\"items\":[");
        for (int i = 0; i < files.size() && i < 200; i++) {
            File f = files.get(i);
            if (i > 0) sb.append(',');
            String name = f.getName();
            String unit = f.getParentFile() == null ? "" : f.getParentFile().getName();
            sb.append("{\"id\":\"").append(unit).append('/').append(name).append("\",\"mb\":")
                    .append(f.length() / (1024 * 1024)).append('}');
        }
        sb.append("]}");
        return sb.toString();
    }

    private void clearCache() {
        File dir = cacheDir;
        if (dir == null) return;
        deleteTree(dir);
    }

    private void deleteTree(File f) {
        if (f == null) return;
        if (f.isDirectory()) {
            File[] fs = f.listFiles();
            if (fs != null) {
                for (int i = 0; i < fs.length; i++) deleteTree(fs[i]);
            }
        }
        f.delete();
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
