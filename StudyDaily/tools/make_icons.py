#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
StudyDaily - 生成 PWA 图标（纯标准库，不依赖 Pillow）

输出 icons/icon-192.png 与 icons/icon-512.png。
Chrome 安装 PWA 要求 manifest 里至少有 192 和 512 两个尺寸的 PNG。
"""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "icons"


def blend(buf, w, h, x, y, r, g, b, a=255):
    if x < 0 or y < 0 or x >= w or y >= h or a <= 0:
        return
    i = (y * w + x) * 4
    if a >= 255:
        buf[i], buf[i + 1], buf[i + 2], buf[i + 3] = r, g, b, 255
        return
    old_a = buf[i + 3] / 255.0
    na = a / 255.0 + old_a * (1 - na)
    if na <= 0:
        return
    for k, c in enumerate((r, g, b)):
        buf[i + k] = int((c * (a / 255.0) + buf[i + k] * old_a * (1 - a / 255.0)) / na)
    buf[i + 3] = int(na * 255)


def rect(buf, w, h, x0, y0, x1, y1, color, radius=0.0):
    for y in range(max(0, int(y0)), min(h, int(y1) + 1)):
        for x in range(max(0, int(x0)), min(w, int(x1) + 1)):
            if radius > 0:
                cx = min(max(x, x0 + radius), x1 - radius)
                cy = min(max(y, y0 + radius), y1 - radius)
                if (x - cx) ** 2 + (y - cy) ** 2 > radius ** 2:
                    continue
            blend(buf, w, h, x, y, *color)


def seg(buf, w, h, x0, y0, x1, y1, thick, color):
    """带抗锯齿的粗线段（画对勾）"""
    dx, dy = x1 - x0, y1 - y0
    L2 = dx * dx + dy * dy or 1
    for y in range(max(0, int(min(y0, y1) - thick)), min(h, int(max(y0, y1) + thick) + 1)):
        for x in range(max(0, int(min(x0, x1) - thick)), min(w, int(max(x0, x1) + thick) + 1)):
            t = max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / L2))
            d = math.hypot(x - (x0 + t * dx), y - (y0 + t * dy))
            if d <= thick:
                a = int(min(255, (thick - d + 1) * 255))
                blend(buf, w, h, x, y, color[0], color[1], color[2], a)


def make(size: int) -> bytes:
    w = h = size
    buf = bytearray(w * h * 4)
    # 蓝色渐变背景
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(0x2E + (0x1B - 0x2E) * t)
        g = int(0x6B + (0x4A - 0x6B) * t)
        b = int(0xFF + (0xE0 - 0xFF) * t)
        for x in range(w):
            i = (y * w + x) * 4
            buf[i], buf[i + 1], buf[i + 2], buf[i + 3] = r, g, b, 255

    # 白色日历卡片
    mx, top, bottom = 0.19 * w, 0.27 * w, 0.81 * w
    rect(buf, w, h, mx, top, w - mx, bottom, (255, 255, 255, 255), 0.09 * w)
    # 顶部橙色标题条
    rect(buf, w, h, mx, top, w - mx, top + 0.13 * w, (255, 138, 76, 255), 0.09 * w)
    rect(buf, w, h, mx, top + 0.07 * w, w - mx, top + 0.13 * w, (255, 138, 76, 255))
    # 两个挂环
    for cx in (0.36 * w, 0.64 * w):
        rect(buf, w, h, cx - 0.025 * w, top - 0.06 * w,
             cx + 0.025 * w, top + 0.03 * w, (255, 255, 255, 255), 0.025 * w)
    # 中间的对勾
    seg(buf, w, h, 0.31 * w, 0.57 * w, 0.44 * w, 0.70 * w, 0.035 * w, (46, 107, 255, 255))
    seg(buf, w, h, 0.44 * w, 0.70 * w, 0.70 * w, 0.42 * w, 0.035 * w, (46, 107, 255, 255))

    raw = b"".join(b"\x00" + bytes(buf[y * w * 4:(y + 1) * w * 4]) for y in range(h))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def main() -> int:
    ICONS.mkdir(parents=True, exist_ok=True)
    for s in (192, 512):
        p = ICONS / f"icon-{s}.png"
        p.write_bytes(make(s))
        print(f"  OK  {p.relative_to(ROOT)} ({p.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
