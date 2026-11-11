"""为课程生成卡片缩略图。

孩子还不识字，卡片上能「看到课程画面」比读标题有效得多。
这个脚本为每个视频截一帧、为每个 PDF 用第一页渲染图，结果写回 courses.json 的 poster 字段。

用法：
    python tools/make_posters.py                     # 处理 StudyDaily 目录下的 courses.json
    python tools/make_posters.py --root D:/x         # 指定应用根目录
    python tools/make_posters.py --force             # 重新生成已存在的缩略图

单独跑也可以；prepare_courses.py 导入完成后会自动调用一次。
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path


def run(cmd):
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def grab(src: Path, dst: Path, seek_sec: float) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-ss", f"{seek_sec:.2f}",
            "-i", str(src), "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "4", str(dst)]):
        return dst.is_file() and dst.stat().st_size > 600
    return False


def poster_for(lesson, root):
    """返回相对 root 的缩略图路径（用 / 分隔），失败返回 None"""
    if lesson.get("type") == "pdf":
        pages = lesson.get("pages") or []
        return pages[0] if pages and (root / pages[0]).is_file() else None

    rel = str(lesson.get("file") or "")
    if not rel:
        return None
    src = root / rel
    if not src.is_file():
        return None

    # videos/u01/001.mp4 -> posters/u01/001.jpg
    parts = Path(rel).parts
    stem = Path(rel).stem
    if "videos" in parts:
        idx = parts.index("videos")
        sub = parts[idx + 1:-1]
        dst = root / "posters" / Path(*sub) / (stem + ".jpg")
    else:
        dst = root / "posters" / (stem + ".jpg")

    if dst.is_file() and dst.stat().st_size > 600:
        return dst.relative_to(root).as_posix()

    dur = float(lesson.get("durationSec") or 0)
    # 优先在 3 秒处截，短视频退到 1 秒、再退到第 0 帧
    for seek in (min(3.0, max(0.0, dur * 0.25)), 1.0, 0.0):
        if grab(src, dst, seek):
            return dst.relative_to(root).as_posix()
    return None


def generate(root: Path, force: bool = False, limit: int = 0) -> dict:
    cat_path = root / "courses.json"
    if not cat_path.is_file():
        print(f"[错误] 找不到 {cat_path}")
        return {"ok": 0, "fail": 0}
    cat = json.loads(cat_path.read_text(encoding="utf-8"))

    ok, fail, skipped = 0, 0, 0
    done = 0
    for unit in cat.get("units", []):
        for lesson in unit.get("lessons", []):
            if limit and done >= limit:
                break
            done += 1
            if not force and lesson.get("poster") and (root / lesson["poster"]).is_file():
                skipped += 1
                continue
            p = poster_for(lesson, root)
            if p:
                lesson["poster"] = p
                ok += 1
            else:
                lesson.pop("poster", None)
                fail += 1
            if done % 25 == 0:
                print(f"  … 已处理 {done} 节（新生成 {ok}，跳过 {skipped}，失败 {fail}）", flush=True)

    cat_path.write_text(json.dumps(cat, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": ok, "fail": fail, "skipped": skipped}


def main():
    ap = argparse.ArgumentParser(description="生成课程卡片缩略图")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent), help="应用根目录")
    ap.add_argument("--force", action="store_true", help="忽略已存在的缩略图")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not run(["ffmpeg", "-version"]):
        print("[错误] 找不到 ffmpeg，请先安装并加入 PATH")
        return 1
    print(f"应用目录：{root}")
    r = generate(root, force=args.force)
    print(f"缩略图完成：新生成 {r['ok']}，已存在 {r['skipped']}，失败 {r['fail']}")
    if r["fail"]:
        print("  失败的课程会显示单元图标占位，不影响学习与播放。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
