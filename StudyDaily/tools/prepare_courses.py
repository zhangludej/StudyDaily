#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
StudyDaily - 课件（视频 + PDF）一次性转码 + 课程清单生成

在【电脑】上跑一次即可，之后平板完全离线使用，不需要常开电脑。

用法：
    python tools/prepare_courses.py --src "D:/幼小衔接课程"
    python tools/prepare_courses.py --src "D:/幼小衔接课程" --dry-run   # 只扫描，先看报告
    python tools/prepare_courses.py --src "D:/幼小衔接课程" --scale 1080 # 允许到 1080p
    python tools/prepare_courses.py --src "D:/幼小衔接课程" --scale 0    # 保留原始分辨率
    python tools/prepare_courses.py --src "D:/幼小衔接课程" --force       # 强制重转

视频处理策略（先用 ffprobe 分类，能不重编码就不重编码，省几小时）：
    copy   : 已是 MP4 / H.264 / AAC / yuv420p / 高度达标  -> 直接复制
    remux  : 视频达标但容器是 ts/mkv/mov                   -> -c copy 换封装（秒级）
    audio  : 视频达标但音频是 DTS/FLAC/E-AC3 等            -> 只转音频为 AAC
    full   : H.265/HEVC、10bit、VP9、超高清                -> 重编码为 H.264 + AAC

注意：必须的是「H.264 + AAC + MP4」，不是 720p。--scale 只是最大高度上限，
      原本 480p/540p 的课件会原样保留，不会被放大；--scale 0 完全不限制高度。

PDF 处理（练习纸 / 讲义 / 字帖，与视频按文件名自然排序混排）：
    复制原文件到 files/uXX/NNN.pdf，并把每页渲染成 pages/uXX/NNN/p001.jpg，
    孩子可在应用内翻页阅读。渲染优先用 pypdfium2（pip install pypdfium2），
    也支持 PyMuPDF / pdftoppm；都没有时保留原 PDF 并在应用里提供系统阅读器入口。

输出（全部 ASCII 路径，避免中文/空格在 URL 上出问题）：
    videos/u01/001.mp4
    files/u01/002.pdf
    pages/u01/002/p001.jpg
    courses.json
    transcode-report.json
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

VIDEO_EXTS = {
    ".ts", ".mkv", ".mp4", ".avi", ".mov", ".flv", ".wmv", ".m4v",
    ".webm", ".mpg", ".mpeg", ".m2ts", ".mts", ".3gp", ".rmvb", ".vob",
}
PDF_EXTS = {".pdf"}
ALL_EXTS = VIDEO_EXTS | PDF_EXTS
H264_NAMES = {"h264", "avc", "avc1"}
AAC_NAMES = {"aac", "aac_latm"}


def log(msg: str) -> None:
    print(msg, flush=True)


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        log(f"[错误] 找不到 {name}。请先安装 FFmpeg 并加入 PATH。")
        log("       Windows: https://www.gyan.dev/ffmpeg/builds/ 下载 essentials 版，")
        log("       解压后把 bin 目录加入系统 PATH，重开终端再运行。")
        sys.exit(1)
    return path


def run(cmd: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, check=True, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE if capture else subprocess.PIPE,
    )


