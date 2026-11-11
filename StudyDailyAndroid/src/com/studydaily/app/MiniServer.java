package com.studydaily.app;

import android.content.ContentResolver;
import android.content.Context;
import android.content.res.AssetManager;
import android.database.Cursor;
import android.net.Uri;
import android.provider.DocumentsContract;
import android.provider.OpenableColumns;

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

    /**
     * 云端课件源。只保留实测能通的三家：源越少，失败时用户等的时间越短。
     * 顺序会被 fastestBase（实测最快）和健康度重排。
     */
    private static final String[] REMOTE_BASES = new String[]{
            "https://gh-proxy.com/https://raw.githubusercontent.com/zhangludej/StudyDaily-courseware/main/",
            "https://ghfast.top/https://raw.githubusercontent.com/zhangludej/StudyDaily-courseware/main/",
            "https://raw.githubusercontent.com/zhangludej/StudyDaily-courseware/main/"
    };
    /**
     * 练习册页、海报这类几十 KB 的小图走 CDN（jsDelivr 对静态小文件命中快），
     * 视频那种几十 MB 的文件才用 github 代理。
     */
    private static final String[] PAGE_BASES = new String[]{
            "https://cdn.jsdelivr.net/gh/zhangludej/StudyDaily-courseware@main/",
            "https://gh-proxy.com/https://raw.githubusercontent.com/zhangludej/StudyDaily-courseware/main/",
            "https://gcore.jsdelivr.net/gh/zhangludej/StudyDaily-courseware@main/",
            "https://raw.githubusercontent.com/zhangludej/StudyDaily-courseware/main/"
    };
    /** 缓存上限：超过就按最久未看淘汰 */    private static final long CACHE_LIMIT = 2L * 1024 * 1024 * 1024;
    /** 单集下载上限，防止异常响应写爆磁盘 */
    private static final long SINGLE_FILE_LIMIT = 260L * 1024 * 1024;
    /** 公益代理首包慢且抖，连接/读取超时都给足，否则第一集之后全部失败 */
    private static final int CONNECT_TIMEOUT = 20000;
    private static final int READ_TIMEOUT = 60000;
    /** 拿到首字节的总预算：超过就放弃，避免用户干等 */
    private static final long FIRST_BYTE_BUDGET = 50000L;
    /** 云端回源并发上限：代理对单 IP 限速，开太多路反而每路都更慢 */
    private static final int MAX_REMOTE_STREAMS = 6;
    /** 后台缓存下载限速 + 让路，保证播放优先 */
    private static final long BG_DOWNLOAD_BPS = 200L * 1024L;

    private final AssetManager assets;
    private final File progressFile;
    private volatile File root;
    private volatile File cacheDir;
    private final java.util.Set<String> downloading =
            java.util.Collections.synchronizedSet(new java.util.HashSet<String>());
    /** 实测最快的源，联网正常时会在启动后几秒内确定 */
    private volatile String fastestBase = null;
    private volatile boolean probed = false;
    /** 各源近期失败次数，用来把不靠谱的源排到后面 */
    private final Map<String, Integer> baseFails =
            java.util.Collections.synchronizedMap(new HashMap<String, Integer>());
    /** 正在给客户端吐流的连接数，后台下载看到 >0 就让路 */
    private final java.util.concurrent.atomic.AtomicInteger activeStreams =
            new java.util.concurrent.atomic.AtomicInteger(0);
    /** 云端回源并发闸门 */
    private final java.util.concurrent.Semaphore remoteGate =
            new java.util.concurrent.Semaphore(MAX_REMOTE_STREAMS, true);
    /** 后台缓存下载队列：串行，一次只下一集，避免把带宽吃光 */
    private final ExecutorService downloadPool = Executors.newSingleThreadExecutor();
    /** 最快源持久化文件（下次启动不用再等测速） */
    private volatile File netBestFile = null;
    /** 家长要求启用本地课件时的回调（Activity 弹文件夹选择器） */
    private volatile Runnable onRequestLocalAccess = null;
    /** 家长用系统文件选择器指定的离线课件文件夹（免「所有文件访问」权限） */
    private volatile Uri treeUri = null;
    private volatile ContentResolver resolver = null;
    private volatile String treeName = "";
    /** 课件文件夹是否真的能用，验证一次后缓存，避免每次请求都探测 */
    private volatile Boolean treeOk = null;
    /** 已定位过的课件文件（拖动进度条会发很多次 Range 请求，缓存省掉重复查找） */
    private final Map<String, TreeFile> treeCache =
            java.util.Collections.synchronizedMap(new HashMap<String, TreeFile>());
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

    /** 本机课件文件夹是否可用：直接目录看 courses.json，选定的文件夹看有没有视频 */
    public boolean localReady() {
        File r = root;
        if (r != null && new File(r, "courses.json").isFile()) return true;
        if (treeUri == null || resolver == null) return false;
        Boolean known = treeOk;
        if (known != null) return known.booleanValue();
        boolean ok = false;
        String[] probes = new String[]{"videos/u01/001.mp4", "videos/u02/001.mp4",
                "videos/u03/001.mp4", "videos/u04/001.mp4"};
        for (int i = 0; i < probes.length && !ok; i++) {
            ok = statInTree(probes[i]) != null;
        }
        treeOk = Boolean.valueOf(ok);
        return ok;
    }

    /** 家长用系统文件选择器选好了课件文件夹（支持只选含 videos 的那一层） */
    public void setLocalTree(Context ctx, Uri uri) {
        if (ctx == null || uri == null) {
            treeUri = null;
            treeOk = null;
            return;
        }
        resolver = ctx.getApplicationContext().getContentResolver();
        treeUri = uri;
        treeOk = null;
        treeCache.clear();
        String n = uri.getLastPathSegment();
        if (n != null) {
            int c = n.indexOf(':');
            treeName = c >= 0 ? n.substring(c + 1) : n;
        } else {
            treeName = "";
        }
    }

    public Uri getLocalTree() {
        return treeUri;
    }

    public String getLocalTreeName() {
        return treeName == null ? "" : treeName;
    }

    /** 家长点「使用本地课件」时回调，由 Activity 弹出系统授权页 */
    public void setLocalAccessHandler(Runnable r) {
        this.onRequestLocalAccess = r;
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
        pool = Executors.newFixedThreadPool(24);
        Thread t = new Thread(new Runnable() {
            @Override
            public void run() {
                acceptLoop();
            }
        }, "mini-http-accept");
        t.setDaemon(true);
        t.start();
        loadFastestBase();
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
        downloadPool.shutdownNow();
    }

    /** 上次测出的最快源，直接复用，省掉首播前的等待 */
    private void loadFastestBase() {
        try {
            File p = progressFile.getParentFile();
            if (p == null) return;
            File f = new File(p, "netbest.txt");
            netBestFile = f;
            if (!f.isFile()) return;
            RandomAccessFile raf = new RandomAccessFile(f, "r");
            try {
                byte[] b = new byte[(int) Math.min(512, raf.length())];
                raf.readFully(b);
                String s = new String(b, "UTF-8").trim();
                for (int i = 0; i < REMOTE_BASES.length; i++) {
                    if (REMOTE_BASES[i].equals(s)) {
                        fastestBase = s;
                        break;
                    }
                }
            } finally {
                raf.close();
            }
        } catch (Exception ignored) {
        }
    }

    private void saveFastestBase(String base) {
        File f = netBestFile;
        if (f == null || base == null) return;
        try {
            FileOutputStream fos = new FileOutputStream(f);
            try {
                fos.write(base.getBytes("UTF-8"));
            } finally {
                fos.close();
            }
        } catch (Exception ignored) {
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
        // 本地课件状态：ready=true 表示本机有完整课件文件夹，可完全离线使用
        if (path.equals("/api/local")) {
            boolean ready = localReady();
            if ("POST".equals(method) && !ready) {
                Runnable h = onRequestLocalAccess;
                if (h != null) h.run();
            }
            String dir = "";
            File r = root;
            if (r != null) {
                dir = r.getAbsolutePath().replace("\\", "\\\\").replace("\"", "\\\"");
            }
            if (dir.length() == 0 && treeUri != null) {
                dir = ("已选择的文件夹 " + (treeName == null ? "" : treeName))
                        .replace("\\", "\\\\").replace("\"", "\\\"");
            }
            sendText(out, 200, "OK", "application/json; charset=utf-8",
                    "{\"ok\":true,\"ready\":" + ready
                            + ",\"picked\":" + (treeUri != null)
                            + ",\"dir\":\"" + dir + "\"}");
            return;
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

        // 1.5) 家长选定的离线课件文件夹（SAF，手机存任意位置都行，不需要存储权限）
        if (treeUri != null && (path.startsWith("/videos/") || path.startsWith("/workbooks/"))) {
            TreeFile tf = statInTree(path.substring(1));
            if (tf != null) {
                serveTreeFile(tf, path, headers, out, headOnly);
                return;
            }
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

    // ------------------------------------------------- 离线课件文件夹（SAF）

    /** 选定文件夹里的一个文件 */
    private static final class TreeFile {
        final Uri uri;
        final long size;
        final String name;

        TreeFile(Uri uri, long size, String name) {
            this.uri = uri;
            this.size = size;
            this.name = name;
        }
    }

    /**
     * 在选定的文件夹里按相对路径找文件。
     * 家长可能选的是「含 videos 的那一层」，也可能直接选了 videos 文件夹，两种都试。
     */
    private TreeFile statInTree(String rel) {
        if (rel == null || rel.length() == 0) return null;
        TreeFile cached = treeCache.get(rel);
        if (cached != null) return cached;
        TreeFile f = statRel(rel);
        if (f == null) {
            if (rel.startsWith("videos/")) {
                f = statRel(rel.substring(7));
            } else if (rel.startsWith("workbooks/")) {
                f = statRel(rel.substring(10));
            }
        }
        // 有些文件管理器（下载、网盘类）的文档 id 不是路径，只能逐层按名字找
        if (f == null) f = walkTree(rel);
        if (f == null && rel.startsWith("videos/")) f = walkTree(rel.substring(7));
        if (f == null && rel.startsWith("workbooks/")) f = walkTree(rel.substring(10));
        if (f != null) {
            if (treeCache.size() > 800) treeCache.clear();
            treeCache.put(rel, f);
        }
        return f;
    }

    /** 逐层按名字查找（videos/u01/001.mp4 最多三层），兼容各种文档提供方 */
    private TreeFile walkTree(String rel) {
        ContentResolver cr = resolver;
        Uri tree = treeUri;
        if (cr == null || tree == null) return null;
        try {
            String parentId = DocumentsContract.getTreeDocumentId(tree);
            if (parentId == null) return null;
            String[] parts = rel.split("/");
            for (int i = 0; i < parts.length; i++) {
                boolean lastPart = (i == parts.length - 1);
                Uri children = DocumentsContract.buildChildDocumentsUriUsingTree(tree, parentId);
                Cursor c = cr.query(children, new String[]{
                        DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                        OpenableColumns.DISPLAY_NAME,
                        OpenableColumns.SIZE}, null, null, null);
                if (c == null) return null;
                String hitId = null;
                long hitSize = -1L;
                try {
                    while (c.moveToNext()) {
                        String id = c.getString(0);
                        String name = c.getString(1);
                        if (name != null && name.equals(parts[i])) {
                            hitId = id;
                            hitSize = c.isNull(2) ? -1L : c.getLong(2);
                            break;
                        }
                    }
                } finally {
                    c.close();
                }
                if (hitId == null) return null;
                if (lastPart) {
                    Uri doc = DocumentsContract.buildDocumentUriUsingTree(tree, hitId);
                    return new TreeFile(doc, hitSize, parts[i]);
                }
                parentId = hitId;
            }
            return null;
        } catch (Exception e) {
            return null;
        }
    }

    private TreeFile statRel(String rel) {
        ContentResolver cr = resolver;
        Uri tree = treeUri;
        if (cr == null || tree == null) return null;
        try {
            String docId = DocumentsContract.getTreeDocumentId(tree);
            if (docId == null) return null;
            String childId = docId;
            if (!childId.endsWith("/")) childId = childId + "/";
            childId = childId + rel;
            Uri doc = DocumentsContract.buildDocumentUriUsingTree(tree, childId);
            long size = -1L;
            String name = null;
            Cursor c = cr.query(doc,
                    new String[]{OpenableColumns.SIZE, OpenableColumns.DISPLAY_NAME},
                    null, null, null);
            if (c != null) {
                try {
                    if (c.moveToFirst()) {
                        if (!c.isNull(0)) size = c.getLong(0);
                        if (!c.isNull(1)) name = c.getString(1);
                    }
                } finally {
                    c.close();
                }
            }
            if (size < 0) return null;
            return new TreeFile(doc, size, name);
        } catch (Exception e) {
            return null;
        }
    }

    /** 从选定文件夹里读文件，支持 Range（拖进度条、边下边播都靠它） */
    private void serveTreeFile(TreeFile tf, String path, Map<String, String> headers,
                               OutputStream out, boolean headOnly) throws IOException {
        long len = tf.size;
        String mime = mimeOf(tf.name != null ? tf.name : path);
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
        long want = (len == 0) ? 0 : (end - start + 1);
        Map<String, String> h = new LinkedHashMap<String, String>();
        h.put("Content-Type", mime);
        h.put("Accept-Ranges", "bytes");
        h.put("Content-Length", String.valueOf(want));
        h.put("Cache-Control", "no-cache");
        if (partial) h.put("Content-Range", "bytes " + start + "-" + end + "/" + len);
        writeHead(out, partial ? "206 Partial Content" : "200 OK", h);
        if (headOnly || want == 0) {
            safeFlush(out);
            return;
        }

        InputStream is = null;
        try {
            ContentResolver cr = resolver;
            if (cr == null) throw new IOException("no resolver");
            is = cr.openInputStream(tf.uri);
            if (is == null) throw new IOException("open failed");
            byte[] buf = new byte[64 * 1024];
            long dropped = 0;
            while (dropped < start) {
                long s = is.skip(start - dropped);
                if (s > 0) {
                    dropped += s;
                    continue;
                }
                int n = is.read(buf, 0, (int) Math.min(buf.length, start - dropped));
                if (n < 0) break;
                dropped += n;
            }
            long sent = 0;
            while (sent < want) {
                int n = is.read(buf, 0, (int) Math.min(buf.length, want - sent));
                if (n < 0) break;
                out.write(buf, 0, n);
                sent += n;
            }
        } finally {
            if (is != null) {
                try {
                    is.close();
                } catch (IOException ignored) {
                }
            }
        }
        safeFlush(out);
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

    /** 公益镜像在墙内通常比 GitHub 直连稳，启动后测速挑最快的排最前，其次按近期失败次数排 */
    private String[] orderedBases() {
        final String best = fastestBase;
        List<String> list = new ArrayList<String>();
        for (int i = 0; i < REMOTE_BASES.length; i++) list.add(REMOTE_BASES[i]);
        java.util.Collections.sort(list, new java.util.Comparator<String>() {
            @Override
            public int compare(String a, String b) {
                boolean ba = a.equals(best), bb = b.equals(best);
                if (ba != bb) return ba ? -1 : 1;
                int fa = failsOf(a), fb = failsOf(b);
                if (fa != fb) return fa - fb;
                return 0;
            }
        });
        return list.toArray(new String[list.size()]);
    }

    /** 小图（练习册页、海报）：按近期成功率挑顺序，CDN 优先 */
    private String[] orderedPageBases() {
        List<String> list = new ArrayList<String>();
        for (int i = 0; i < PAGE_BASES.length; i++) list.add(PAGE_BASES[i]);
        java.util.Collections.sort(list, new java.util.Comparator<String>() {
            @Override
            public int compare(String a, String b) {
                int fa = failsOf(a), fb = failsOf(b);
                if (fa != fb) return fa - fb;
                return 0;
            }
        });
        return list.toArray(new String[list.size()]);
    }

    private int failsOf(String base) {
        Integer v = baseFails.get(base);
        return v == null ? 0 : v.intValue();
    }

    private void markFail(String base) {
        baseFails.put(base, Integer.valueOf(Math.min(9, failsOf(base) + 1)));
        if (base.equals(fastestBase) && failsOf(base) >= 2) {
            // 曾经最快的源连续失败，让位给别人，下次成功再回来
            fastestBase = null;
        }
    }

    private void markOk(String base) {
        if (failsOf(base) > 0) baseFails.put(base, Integer.valueOf(0));
        if (!base.equals(fastestBase)) {
            fastestBase = base;
            saveFastestBase(base);
        }
    }

    /** 取每源各 128KB 粗测速，几秒内定出首选源 */
    public void probeAsync() {
        if (probed) return;
        probed = true;
        Thread t = new Thread(new Runnable() {
            @Override
            public void run() {
                // 用两集各测一次，避免只预热到某一集造成的误判
                String[] probePaths = new String[]{"videos/u01/001.mp4", "videos/u02/001.mp4"};
                String best = null;
                long bestSpeed = 0;
                for (int i = 0; i < REMOTE_BASES.length; i++) {
                    long total = 0;
                    for (int k = 0; k < probePaths.length; k++) {
                        HttpURLConnection conn = null;
                        try {
                            conn = (HttpURLConnection) new URL(REMOTE_BASES[i] + probePaths[k]).openConnection();
                            conn.setConnectTimeout(6000);
                            conn.setReadTimeout(6000);
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
                            if (got > 0) total += got * 1000L / dt;
                            try { is.close(); } catch (IOException ignored) { }
                            conn.disconnect();
                        } catch (Exception e) {
                            if (conn != null) conn.disconnect();
                        }
                    }
                    if (total > bestSpeed) {
                        bestSpeed = total;
                        best = REMOTE_BASES[i];
                    }
                }
                if (best != null && !best.equals(fastestBase)) {
                    fastestBase = best;
                    saveFastestBase(best);
                }
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
     * 云端点播：把 WebView 的 Range 请求原样转发给云端，边收边吐。
     * 首包慢是公益代理的常态，所以给足超时并允许换源重试；
     * 同时限制回源并发，避免多路请求互相抢带宽把每一路都拖死。
     */
    private void serveRemoteVideo(String path, Map<String, String> headers,
                                  OutputStream out, boolean headOnly) {
        File target = cacheFileOf(path);
        // 练习册页/海报是小整图：边发边存，不再额外开一路后台下载（省一半流量）
        boolean page = path.startsWith("/workbooks/") || path.startsWith("/posters/");
        if (!page && target != null && !target.isFile()) {
            startBackgroundDownload(path, target);
        }

        String range = headers.get("range");
        Exception last = null;
        boolean started = false;   // 已经开始往客户端吐数据后，就不再换源（换了也会错乱）
        boolean gateHeld = false;
        String usedBase = null;

        try {
            // 闸门：最多 4 路并发回源，拿不到就排队（图片请求会插队完成）
            try {
                gateHeld = remoteGate.tryAcquire(35, TimeUnit.SECONDS);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
            if (!gateHeld) {
                try {
                    sendText(out, 503, "Busy", "text/plain; charset=utf-8",
                            "网络有点忙，稍等一下再点这一课");
                } catch (IOException ignored) {
                }
                return;
            }

            long deadline = System.currentTimeMillis() + FIRST_BYTE_BUDGET;
            // 小图走 CDN 源，视频走 gh 代理源
            // 两轮：第一轮按健康度试所有源，第二轮再给主力源一次机会（首包抖动很常见）
            for (int round = 0; round < 2 && !started; round++) {
                String[] bases = page ? orderedPageBases() : orderedBases();
                for (int i = 0; i < bases.length; i++) {
                    long remain = deadline - System.currentTimeMillis();
                    if (started) break;
                    if (remain < 4000L) break;
                    // 第二轮只重试排在前面的两个源，避免用户等太久
                    if (round > 0 && i >= 2) break;

                    HttpURLConnection conn = null;
                    try {
                        conn = (HttpURLConnection) new URL(bases[i] + path.substring(1)).openConnection();
                        conn.setConnectTimeout((int) Math.min(CONNECT_TIMEOUT, Math.max(6000L, remain)));
                        conn.setReadTimeout(READ_TIMEOUT);
                        conn.setInstanceFollowRedirects(true);
                        conn.setRequestProperty("User-Agent", "StudyDaily/1.0");
                        conn.setRequestProperty("Accept-Encoding", "identity");
                        if (range != null) conn.setRequestProperty("Range", range);

                        int code = conn.getResponseCode();
                        if (code != 200 && code != 206) {
                            last = new IOException("HTTP " + code);
                            markFail(bases[i]);
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
                        started = true;
                        usedBase = bases[i];
                        markOk(bases[i]);
                        if (headOnly) {
                            safeFlush(out);
                            conn.disconnect();
                            return;
                        }
                        activeStreams.incrementAndGet();
                        File part = null;
                        FileOutputStream cos = null;
                        boolean cacheInline = page && code == 200 && target != null;
                        try {
                            InputStream is = conn.getInputStream();
                            if (cacheInline) {
                                try {
                                    File parent = target.getParentFile();
                                    if (parent != null) parent.mkdirs();
                                    part = new File(target.getPath() + ".part");
                                    cos = new FileOutputStream(part);
                                } catch (Exception e) {
                                    part = null;
                                    cos = null;
                                }
                            }
                            OutputStream bout = new java.io.BufferedOutputStream(out, 64 * 1024);
                            try {
                                byte[] buf = new byte[64 * 1024];
                                int n;
                                while ((n = is.read(buf)) > 0) {
                                    bout.write(buf, 0, n);
                                    if (cos != null) cos.write(buf, 0, n);
                                }
                                bout.flush();
                            } finally {
                                try { is.close(); } catch (IOException ignored) { }
                            }
                        } finally {
                            if (cos != null) {
                                try { cos.close(); } catch (IOException ignored) { }
                            }
                            if (part != null) {
                                if (part.length() > 1024) {
                                    if (target.exists()) target.delete();
                                    part.renameTo(target);
                                    evictIfNeeded();
                                } else {
                                    part.delete();
                                }
                            }
                            activeStreams.decrementAndGet();
                            conn.disconnect();
                        }
                        safeFlush(out);
                        return;
                    } catch (Exception e) {
                        last = e;
                        if (conn != null) conn.disconnect();
                        if (started) {
                            // 已经在播了，只是这一路断了，别再换源重来
                            return;
                        }
                        markFail(bases[i]);
                    }
                }
            }
            // 所有源都失败：多半是没联网，且这集还没缓存
            try {
                sendText(out, 504, "Gateway Timeout", "text/plain; charset=utf-8",
                        "这一集还没存到本机，需要连接网络才能看" + (last == null ? "" : "（" + last.getMessage() + "）"));
            } catch (IOException ignored) {
            }
        } finally {
            if (gateHeld) remoteGate.release();
        }
    }

    /**
     * 后台整集下载：串行队列（一次只下一集）、限速、且有播放时自动让路。
     * 之前是每开一集就并行拉整集，几集连点就把代理带宽吃光，后面的课全部超时。
     */
    private void startBackgroundDownload(final String path, final File target) {
        if (cacheDir == null) return;
        if (!downloading.add(path)) return;
        try {
            downloadPool.execute(new Runnable() {
                @Override
                public void run() {
                    try {
                        downloadOne(path, target);
                    } finally {
                        downloading.remove(path);
                    }
                }
            });
        } catch (RuntimeException e) {
            downloading.remove(path);
        }
    }

    private void downloadOne(String path, File target) {
        File parent = target.getParentFile();
        if (parent != null) parent.mkdirs();
        File part = new File(target.getPath() + ".part");
        String[] bases = orderedBases();
        for (int i = 0; i < bases.length; i++) {
            HttpURLConnection conn = null;
            OutputStream os = null;
            try {
                conn = (HttpURLConnection) new URL(bases[i] + path.substring(1)).openConnection();
                conn.setConnectTimeout(CONNECT_TIMEOUT);
                conn.setReadTimeout(READ_TIMEOUT);
                conn.setInstanceFollowRedirects(true);
                conn.setRequestProperty("User-Agent", "StudyDaily/1.0");
                conn.setRequestProperty("Accept-Encoding", "identity");
                if (conn.getResponseCode() != 200) {
                    conn.disconnect();
                    markFail(bases[i]);
                    continue;
                }
                os = new FileOutputStream(part);
                InputStream is = conn.getInputStream();
                byte[] buf = new byte[32 * 1024];
                int n;
                long total = 0;
                boolean tooBig = false;
                long t0 = System.currentTimeMillis();
                long gaveWay = 0;
                while ((n = is.read(buf)) > 0) {
                    // 有人在看视频就让路，最多让 2 分钟，免得连接被代理掐掉
                    while (activeStreams.get() > 0 && gaveWay < 120000L) {
                        try {
                            Thread.sleep(500);
                        } catch (InterruptedException ie) {
                            Thread.currentThread().interrupt();
                            break;
                        }
                        gaveWay += 500;
                    }
                    os.write(buf, 0, n);
                    total += n;
                    if (total > SINGLE_FILE_LIMIT) { tooBig = true; break; }
                    // 限速：别和播放抢带宽
                    long expect = total * 1000L / BG_DOWNLOAD_BPS;
                    long spent = System.currentTimeMillis() - t0;
                    if (expect > spent) {
                        try {
                            Thread.sleep(Math.min(1000L, expect - spent));
                        } catch (InterruptedException ie) {
                            Thread.currentThread().interrupt();
                            break;
                        }
                    }
                }
                try { is.close(); } catch (IOException ignored) { }
                try { os.close(); } catch (IOException ignored) { }
                os = null;
                conn.disconnect();
                if (tooBig) { part.delete(); return; }
                if (part.length() > 4096) {
                    if (target.exists()) target.delete();
                    part.renameTo(target);
                    evictIfNeeded();
                    markOk(bases[i]);
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
