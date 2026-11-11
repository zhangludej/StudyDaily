#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
StudyDaily - 生成可真实播放的演示课程（视频 + PDF 练习纸）

生成 3 个单元、14 节课（12 段短视频 + 2 份 PDF）与 courses.json（demo=true）。
视频本身只有十来秒、清单里的时长是模拟值，方便快速体验
「今日清单 -> 播放/翻页 -> 自动完成 -> 顺延 -> 复习 -> 热力图」全流程。

第一天前 4 节里第 3 节就是 PDF，打开应用即可看到翻页效果。

正式使用时运行 prepare_courses.py 会覆盖 courses.json 为真实课程。

    python tools/make_demo.py
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIDEO_DIR = ROOT / "videos" / "demo"
FILE_DIR = ROOT / "files" / "demo"
PAGE_DIR = ROOT / "pages" / "demo"

# (类型, 课名, 模拟分钟数, PDF 页数)
UNITS = [
    ("拼音启蒙", [
        ("video", "a o e", 7, 0),
        ("video", "i u ü", 8, 0),
        ("pdf", "拼音描红练习纸", 5, 2),
        ("video", "b p m f", 6, 0),
        ("video", "d t n l", 9, 0),
    ]),
    ("趣味识字", [
        ("video", "一二三", 8, 0),
        ("video", "人口手", 7, 0),
        ("video", "日月水火", 9, 0),
        ("video", "天地上下", 6, 0),
    ]),
    ("数学思维", [
        ("video", "10 以内数数", 8, 0),
        ("video", "比多少", 7, 0),
        ("pdf", "图形涂色练习", 5, 1),
        ("video", "认识图形", 9, 0),
        ("video", "简单加减", 6, 0),
    ]),
]
COLORS = [0x4F7CFF, 0xFF7A59, 0x3BB273, 0x9B6BFF,
          0xF5A623, 0x2BB6C4, 0xE8557F, 0x6C8C3C,
          0x5A6FF0, 0xD96C2B, 0x2E9E8F, 0x8E5AC8]


def load_font(size: int):
    for p in ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
              "/System/Library/Fonts/PingFang.ttc",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
        if Path(p).exists():
            try:
                from PIL import ImageFont
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    from PIL import ImageFont
    return ImageFont.load_default()


def practice_page(page_no: int, label: str, color: tuple):
    """画一张练习纸：四线三格 + 田字格，纯 PIL，不依赖系统字体文件也能跑"""
    from PIL import Image, ImageDraw
    w, h = 800, 1100
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    f_big = load_font(52)
    f_small = load_font(30)

    d.rectangle([0, 0, w, 110], fill=color)
    d.text((40, 28), f"{label}   {page_no}", fill="white", font=f_big)

    # 四线三格
    for row in range(3):
        y = 190 + row * 200
        for k in range(4):
            d.line([(60, y + k * 34), (w - 60, y + k * 34)],
                   fill=(205, 218, 240), width=2)
        d.text((100, y + 40), "a   o   e", fill=(70, 95, 140), font=f_big)
        d.line([(60, y + 34), (60, y + 102)], fill=(205, 218, 240), width=2)
        d.line([(w - 60, y + 34), (w - 60, y + 102)], fill=(205, 218, 240), width=2)

    # 田字格
    d.text((60, 820), "写一写", fill=(70, 95, 140), font=f_small)
    for r in range(2):
        for c in range(4):
            x0, y0 = 60 + c * 175, 880 + r * 100
            d.rectangle([x0, y0, x0 + 150, y0 + 90], outline=(190, 205, 230), width=2)
            d.line([(x0 + 75, y0), (x0 + 75, y0 + 90)], fill=(225, 232, 245), width=1)
            d.line([(x0, y0 + 45), (x0 + 150, y0 + 45)], fill=(225, 232, 245), width=1)
    return img


def write_pdf(pdf_path: Path, image_paths: list[Path]) -> None:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    for p in image_paths:
        c.drawImage(str(p), 0, 0, width=A4[0], height=A4[1])
        c.showPage()
    c.save()


def make_video(dst: Path, idx: int) -> bool:
    seconds = 10 + (idx % 4) * 2
    color = COLORS[(idx - 1) % len(COLORS)]
    freq = 330 + idx * 22
    cmd = [
        "ffmpeg", "-nostdin", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c=0x{color:06X}:s=640x360:r=25:d={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-ac", "2",
        "-shortest", "-movflags", "+faststart", str(dst),
    ]
    p = subprocess.run(cmd, text=True, encoding="utf-8", errors="replace",
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if p.returncode != 0:
        print(f"[错误] 生成 {dst.name} 失败：{(p.stderr or '')[-300:]}")
        return False
    return True


def main() -> int:
    if not shutil.which("ffmpeg"):
        print("[错误] 找不到 ffmpeg，请先安装并加入 PATH")
        return 1
    try:
        import PIL  # noqa: F401
        import reportlab  # noqa: F401
    except ImportError:
        print("[错误] 生成演示 PDF 需要 Pillow 和 reportlab：pip install pillow reportlab")
        return 1

    for d in (VIDEO_DIR, FILE_DIR, PAGE_DIR):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)

    units = []
    idx = 0
    for ui, (unit_name, rows) in enumerate(UNITS, start=1):
        lessons = []
        for li, (kind, title, minutes, pages_count) in enumerate(rows, start=1):
            idx += 1
            lesson = {
                "id": f"u{ui:02d}-{li:03d}",
                "title": title,
                "durationSec": minutes * 60,
                "order": li,
            }
            if kind == "video":
                name = f"{idx:03d}.mp4"
                if not make_video(VIDEO_DIR / name, idx):
                    return 1
                lesson["file"] = f"videos/demo/{name}"
                print(f"  OK  视频  {lesson['file']}  {title}")
            else:
                pdf_rel = f"files/demo/{idx:03d}.pdf"
                page_rels = []
                imgs = []
                pd = PAGE_DIR / f"{idx:03d}"
                pd.mkdir(parents=True, exist_ok=True)
                color = COLORS[(idx - 1) % len(COLORS)]
                rgb = ((color >> 16) & 255, (color >> 8) & 255, color & 255)
                for n in range(1, pages_count + 1):
                    jp = pd / f"p{n:03d}.jpg"
                    practice_page(n, title, rgb).save(jp, "JPEG", quality=86)
                    imgs.append(jp)
                    page_rels.append(f"pages/demo/{idx:03d}/p{n:03d}.jpg")
                write_pdf(ROOT / pdf_rel, imgs)
                lesson.update({
                    "type": "pdf",
                    "file": pdf_rel,
                    "pageCount": pages_count,
                    "pages": page_rels,
                    "renderMode": "demo",
                })
                print(f"  OK  PDF   {pdf_rel}  {title}（{pages_count} 页）")
            lessons.append(lesson)
        units.append({"id": f"u{ui:02d}", "name": unit_name, "lessons": lessons})

    catalog = {
        "version": 1,
        "title": "幼小衔接 · 演示课程",
        "generatedAt": dt.datetime.now().isoformat(timespec="seconds"),
        "demo": True,
        "durationSimulated": True,
        "units": units,
    }
    (ROOT / "courses.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    n_pdf = sum(1 for u in units for l in u["lessons"] if l.get("type") == "pdf")
    print(f"\n已生成 {idx - n_pdf} 段视频 + {n_pdf} 份 PDF，共 {idx} 节课和 courses.json（演示模式）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
