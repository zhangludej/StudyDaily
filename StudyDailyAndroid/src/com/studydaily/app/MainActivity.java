package com.studydaily.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.pm.ActivityInfo;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.Settings;
import android.util.Log;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.webkit.ConsoleMessage;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.Toast;

import java.io.File;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

/**
 * 每日学习 · 安卓壳
 * 启动内置 HTTP 服务 -> WebView 打开 127.0.0.1 -> 进儿童模式界面。
 * 视频不进 APK，直接读平板上的「内部存储/StudyDaily」目录。
 */
public class MainActivity extends Activity {

    private static final String TAG = "StudyDaily";
    private static final int REQ_STORAGE = 1001;

    private WebView webView;
    private MiniServer server;
    private FrameLayout rootLayout;
    private View customView;
    private WebChromeClient.CustomViewCallback customViewCallback;
    private long lastBackAt = 0L;
    private boolean pageLoaded = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        getWindow().getDecorView().setBackgroundColor(Color.parseColor("#FFF8F0"));

        rootLayout = new FrameLayout(this);
        rootLayout.setBackgroundColor(Color.parseColor("#FFF8F0"));
        setContentView(rootLayout);

        webView = new WebView(this);
        rootLayout.addView(webView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        setupWebView();
        goImmersive();

        File progress = new File(getFilesDir(), "progress.json");
        server = new MiniServer(getAssets(), progress);
        // 云端点播缓存：应用私有外部目录，免存储权限，卸载即清
        try {
            File cacheBase = getExternalFilesDir(null);
            if (cacheBase == null) cacheBase = getFilesDir();
            server.setCacheDir(new File(cacheBase, "cw"));
        } catch (Throwable ignored) {
        }
        startAndLoad();
    }

    // ------------------------------------------------------------ 目录与权限

    /** 依次尝试：内部存储/StudyDaily、/sdcard/StudyDaily、应用私有目录 */
    private List<File> candidateRoots() {
        List<File> list = new ArrayList<File>();
        try {
            File ext = Environment.getExternalStorageDirectory();
            if (ext != null) {
                list.add(new File(ext, "StudyDaily"));
                File alt = new File(ext, "studydaily");
                list.add(alt);
            }
        } catch (Exception ignored) {
        }
        list.add(new File("/sdcard/StudyDaily"));
        try {
            File priv = getExternalFilesDir(null);
            if (priv != null) list.add(new File(priv, "StudyDaily"));
        } catch (Exception ignored) {
        }
        return list;
    }

    private File resolveRoot() {
        List<File> cands = candidateRoots();
        for (File f : cands) {
            if (new File(f, "courses.json").isFile()) return f;
        }
        for (File f : cands) {
            if (f.isDirectory()) return f;
        }
        return null;
    }

    private boolean hasStorageAccess() {
        if (Build.VERSION.SDK_INT >= 30) {
            try {
                return Environment.isExternalStorageManager();
            } catch (Throwable t) {
                return false;
            }
        }
        // Android 7–10：READ_EXTERNAL_STORAGE 是运行时权限
        return checkSelfPermission(android.Manifest.permission.READ_EXTERNAL_STORAGE)
                == android.content.pm.PackageManager.PERMISSION_GRANTED;
    }

    private void requestRuntimeStorage() {
        if (Build.VERSION.SDK_INT >= 23 && Build.VERSION.SDK_INT < 30) {
            requestPermissions(new String[]{
                    android.Manifest.permission.READ_EXTERNAL_STORAGE,
                    android.Manifest.permission.WRITE_EXTERNAL_STORAGE}, REQ_STORAGE);
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQ_STORAGE) {
            startAndLoad();
        }
    }

