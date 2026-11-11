# -*- coding: utf-8 -*-
"""
每日学习 · 桌面版本地服务

设计要点：
  1. 只监听 127.0.0.1，不对外暴露，不需要管理员权限，不写注册表
  2. 练习册、海报、语音全部内置，完全离线可用；视频体积太大，走云端点播
  3. 视频边看边存到本机缓存，看过的课以后没网也能看
  4. 若 exe 同目录有 videos/ 或 课件/ 文件夹，优先读本地，彻底离线
  5. 后台缓存一次只下一集、限速、且有人在看视频时自动让路
"""

import json
import os
import sys
import threading
import time
import queue
import subprocess
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
import urllib.request
import urllib.error

APP_NAME = "StudyDaily"

REMOTE_BASES = [
    "https://gh-proxy.com/https://raw.githubusercontent.com/zhangludej/StudyDaily-courseware/main/",
    "https://ghfast.top/https://raw.githubusercontent.com/zhangludej/StudyDaily-courseware/main/",
    "https://raw.githubusercontent.com/zhangludej/StudyDaily-courseware/main/",
]

# 练习册页、海报这类几十 KB 的小图：CDN 命中比 github 代理快
PAGE_BASES = [
    "https://cdn.jsdelivr.net/gh/zhangludej/StudyDaily-courseware@main/",
    "https://gh-proxy.com/https://raw.githubusercontent.com/zhangludej/StudyDaily-courseware/main/",
    "https://gcore.jsdelivr.net/gh/zhangludej/StudyDaily-courseware@main/",
    "https://raw.githubusercontent.com/zhangludej/StudyDaily-courseware/main/",
]


def bases_for(rel: str) -> list:
    """小图走 CDN，视频走 gh 代理"""
    if rel.startswith("workbooks/") or rel.startswith("posters/"):
        return PAGE_BASES
    return REMOTE_BASES

CONNECT_TIMEOUT = 20
READ_TIMEOUT = 60
FIRST_BYTE_BUDGET = 50          # 拿到首字节的总预算（秒）
MAX_REMOTE_STREAMS = 6          # 云端回源并发上限
CACHE_LIMIT = 2 * 1024 ** 3     # 2GB
SINGLE_FILE_LIMIT = 260 * 1024 ** 2
BG_BPS = 200 * 1024             # 后台缓存限速
CHUNK = 64 * 1024
USER_AGENT = "StudyDaily-PC/1.0"


# ---------------------------------------------------------------- 路径