def natural_key(s: str):
    """自然排序：第2课 排在 第10课 前面"""
    parts = re.split(r"(\d+)", str(s))
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def probe(path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=index,codec_type,codec_name,pix_fmt,height,width",
        "-of", "json", str(path),
    ]
    try:
        out = run(cmd, capture=True)
        data = json.loads(out.stdout or "{}")
    except Exception as e:
        return {"duration": 0.0, "video": None, "audio": None, "error": str(e)}

    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    try:
        duration = float(data.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return {"duration": duration, "video": v, "audio": a, "error": None}


def decide(info: dict, ext: str, target_height: int) -> tuple[str, str]:
    """返回 (策略, 原因)"""
    v, a = info.get("video"), info.get("audio")
    if not v:
        return "full", "未检测到视频流，尝试强制重编码"
    vc = (v.get("codec_name") or "").lower()
    pix = (v.get("pix_fmt") or "").lower()
    height = int(v.get("height") or 0)
    ac = (a.get("codec_name") or "").lower() if a else ""

    if vc not in H264_NAMES:
        return "full", f"视频编码 {vc or '未知'} 需转为 H.264"
    if pix != "yuv420p":
        return "full", f"像素格式 {pix or '未知'} 平板可能不兼容"
    if target_height and height > target_height:
        return "full", f"分辨率 {height}p 降到 {target_height}p 省空间"
    if ac and ac not in AAC_NAMES:
        return "audio", f"音频 {ac} 平板可能不支持，只转音频为 AAC"
    if not ac:
        return "audio", "未检测到音轨，补一条静音 AAC 音轨"
    if ext.lower() != ".mp4":
        return "remux", f"容器 {ext} 换封装为 MP4"
    return "copy", "已符合要求，直接复制"


def scan(src: Path) -> list[dict]:
    """顶层子文件夹 = 单元；视频与 PDF 按文件名自然排序混排"""
    groups: list[tuple[str, Path]] = []
    loose: list[Path] = []
    for child in sorted(src.iterdir(), key=lambda p: natural_key(p.name)):
        if child.name.startswith(".") or child.name.lower() in {
            "videos", "tools", "icons", "files", "pages"
        }:
            continue
        if child.is_dir():
            groups.append((child.name, child))
        elif child.is_file() and child.suffix.lower() in ALL_EXTS:
            loose.append(child)
    if loose:
        groups.insert(0, ("课程", src))

    if not groups:
        return []

    # 单元按自然排序
    groups.sort(key=lambda g: natural_key(g[0]))
    items = []
    for ui, (unit_name, folder) in enumerate(groups, start=1):
        files = []
        for p in folder.rglob("*"):
            if p.is_file() and p.suffix.lower() in ALL_EXTS:
                if folder == src and p.parent != src:
                    continue  # 默认单元只收顶层文件
                files.append(p)
        files.sort(key=lambda p: natural_key(str(p.relative_to(folder))))
        for li, p in enumerate(files, start=1):
            is_pdf = p.suffix.lower() in PDF_EXTS
            items.append({
                "unit_index": ui,
                "unit_name": unit_name,
                "lesson_index": li,
                "title": p.stem,
                "src": p,
                "ext": p.suffix.lower(),
                "kind": "pdf" if is_pdf else "video",
                "out": (Path(f"files/u{ui:02d}/{li:03d}.pdf") if is_pdf
                        else Path(f"videos/u{ui:02d}/{li:03d}.mp4")),
                "pages_dir": Path(f"pages/u{ui:02d}/{li:03d}") if is_pdf else None,
            })
    return items


def build_ffmpeg_cmd(item: dict, strategy: str, src_root: Path, out_root: Path,
                     target_height: int, crf: int) -> list[str]:
    src = str(item["src"])
    dst = str(out_root / item["out"])
    base = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", src]
    tail = ["-movflags", "+faststart", "-threads", "2", dst]

    if strategy == "remux":
        return base + ["-map", "0:v:0", "-map", "0:a:0?", "-c", "copy"] + tail
    if strategy == "audio":
        return base + [
            "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k", "-ac", "2",
        ] + tail
    # full
    vf = (["-vf", f"scale=-2:{target_height}"]
          if target_height and (item.get("height") or 0) > target_height else [])
    return base + [
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        *vf,
        "-c:a", "aac", "-b:a", "128k", "-ac", "2",
    ] + tail


def render_pdf(pdf: Path, pages_dir: Path, dpi: int, force: bool) -> tuple[int, str]:
    """把 PDF 每页渲染成 JPG，返回 (页数, 渲染方式)。没有可用后端时返回 (0, 'native')"""
    pages_dir.mkdir(parents=True, exist_ok=True)
    old = sorted(pages_dir.glob("p[0-9][0-9][0-9].jpg"))
    if old and not force:
        return len(old), "cached"
    for f in old:
        try:
            f.unlink()
        except OSError:
            pass

    # 1) pypdfium2（纯 Python，推荐：pip install pypdfium2）
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(str(pdf))
        try:
            n = len(doc)
            for i in range(n):
                bitmap = doc[i].render(scale=dpi / 72.0)
                bitmap.to_pil().convert("RGB").save(
                    pages_dir / f"p{i + 1:03d}.jpg", "JPEG", quality=82, optimize=True)
            return n, "pypdfium2"
        finally:
            doc.close()
    except ImportError:
        pass
    except Exception as e:
        log(f"      pypdfium2 渲染失败（{e}），尝试其它方式")

    # 2) PyMuPDF
    try:
        import fitz
        doc = fitz.open(str(pdf))
        try:
            n = len(doc)
            for i, page in enumerate(doc, start=1):
                page.get_pixmap(dpi=dpi).save(str(pages_dir / f"p{i:03d}.jpg"))
            return n, "pymupdf"
        finally:
            doc.close()
    except ImportError:
        pass
    except Exception as e:
        log(f"      PyMuPDF 渲染失败（{e}），尝试其它方式")

    # 3) poppler 命令行
    if shutil.which("pdftoppm"):
        prefix = pages_dir / "tmp"
        proc = subprocess.run(
            ["pdftoppm", "-jpeg", "-r", str(dpi), "-jpegopt", "quality=82",
             str(pdf), str(prefix)],
            text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if proc.returncode == 0:
            imgs = sorted(pages_dir.glob("tmp-*.jpg"), key=lambda p: natural_key(p.name))
            for i, f in enumerate(imgs, start=1):
                f.replace(pages_dir / f"p{i:03d}.jpg")
            return len(imgs), "pdftoppm"

    return 0, "native"


def process_pdf(item: dict, args, out_root: Path) -> dict:
    """PDF 课件：保留原文件 + 每页转图片，按页数估算学习时长"""
    dst = out_root / item["out"]
    pages_dir = out_root / item["pages_dir"]
    rel = item["pages_dir"].as_posix()
    result = {
        "src": str(item["src"]),
        "out": str(item["out"]).replace("\\", "/"),
        "strategy": "pdf",
        "ok": False,
        "error": None,
    }
    if args.dry_run:
        result.update({"ok": True, "pageCount": 0, "renderMode": "dry-run",
                       "duration": args.pdf_minutes * 60})
        return result
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists() or args.force:
            shutil.copy2(item["src"], dst)
        n, mode = render_pdf(dst, pages_dir, args.pdf_dpi, args.force)
        minutes = max(args.pdf_minutes, n)  # 页数多时按每页 1 分钟上调
        result.update({
            "ok": True,
            "pageCount": n,
            "renderMode": mode,
            "duration": minutes * 60,
            "pages": [f"{rel}/p{i:03d}.jpg" for i in range(1, n + 1)],
        })
        if mode == "native":
            log("      未找到 PDF 渲染库，已保留原文件（应用内可用系统阅读器打开）")
    except Exception as e:
        result["error"] = str(e)[:500]
    return result


def process_one(item: dict, args, out_root: Path) -> dict:
    if item.get("kind") == "pdf":
        return process_pdf(item, args, out_root)
    dst = out_root / item["out"]
    dst.parent.mkdir(parents=True, exist_ok=True)
    result = {"src": str(item["src"]), "out": str(item["out"]).replace("\\", "/"),
              "strategy": None, "ok": False, "error": None}

    if dst.exists() and dst.stat().st_size > 0 and not args.force:
        info = probe(dst)
        result.update({"strategy": "skip-existing", "ok": True,
                       "duration": info["duration"]})
        return result

    info = probe(item["src"])
    if info["error"]:
        result["error"] = "ffprobe 读取失败: " + info["error"]
        return result
    v = info.get("video") or {}
    item["height"] = int(v.get("height") or 0)
    strategy, reason = decide(info, item["ext"], args.scale)
    result["strategy"] = strategy
    result["reason"] = reason
    result["source_duration"] = info["duration"]

    if args.dry_run:
        result["ok"] = True
        result["duration"] = info["duration"]
        return result

    try:
        if strategy == "copy":
            shutil.copy2(item["src"], dst)
        else:
            cmd = build_ffmpeg_cmd(item, strategy, args.src, out_root, args.scale, args.crf)
            proc = subprocess.run(cmd, text=True, encoding="utf-8", errors="replace",
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if proc.returncode != 0:
                raise RuntimeError((proc.stderr or "").strip()[-500:])
        out_info = probe(dst)
        result["duration"] = out_info["duration"] or info["duration"]
        result["ok"] = True
    except Exception as e:
        result["error"] = str(e)[:500]
        try:
            if dst.exists():
                dst.unlink()
        except OSError:
            pass
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="StudyDaily 课件转码与课程清单生成")
    ap.add_argument("--src", required=True, help="原始课件根目录（按单元分子文件夹）")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent),
                    help="输出根目录，默认项目根目录")
    ap.add_argument("--scale", type=int, default=720,
                    help="视频最大高度，默认 720；0 表示保留原始分辨率（并非必须是 720p）")
    ap.add_argument("--crf", type=int, default=23, help="H.264 质量，默认 23")
    ap.add_argument("--pdf-minutes", type=int, default=5,
                    help="PDF 课默认时长（分钟），页数多时按每页 1 分钟自动上调")
    ap.add_argument("--pdf-dpi", type=int, default=110, help="PDF 转图片的分辨率，默认 110")
    ap.add_argument("--jobs", type=int, default=2, help="并发数，默认 2")
    ap.add_argument("--force", action="store_true", help="忽略已有输出，强制重转")
    ap.add_argument("--dry-run", action="store_true", help="只扫描出报告，不写文件")
    ap.add_argument("--title", default="幼小衔接课程", help="课程名称")
    args = ap.parse_args()

    src = Path(args.src).expanduser().resolve()
    out_root = Path(args.out).expanduser().resolve()
    if not src.is_dir():
        log(f"[错误] 源目录不存在：{src}")
        return 1
    out_root.mkdir(parents=True, exist_ok=True)

    items = scan(src)
    if not items:
        log(f"[错误] 在 {src} 下没有找到课件。支持的格式：{', '.join(sorted(ALL_EXTS))}")
        return 1

    n_pdf = sum(1 for i in items if i["kind"] == "pdf")
    n_video = len(items) - n_pdf
    if n_video:
        require_tool("ffprobe")
        require_tool("ffmpeg")

    log(f"扫描完成：{n_video} 个视频 + {n_pdf} 个 PDF，"
        f"{len(set(i['unit_index'] for i in items))} 个单元")
    log(f"输出目录：{out_root}\n")

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        futs = {ex.submit(process_one, it, args, out_root): it for it in items}
        for n, fut in enumerate(concurrent.futures.as_completed(futs), start=1):
            it = futs[fut]
            try:
                r = fut.result()
            except Exception as e:
                r = {"src": str(it["src"]), "out": str(it["out"]).replace("\\", "/"),
                     "strategy": None, "ok": False, "error": str(e)}
            results.append((it, r))
            flag = "OK " if r["ok"] else "FAIL"
            log(f"[{n}/{len(items)}] {flag} {r['strategy'] or '-':<13} {it['title'][:34]}"
                + ("" if r["ok"] else "  <- " + str(r["error"])[:90]))

    by_out = {r["out"]: r for _, r in results}
    counts: dict[str, int] = {}
    for _, r in results:
        counts[r["strategy"] or "error"] = counts.get(r["strategy"] or "error", 0) + 1
    failed = [r for _, r in results if not r["ok"]]

    report = {
        "generatedAt": dt.datetime.now().isoformat(timespec="seconds"),
        "source": str(src),
        "dryRun": args.dry_run,
        "scale": args.scale,
        "crf": args.crf,
        "pdfMinutes": args.pdf_minutes,
        "pdfDpi": args.pdf_dpi,
        "counts": counts,
        "failedCount": len(failed),
        "failed": failed,
        "items": results and [{"source": it["title"], **r} for it, r in results],
    }
    (out_root / "transcode-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    log("\n分类统计：" + "，".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if failed:
        log(f"[警告] {len(failed)} 个文件处理失败，详见 transcode-report.json")

    if args.dry_run:
        log("\n这是 --dry-run，未写入任何文件。确认无误后去掉 --dry-run 再跑一次。")
        return 0

    units = []
    grouped: dict[int, list] = {}
    for it, r in results:
        if not r["ok"]:
            continue
        grouped.setdefault(it["unit_index"], []).append((it, r))
    for ui in sorted(grouped):
        rows = sorted(grouped[ui], key=lambda x: x[0]["lesson_index"])
        lessons = []
        for it, r in rows:
            lesson = {
                "id": f"u{ui:02d}-{it['lesson_index']:03d}",
                "title": it["title"],
                "file": by_out[r["out"]]["out"],
                "durationSec": round(float(r.get("duration") or 0)),
                "order": it["lesson_index"],
            }
            if it["kind"] == "pdf":
                lesson["type"] = "pdf"
                lesson["pageCount"] = int(r.get("pageCount") or 0)
                lesson["pages"] = r.get("pages") or []
                lesson["renderMode"] = r.get("renderMode") or "native"
            lessons.append(lesson)
        if lessons:
            units.append({
                "id": f"u{ui:02d}",
                "name": rows[0][0]["unit_name"],
                "lessons": lessons,
            })

    total_sec = sum(l["durationSec"] for u in units for l in u["lessons"])
    catalog = {
        "version": 1,
        "title": args.title,
        "generatedAt": dt.datetime.now().isoformat(timespec="seconds"),
        "demo": False,
        "units": units,
    }
    (out_root / "courses.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")

    n_lessons = sum(len(u["lessons"]) for u in units)
    native = sum(1 for _, r in results if r.get("ok") and r.get("renderMode") == "native")
    log(f"\n完成：{len(units)} 个单元 / {n_lessons} 节课（含 {n_pdf} 个 PDF），"
        f"总时长约 {total_sec / 3600:.1f} 小时")
    if native:
        log(f"[提示] {native} 个 PDF 未能在应用内翻页，已保留原文件。"
            "执行 pip install pypdfium2 后加 --force 重跑即可。")
    log(f"已生成 {out_root / 'courses.json'}")
    log("下一步：把整个 StudyDaily 文件夹拷到安卓平板，按应用内「家长端 → 使用帮助」操作。")
    if failed:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
