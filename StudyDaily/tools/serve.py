#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
StudyDaily - 支持 HTTP Range 的本地服务器

浏览器播放本地 mp4 必须走 HTTP（file:// 会被 Chrome 拒绝），且拖动进度条
依赖 206 Partial Content；Python 自带的 http.server 不支持 Range，这里补上。
同时提供 /api/progress 让学习进度自动落盘，避免清浏览器数据后丢失。

    python tools/serve.py                     # 默认 127.0.0.1:8777
    python tools/serve.py --port 8080
    python tools/serve.py --host 0.0.0.0      # 允许局域网内其它设备访问

浏览器打开 http://127.0.0.1:8777/ 即可。
平板端若改用现成的「本地 HTTP 服务器」App 指向本目录，进度接口会 404，
应用会自动降级为浏览器本地存储 + 手动导出备份（功能不受影响）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROGRESS_FILE = ROOT / "progress.json"


class Handler(SimpleHTTPRequestHandler):
    server_version = "StudyDaily/1.0"

    MIME = {
        ".js": "text/javascript; charset=utf-8",
        ".mjs": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".webmanifest": "application/manifest+json; charset=utf-8",
        ".mp4": "video/mp4",
        ".m4v": "video/mp4",
        ".png": "image/png",
        ".svg": "image/svg+xml",
        ".ico": "image/x-icon",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def guess_type(self, path):
        ext = os.path.splitext(str(path))[1].lower()
        return self.MIME.get(ext) or super().guess_type(path)

    def log_message(self, fmt, *args):
        # 视频 Range 请求很频繁，只打印精简信息
        if "/api/" in (self.path or ""):
            super().log_message(fmt, *args)

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = (self.path or "").split("?", 1)[0]
        if path == "/api/ping":
            return self._json({"ok": True, "server": "StudyDaily", "progressApi": True})
        if path == "/api/progress":
            try:
                return self._json(json.loads(PROGRESS_FILE.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                return self._json({})
        return super().do_GET()

    def do_POST(self):
        path = (self.path or "").split("?", 1)[0]
        if path != "/api/progress":
            return self._json({"ok": False, "error": "not found"}, 404)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n > 0 else b"{}"
            data = json.loads(raw.decode("utf-8"))
            tmp = PROGRESS_FILE.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, PROGRESS_FILE)
            return self._json({"ok": True})
        except Exception as e:
            return self._json({"ok": False, "error": str(e)}, 500)

    def end_headers(self):
        # 课程清单和进度不缓存，改完刷新就生效；视频照常缓存
        p = (self.path or "").split("?", 1)[0].lower()
        if p.endswith(".json") or p.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def send_head(self):
        """在父类基础上支持 Range -> 206 Partial Content"""
        rng = self.headers.get("Range")
        path = self.translate_path(self.path)
        if not rng or os.path.isdir(path):
            return super().send_head()

        m = re.match(r"bytes=(\d*)-(\d*)\s*$", rng.strip())
        if not m:
            return super().send_head()
        try:
            f = open(path, "rb")
        except OSError:
            return super().send_head()

        fs = os.fstat(f.fileno())
        size = fs[stat.ST_SIZE]
        start_s, end_s = m.group(1), m.group(2)
        try:
            if start_s == "" and end_s:
                length = int(end_s)
                start, end = max(0, size - length), size - 1
            else:
                start = int(start_s or 0)
                end = min(int(end_s), size - 1) if end_s else size - 1
        except ValueError:
            f.close()
            return super().send_head()

        if start > end or start >= size or size == 0:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            f.close()
            return None

        length = end - start + 1
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self.send_header("Last-Modified", self.date_time_string(fs.st_mtime))
        self.end_headers()
        f.seek(start)
        self._range_remaining = length
        return f

    def copyfile(self, source, outputfile):
        """206 响应只发送请求的字节区间，不能把整个文件写出去"""
        remaining = getattr(self, "_range_remaining", None)
        if remaining is None:
            return super().copyfile(source, outputfile)
        del self._range_remaining
        try:
            while remaining > 0:
                chunk = source.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                outputfile.write(chunk)
                remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="StudyDaily 本地服务器")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8777)
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"StudyDaily 已启动：http://{args.host}:{args.port}/")
    print(f"服务目录：{ROOT}")
    print("平板/电脑浏览器打开上面地址；Ctrl+C 停止。")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