def base_dir() -> Path:
    """打包后资源解压目录；开发时是脚本所在目录"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = Path(root) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


WEB_DIR = base_dir() / "web"
DATA_DIR = data_dir()
CACHE_DIR = DATA_DIR / "cache"
PROGRESS_FILE = DATA_DIR / "progress.json"
NETBEST_FILE = DATA_DIR / "netbest.txt"

# 本地课件目录：exe 旁边的 课件/ 或 根目录下的 videos/
def find_local_course() -> Path | None:
    for cand in (exe_dir() / "课件", exe_dir() / "courseware", exe_dir()):
        try:
            if (cand / "videos").is_dir() or (cand / "workbooks").is_dir():
                return cand
        except OSError:
            pass
    return None


LOCAL_COURSE = find_local_course()


# ---------------------------------------------------------------- 运行状态

_streams = 0
_streams_lock = threading.Lock()
gate = threading.Semaphore(MAX_REMOTE_STREAMS)
fails: dict[str, int] = {}
fails_lock = threading.Lock()
fastest: str | None = None
fastest_lock = threading.Lock()
downloading: set[str] = set()
downloading_lock = threading.Lock()
dl_queue: "queue.Queue[tuple[str, Path]]" = queue.Queue()


def load_fastest() -> None:
    global fastest
    try:
        if NETBEST_FILE.is_file():
            v = NETBEST_FILE.read_text(encoding="utf-8").strip()
            if v in REMOTE_BASES:
                fastest = v
    except OSError:
        pass


def save_fastest(base: str) -> None:
    try:
        NETBEST_FILE.write_text(base, encoding="utf-8")
    except OSError:
        pass


def fails_of(base: str) -> int:
    return fails.get(base, 0)


def mark_fail(base: str) -> None:
    global fastest
    with fails_lock:
        fails[base] = min(9, fails.get(base, 0) + 1)
        if fastest == base and fails[base] >= 2:
            fastest = None


def mark_ok(base: str) -> None:
    global fastest
    with fails_lock:
        fails[base] = 0
        if fastest != base:
            fastest = base
            save_fastest(base)


def ordered_bases(rel: str = "") -> list[str]:
    pool = bases_for(rel)
    if pool is PAGE_BASES:
        return sorted(pool, key=fails_of)
    best = fastest
    return sorted(pool,
                  key=lambda b: (0 if b == best else 1, fails_of(b)))


def inc_stream() -> None:
    global _streams
    with _streams_lock:
        _streams += 1


def dec_stream() -> None:
    global _streams
    with _streams_lock:
        if _streams > 0:
            _streams -= 1


def active_streams() -> int:
    with _streams_lock:
        return _streams


# ---------------------------------------------------------------- 测速

def probe_sources() -> None:
    """各源取 128KB 粗测，定出最快的源并记下来，下次启动直接用"""
    global fastest
    paths = ["videos/u01/001.mp4", "videos/u02/001.mp4"]
    best, best_speed = None, 0
    for base in REMOTE_BASES:
        total = 0
        for p in paths:
            req = urllib.request.Request(base + p, headers={
                "User-Agent": USER_AGENT, "Range": "bytes=0-131071"})
            try:
                t0 = time.time()
                with urllib.request.urlopen(req, timeout=6) as r:
                    got = 0
                    while got < 131072:
                        buf = r.read(16384)
                        if not buf:
                            break
                        got += len(buf)
                dt = max(0.001, time.time() - t0)
                if got > 0:
                    total += got / dt
            except Exception:
                continue
        if total > best_speed:
            best_speed, best = total, base
    if best:
        with fastest_lock:
            if fastest != best:
                fastest = best
                save_fastest(best)


# ---------------------------------------------------------------- 后台缓存下载

def cache_path_of(rel: str) -> Path | None:
    if ".." in rel:
        return None
    return CACHE_DIR / rel


def enqueue_download(rel: str, target: Path) -> None:
    with downloading_lock:
        if rel in downloading:
            return
        downloading.add(rel)
    dl_queue.put((rel, target))


def download_worker() -> None:
    while True:
        rel, target = dl_queue.get()
        try:
            download_one(rel, target)
        except Exception:
            pass
        finally:
            with downloading_lock:
                downloading.discard(rel)
            dl_queue.task_done()


def download_one(rel: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    for base in ordered_bases(rel):
        try:
            req = urllib.request.Request(base + rel, headers={
                "User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
            with urllib.request.urlopen(req, timeout=CONNECT_TIMEOUT) as r:
                if r.status != 200:
                    mark_fail(base)
                    continue
                total, t0, gave_way = 0, time.time(), 0.0
                with open(part, "wb") as f:
                    while True:
                        # 有人在看视频就让路，最多让 2 分钟
                        while active_streams() > 0 and gave_way < 120:
                            time.sleep(0.5)
                            gave_way += 0.5
                        buf = r.read(32768)
                        if not buf:
                            break
                        f.write(buf)
                        total += len(buf)
                        if total > SINGLE_FILE_LIMIT:
                            raise ValueError("too big")
                        # 限速，别和播放抢带宽
                        expect = total / BG_BPS
                        spent = time.time() - t0
                        if expect > spent:
                            time.sleep(min(1.0, expect - spent))
            if part.stat().st_size > 4096:
                if target.exists():
                    target.unlink()
                part.replace(target)
                evict_if_needed()
                mark_ok(base)
            else:
                part.unlink(missing_ok=True)
            return
        except Exception:
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass
            mark_fail(base)


def evict_if_needed() -> None:
    """超过上限就按最久未看淘汰"""
    if not CACHE_DIR.is_dir():
        return
    files = [p for p in CACHE_DIR.rglob("*") if p.is_file() and not p.name.endswith(".part")]
    total = sum(p.stat().st_size for p in files)
    if total <= CACHE_LIMIT:
        return
    files.sort(key=lambda p: p.stat().st_mtime)
    for p in files:
        if total <= CACHE_LIMIT:
            break
        try:
            size = p.stat().st_size
            p.unlink()
            total -= size
        except OSError:
            pass


def cache_stats() -> dict:
    items, total = [], 0
    if CACHE_DIR.is_dir():
        for p in CACHE_DIR.rglob("*"):
            if p.is_file() and not p.name.endswith(".part"):
                size = p.stat().st_size
                total += size
                if len(items) < 200:
                    rel = str(p.relative_to(CACHE_DIR)).replace("\\", "/")
                    items.append({"id": rel, "mb": size // (1024 * 1024)})
    with downloading_lock:
        pending = len(downloading)
    return {"count": len(items), "bytes": total, "limit": CACHE_LIMIT,
            "downloading": pending, "items": items}


def clear_cache() -> None:
    if not CACHE_DIR.is_dir():
        return
    for p in sorted(CACHE_DIR.rglob("*"), key=lambda x: len(x.parts), reverse=True):
        try:
            if p.is_dir():
                p.rmdir()
            else:
                p.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- MIME

MIME = {
    ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp",
    ".ico": "image/x-icon", ".mp4": "video/mp4", ".m4v": "video/mp4",
    ".webm": "video/webm", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
    ".wav": "audio/wav", ".ogg": "audio/ogg", ".pdf": "application/pdf",
    ".txt": "text/plain; charset=utf-8", ".woff2": "font/woff2",
}


def mime_of(name: str) -> str:
    return MIME.get(Path(name).suffix.lower(), "application/octet-stream")


def parse_range(header: str, size: int):
    """bytes=start-end / bytes=start- / bytes=-suffix；越界返回 None"""
    try:
        spec = header.strip()
        if not spec.startswith("bytes="):
            return None
        spec = spec[6:].split(",")[0].strip()
        s, _, e = spec.partition("-")
        s, e = s.strip(), e.strip()
        if not s:
            if not e:
                return None
            suffix = int(e)
            if suffix <= 0:
                return None
            start, end = max(0, size - suffix), size - 1
        else:
            start = int(s)
            end = int(e) if e else size - 1
        start = max(0, start)
        end = min(end, size - 1)
        if start > end or start >= size:
            return None
        return start, end
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "StudyDaily"
    sys_version = ""

    # 安静一点，不往控制台刷日志
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        self.dispatch("GET")

    def do_HEAD(self):
        self.dispatch("HEAD")

    def do_POST(self):
        self.dispatch("POST")

    def dispatch(self, method: str):
        try:
            parts = urlsplit(self.path)
            path = unquote(parts.path)
            if path.startswith("/api/"):
                self.handle_api(method, path)
            elif method in ("GET", "HEAD"):
                self.serve_static(path, method == "HEAD")
            else:
                self.send_bytes(405, b"405", "text/plain; charset=utf-8")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception:
            try:
                self.send_bytes(500, b"500", "text/plain; charset=utf-8")
            except Exception:
                pass

    # ---------------- API

    def handle_api(self, method: str, path: str):
        if path == "/api/ping":
            return self.send_json({"ok": True, "server": "StudyDaily-PC",
                                   "progressApi": True,
                                   "local": LOCAL_COURSE is not None})
        if path == "/api/progress":
            if method == "GET":
                try:
                    body = PROGRESS_FILE.read_text(encoding="utf-8").strip() or "{}"
                except OSError:
                    body = "{}"
                return self.send_bytes(200, body.encode("utf-8"),
                                       "application/json; charset=utf-8", no_store=True)
            if method == "POST":
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 8 * 1024 * 1024:
                    return self.send_json({"ok": False}, 400)
                data = self.rfile.read(length)
                tmp = PROGRESS_FILE.with_suffix(".tmp")
                try:
                    tmp.write_bytes(data)
                    tmp.replace(PROGRESS_FILE)
                except OSError:
                    pass
                return self.send_json({"ok": True})
        if path == "/api/cache":
            if method == "GET":
                return self.send_json(cache_stats())
            if method == "POST":
                clear_cache()
                return self.send_json({"ok": True})
        if path == "/api/local":
            ready = LOCAL_COURSE is not None
            return self.send_json({"ok": True, "ready": ready,
                                   "dir": str(LOCAL_COURSE) if ready else ""})
        return self.send_json({"ok": False}, 404)

    # ---------------- 静态

    def serve_static(self, path: str, head_only: bool):
        if "\x00" in path or ".." in path:
            return self.send_bytes(403, b"403", "text/plain; charset=utf-8")
        rel = path.lstrip("/")
        if not rel:
            rel = "index.html"

        # 1) 内置网页资源优先：保证界面永远是随程序更新的那一份，
        #    不会被课件文件夹里的旧网页文件盖住
        f = WEB_DIR / rel
        if f.is_file():
            return self.serve_file(f, head_only)

        # 2) 本地课件目录（视频放这里就能彻底离线）
        if LOCAL_COURSE is not None:
            f = LOCAL_COURSE / rel
            if f.is_file():
                return self.serve_file(f, head_only)

        # 3) 缓存里看过的一份
        cached = cache_path_of(rel)
        if cached is not None and cached.is_file():
            try:
                cached.touch()
            except OSError:
                pass
            return self.serve_file(cached, head_only)

        # 4) 视频 / 练习册页本地没有，就走云端，边看边缓存
        if rel.startswith("videos/") or rel.startswith("workbooks/"):
            return self.serve_remote(rel, head_only)

        # 5) 单页应用兜底
        if "." not in rel:
            idx = WEB_DIR / "index.html"
            if idx.is_file():
                return self.serve_file(idx, head_only)

        return self.send_bytes(404, ("找不到文件：" + path).encode("utf-8"),
                               "text/plain; charset=utf-8")

    def serve_file(self, f: Path, head_only: bool, no_store: bool = False):
        try:
            size = f.stat().st_size
        except OSError:
            return self.send_bytes(404, b"404", "text/plain; charset=utf-8")
        mime = mime_of(f.name)
        start, end = 0, size - 1
        partial = False
        rng = self.headers.get("Range")
        if rng and size > 0:
            rp = parse_range(rng, size)
            if rp is None:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            start, end = rp
            partial = True
        length = 0 if size == 0 else end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", mime)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store" if no_store else "no-cache")
        if partial:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        if head_only or length == 0:
            return
        with open(f, "rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                buf = fh.read(min(CHUNK, remaining))
                if not buf:
                    break
                self.wfile.write(buf)
                remaining -= len(buf)

    def serve_remote(self, rel: str, head_only: bool):
        target = cache_path_of(rel)
        # 练习册页/海报是小整图：边发边存，不再额外排队下载一份（省一半流量）
        is_page = rel.startswith("workbooks/") or rel.startswith("posters/")
        if (not is_page) and target is not None and not target.is_file():
            enqueue_download(rel, target)

        rng = self.headers.get("Range")
        deadline = time.time() + FIRST_BYTE_BUDGET
        got_gate = gate.acquire(timeout=35)
        if not got_gate:
            return self.send_bytes(503, "网络有点忙，稍等一下再点这一课".encode("utf-8"),
                                   "text/plain; charset=utf-8")
        started = False
        last_err = None
        try:
            for rnd in range(2):
                bases = ordered_bases(rel)
                for i, base in enumerate(bases):
                    if started:
                        break
                    remain = deadline - time.time()
                    if remain < 4:
                        break
                    if rnd > 0 and i >= 2:
                        break
                    try:
                        headers = {"User-Agent": USER_AGENT,
                                   "Accept-Encoding": "identity"}
                        if rng:
                            headers["Range"] = rng
                        req = urllib.request.Request(base + rel, headers=headers)
                        resp = urllib.request.urlopen(req, timeout=CONNECT_TIMEOUT)
                        code = resp.status
                        if code not in (200, 206):
                            last_err = "HTTP %d" % code
                            mark_fail(base)
                            resp.close()
                            continue
                        ctype = resp.headers.get("Content-Type") or mime_of(rel)
                        clen = resp.headers.get("Content-Length")
                        crange = resp.headers.get("Content-Range")
                        self.send_response(206 if code == 206 else 200)
                        self.send_header("Content-Type", ctype)
                        self.send_header("Accept-Ranges", "bytes")
                        self.send_header("Cache-Control", "no-cache")
                        if clen:
                            self.send_header("Content-Length", clen)
                        else:
                            self.close_connection = True
                        if crange:
                            self.send_header("Content-Range", crange)
                        self.end_headers()
                        started = True
                        mark_ok(base)
                        if head_only:
                            resp.close()
                            return
                        inc_stream()
                        cos = None
                        part = None
                        if is_page and code == 200 and target is not None:
                            try:
                                target.parent.mkdir(parents=True, exist_ok=True)
                                part = target.with_suffix(target.suffix + ".part")
                                cos = open(part, "wb")
                            except OSError:
                                cos, part = None, None
                        try:
                            while True:
                                buf = resp.read(CHUNK)
                                if not buf:
                                    break
                                self.wfile.write(buf)
                                if cos is not None:
                                    cos.write(buf)
                        finally:
                            if cos is not None:
                                try:
                                    cos.close()
                                except OSError:
                                    pass
                            if part is not None and part.is_file():
                                try:
                                    if part.stat().st_size > 1024:
                                        part.replace(target)
                                        evict_if_needed()
                                    else:
                                        part.unlink()
                                except OSError:
                                    pass
                            dec_stream()
                            resp.close()
                        return
                    except Exception as e:
                        last_err = str(e)
                        if started:
                            return
                        mark_fail(base)
            msg = "这一集还没存到本机，需要连接网络才能看"
            if last_err:
                msg += "（%s）" % last_err
            self.send_bytes(504, msg.encode("utf-8"), "text/plain; charset=utf-8")
        finally:
            if got_gate:
                gate.release()

    # ---------------- 输出

    def send_json(self, obj, code: int = 200):
        self.send_bytes(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8", no_store=True)

    def send_bytes(self, code: int, body: bytes, mime: str, no_store: bool = False):
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if no_store else "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


# ---------------------------------------------------------------- 启动

def start_server() -> int:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    server = None
    port = 0
    for p in range(8720, 8761):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            port = p
            break
        except OSError:
            continue
    if server is None:
        raise RuntimeError("没有可用端口")
    server.daemon_threads = True
    load_fastest()
    # 纯离线（本地有视频）就不用测速，省得白跑一趟网络
    if LOCAL_COURSE is None:
        threading.Thread(target=probe_sources, daemon=True).start()
    threading.Thread(target=download_worker, daemon=True).start()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return port


def wait_ready(port: int, seconds: float = 6.0) -> bool:
    """确认服务真的能应答，再开窗口——否则 Edge 只会显示拒绝连接"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/ping" % port,
                                        timeout=1) as r:
                if json.loads(r.read().decode("utf-8")).get("server") == "StudyDaily-PC":
                    return True
        except Exception:
            time.sleep(0.1)
    return False


