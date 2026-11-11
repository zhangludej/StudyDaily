#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
生成桌面图标（小熊 + 渐变底），替换原先的简笔图标

用法：
    pip install pillow
    python tools/make_app_icons.py

产出：
    icons/icon-192.png  icons/icon-512.png      PWA / 网页用
    ../StudyDailyAndroid/res/mipmap-xxhdpi/ic_launcher.png   安卓启动图标
"""
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter
except ImportError:
    sys.exit("缺少 Pillow：pip install pillow")

ROOT = Path(__file__).resolve().parent.parent
BEAR = ROOT / "assets" / "img" / "bear.png"
SPARK = ROOT / "assets" / "img" / "sparkles.png"
OUT_ICONS = ROOT / "icons"
ANDROID_ICON = ROOT.parent / "StudyDailyAndroid" / "res" / "mipmap-xxhdpi" / "ic_launcher.png"

TOP = (77, 163, 255)      # 天蓝
BOTTOM = (47, 184, 108)   # 草绿


def gradient(size: int) -> Image.Image:
    img = Image.new("RGB", (size, size), TOP)
    d = ImageDraw.Draw(img)
    for y in range(size):
        t = y / max(1, size - 1)
        # 轻微曲线，让过渡更自然
        t = t ** 0.85
        c = tuple(int(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3))
        d.line([(0, y), (size, y)], fill=c)
    return img


def rounded_mask(size: int, radius: int) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def paste_center(canvas: Image.Image, path: Path, ratio: float, dy: float = 0.0):
    if not path.exists():
        print("  跳过（缺文件）:", path.name)
        return
    src = Image.open(path).convert("RGBA")
    size = int(canvas.width * ratio)
    src = src.resize((size, size), Image.LANCZOS)
    x = (canvas.width - size) // 2
    y = int((canvas.height - size) / 2 + canvas.height * dy)
    canvas.alpha_composite(src, (x, y))


def make_icon(px: int, radius_ratio: float = 0.22) -> Image.Image:
    canvas = gradient(px).convert("RGBA")
    # 底部一圈柔光，让图标有层次
    glow = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([-px * 0.25, px * 0.55, px * 1.25, px * 1.45], fill=(255, 255, 255, 46))
    glow = glow.filter(ImageFilter.GaussianBlur(px * 0.05))
    canvas.alpha_composite(glow)

    paste_center(canvas, BEAR, 0.70, 0.02)
    paste_center(canvas, SPARK, 0.20, -0.30)

    canvas.putalpha(rounded_mask(px, int(px * radius_ratio)))
    return canvas


def main():
    OUT_ICONS.mkdir(parents=True, exist_ok=True)
    for px, name in ((192, "icon-192.png"), (512, "icon-512.png")):
        ic = make_icon(px)
        ic.save(OUT_ICONS / name, "PNG", optimize=True)
        print("  %-12s %dpx  %.0f KB" % (name, px, (OUT_ICONS / name).stat().st_size / 1024))

    # 安卓启动图标：不切圆角（系统自己裁），尺寸 144px 放 xxhdpi
    if ANDROID_ICON.parent.exists():
        a = make_icon(432, radius_ratio=0.0).resize((144, 144), Image.LANCZOS)
        a.save(ANDROID_ICON, "PNG", optimize=True)
        print("  安卓图标     144px  %s" % ANDROID_ICON.name)

    print("图标生成完成")


if __name__ == "__main__":
    main()