    private void requestStorageAccess() {
        if (Build.VERSION.SDK_INT >= 30) {
            try {
                Intent i = new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION);
                i.setData(Uri.parse("package:" + getPackageName()));
                startActivity(i);
                return;
            } catch (Exception e) {
                try {
                    startActivity(new Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION));
                    return;
                } catch (Exception ignored) {
                }
            }
        }
        Toast.makeText(this, "请在系统设置中允许本应用访问存储", Toast.LENGTH_LONG).show();
    }

    // ------------------------------------------------------------ 启动服务

    private void startAndLoad() {
        File root = resolveRoot();
        server.setRoot(root);
        if (root == null) {
            Log.w(TAG, "未找到课程目录");
        } else {
            Log.i(TAG, "课程目录: " + root.getAbsolutePath());
        }

        if (!server.isRunning()) {
            try {
                server.start();
            } catch (IOException e) {
                Log.e(TAG, "本地服务启动失败", e);
                new AlertDialog.Builder(this)
                        .setTitle("启动失败")
                        .setMessage("本地服务无法启动：" + e.getMessage())
                        .setPositiveButton("退出", new DialogInterface.OnClickListener() {
                            @Override
                            public void onClick(DialogInterface d, int w) {
                                finish();
                            }
                        })
                        .show();
                return;
            }
        }

        String url = "http://127.0.0.1:" + server.getPort() + "/";
        if (!pageLoaded) {
            webView.loadUrl(url);
            pageLoaded = true;
        } else {
            webView.reload();
        }

        if (root == null) {
            // 没有本地课件目录也没关系：课程表在 APK 内置，视频按需从云端点播
            Log.i(TAG, "无本地课程目录，使用内置课程表 + 云端课件");
        }
        // 不再强制申请「访问所有文件」：视频边看边缓存到应用私有目录，免权限
    }

    private void showMissingFolderDialog() {
        String path = Environment.getExternalStorageDirectory().getAbsolutePath() + "/StudyDaily";
        new AlertDialog.Builder(this)
                .setTitle("没找到课程文件夹")
                .setMessage("请把电脑上的 StudyDaily 文件夹整个拷到平板，路径应为：\n\n"
                        + path + "\n\n"
                        + "拷好后点「重新检查」。也可以直接从这里打开设置授予存储权限。")
                .setPositiveButton("重新检查", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface d, int w) {
                        startAndLoad();
                    }
                })
                .setNeutralButton("授予存储权限", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface d, int w) {
                        requestStorageAccess();
                    }
                })
                .setNegativeButton("先看看界面", null)
                .show();
    }

    private void showPermissionDialog() {
        new AlertDialog.Builder(this)
                .setTitle("需要读取课件")
                .setMessage("为了播放平板上的课程视频，需要允许「访问所有文件」。\n\n"
                        + "这是系统的一次性授权，只读取 StudyDaily 文件夹里的课件，不会上传任何内容。")
                .setPositiveButton("去授权", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface d, int w) {
                        requestStorageAccess();
                    }
                })
                .setNegativeButton("稍后", null)
                .show();
    }

    // ------------------------------------------------------------ WebView

    private void setupWebView() {
        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        s.setLoadsImagesAutomatically(true);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setSupportZoom(false);
        s.setBuiltInZoomControls(false);
        s.setTextZoom(100);
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);
        if (Build.VERSION.SDK_INT >= 21) {
            s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        }

        webView.setBackgroundColor(Color.parseColor("#FFF8F0"));
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                if (url != null && url.startsWith("http://127.0.0.1")) return false;
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
                } catch (Exception ignored) {
                }
                return true;
            }
        });
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onShowCustomView(View view, CustomViewCallback callback) {
                if (customView != null) {
                    callback.onCustomViewHidden();
                    return;
                }
                customView = view;
                customViewCallback = callback;
                rootLayout.addView(customView, new FrameLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
                webView.setVisibility(View.GONE);
                goImmersive();
            }

            @Override
            public void onHideCustomView() {
                if (customView == null) return;
                rootLayout.removeView(customView);
                customView = null;
                webView.setVisibility(View.VISIBLE);
                if (customViewCallback != null) {
                    customViewCallback.onCustomViewHidden();
                    customViewCallback = null;
                }
                goImmersive();
            }

            @Override
            public boolean onConsoleMessage(ConsoleMessage cm) {
                Log.d(TAG, "console: " + cm.message());
                return true;
            }
        });
    }

    private void goImmersive() {
        View decor = getWindow().getDecorView();
        decor.setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                        | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY);
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus && customView == null) goImmersive();
    }

    // ------------------------------------------------------------ 生命周期

    @Override
    protected void onResume() {
        super.onResume();
        goImmersive();
        // 从系统设置授权回来后，重新解析目录并刷新页面
        if (server != null && server.isRunning()) {
            File now = resolveRoot();
            File cur = server.getRoot();
            String nowPath = now == null ? "" : now.getAbsolutePath();
            String curPath = cur == null ? "" : cur.getAbsolutePath();
            if (!nowPath.equals(curPath)) {
                server.setRoot(now);
                if (now != null) webView.reload();
            }
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        if (server != null) server.stop();
        if (webView != null) {
            try {
                webView.destroy();
            } catch (Exception ignored) {
            }
        }
        super.onDestroy();
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK) {
            if (customView != null) {
                // 全屏播放时先退出全屏
                if (customViewCallback != null) customViewCallback.onCustomViewHidden();
                return true;
            }
            long now = System.currentTimeMillis();
            if (now - lastBackAt < 2000) {
                finish();
                return true;
            }
            lastBackAt = now;
            Toast.makeText(this, "再按一次返回键退出", Toast.LENGTH_SHORT).show();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }
}