def setup_log() -> None:
    """打包成 windowed exe 后 stdout/stderr 都看不见了，落一份日志到数据目录"""
    try:
        f = open(DATA_DIR / "log.txt", "a", encoding="utf-8", buffering=1)
        sys.stdout = f
        sys.stderr = f
        print("\n==== %s 启动 pid=%d ====" % (datetime.now().strftime("%m-%d %H:%M:%S"), os.getpid()))
        print("exe:", sys.executable)
        print("data:", DATA_DIR, " local_course:", LOCAL_COURSE)
    except Exception:
        pass


def alert(msg: str) -> None:
    """启动失败时至少弹个窗，而不是让用户对着拒绝连接的页面发呆"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, "StudyDaily", 0x10)
    except Exception:
        pass


def run_window_mode() -> None:
    existing = ping_existing()
    if existing is not None:
        if wait_ready(existing, 3):
            open_window("http://127.0.0.1:%d/" % existing)
            return
        # 旧实例已经名存实亡（比如被安全软件关了），继续自己起一份
    port = start_server()
    print("server on port", port)
    if not wait_ready(port):
        alert("内部服务启动失败。\n\n日志位置：%s\\log.txt" % DATA_DIR)
        return
    url = "http://127.0.0.1:%d/" % port
    proc = open_window(url)
    if proc is not None:
        # 窗口关掉就退出，避免后台残留进程
        proc.wait()
    else:
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


def main() -> None:
    setup_log()
    # 维护用：只跑服务、不开窗口，方便自检
    if "--server-only" in sys.argv or "--no-window" in sys.argv:
        try:
            port = start_server()
            (DATA_DIR / "last_port.txt").write_text(str(port), encoding="utf-8")
            print("server-only on port", port)
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        except Exception as e:
            import traceback
            traceback.print_exc()
            alert("服务启动失败：%s\n\n详细信息见 %s\\log.txt" % (e, DATA_DIR))
        return

    try:
        run_window_mode()
    except Exception as e:
        import traceback
        traceback.print_exc()
        alert("启动失败：%s\n\n详细信息见 %s\\log.txt" % (e, DATA_DIR))


def find_edge() -> str | None:
    for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
              r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
        if os.path.isfile(p):
            return p
    return None


def open_window(url: str):
    """用 Edge/Chrome 的应用模式开一个没有地址栏的独立窗口，像原生程序"""
    exe = find_edge()
    if not exe:
        webbrowser.open(url)
        return None
    profile = DATA_DIR / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    args = [exe,
            "--app=" + url,
            "--window-size=1280,860",
            "--start-maximized",
            "--user-data-dir=" + str(profile),
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate",
            "--disable-background-networking"]
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(args, creationflags=flags)


def ping_existing() -> int | None:
    """已经有一个实例在跑就直接复用，避免双击两次开出两个服务"""
    for p in range(8720, 8761):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/ping" % p,
                                        timeout=1) as r:
                data = json.loads(r.read().decode("utf-8"))
                if data.get("server") == "StudyDaily-PC":
                    return p
        except Exception:
            continue
    return None


if __name__ == "__main__":
    main()
