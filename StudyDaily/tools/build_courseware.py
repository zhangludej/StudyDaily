#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从主课数据 + 本地视频批量生成 PDF 课件（字卡 + 静止段讲解页）。

产出（都在 <root>/pdfs/ 下，路径与 videos/ 同级，纯离线）：
  pdfs/cw-hanzi/<集号>/pages/page-001.jpg ...   识字字卡页图（App 翻页）
  pdfs/cw-hanzi/<集号>/printable.pdf             每集一本，可拷出来打印描红
  pdfs/cw-pinyin/<集号>/pages/...                 拼音四线三格卡
  pdfs/cw-video/<集号>/pages/...                 124 集静止段讲解页（视频原画）
  pdfs/catalog.json                              增量书架清单（重启 App 即生效）

用法：
  python tools/build_courseware.py --sample     # 只出样张，快
  python tools/build_courseware.py --full       # 全量 124 集
依赖：Pillow、pypinyin、ffmpeg（PATH 里没有时自动找常见安装）
"""
import argparse
import io
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageChops, ImageStat

HERE = Path(__file__).resolve().parent.parent
W, H = 900, 1200
CREAM = "#FFFBF3"
ORANGE = "#F97316"
INK = "#1C1917"
MUTED = "#A8A29E"

F_KAI = "C:/Windows/Fonts/simkai.ttf"
F_HEI = "C:/Windows/Fonts/simhei.ttf"
F_YAHEI = "C:/Windows/Fonts/msyh.ttc"
F_YAHEI_B = "C:/Windows/Fonts/msyhbd.ttc"

try:
    from pypinyin import pinyin as _pinyin
except ImportError:
    _pinyin = None


def font(path, size):
    return ImageFont.truetype(path, size)


def py(ch):
    if _pinyin and "\u4e00" <= ch <= "\u9fff":
        return _pinyin(ch)[0][0]
    return ""


def find_ffmpeg():
    p = shutil.which("ffmpeg")
    if p:
        return p
    for c in [
        "C:/Users/飞翔/Doubao/chats/2026-09-28/new-chat/ffmpeg/bin/ffmpeg.exe",
        "C:/Users/飞翔/Doubao/chats/2026-09-12/new-chat/voice-pro/installer_files/ffmpeg/bin/ffmpeg.exe",
    ]:
        if Path(c).is_file():
            return c
    raise RuntimeError("找不到 ffmpeg")


# ---------------------------------------------------------------- 卡片渲染

def card_base(label, sub):
    img = Image.new("RGB", (W, H), CREAM)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((48, 48, W - 48, H - 48), radius=36, fill="#FFFFFF",
                        outline="#F2E8DA", width=3)
    d.text((90, 92), label, font=font(F_YAHEI_B, 34), fill=ORANGE)
    d.text((90, 142), sub, font=font(F_YAHEI, 24), fill=MUTED)
    d.line((90, 186, W - 90, 186), fill="#F2E8DA", width=3)
    return img, d


def draw_tianzige(d, box, ch):
    """田字格 + 楷体大字，可直接打印描红。"""
    x0, y0, x1, y1 = box
    d.rectangle((x0, y0, x1, y1), outline="#E8C9A0", width=5)
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    for i in range(y0 + 12, y1 - 12, 22):          # 虚线
        d.line((cx, i, cx, min(i + 11, y1 - 12)), fill="#EFD9BC", width=2)
    for i in range(x0 + 12, x1 - 12, 22):
        d.line((i, cy, min(i + 11, x1 - 12), cy), fill="#EFD9BC", width=2)
    f = font(F_KAI, int((y1 - y0) * 0.70))
    bb = d.textbbox((0, 0), ch, font=f)
    d.text((cx - (bb[2] - bb[0]) / 2 - bb[0], cy - (bb[3] - bb[1]) / 2 - bb[1]),
           ch, font=f, fill=INK)
    # 四角小标记
    for px, py in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
        d.ellipse((px - 5, py - 5, px + 5, py + 5), fill=ORANGE)


def hanzi_card(ch, idx, total, label):
    img, d = card_base("识字字卡", label)
    p = py(ch)
    if p:
        f = font(F_YAHEI_B, 76)
        bb = d.textbbox((0, 0), p, font=f)
        d.text(((W - (bb[2] - bb[0])) / 2 - bb[0], 220), p, font=f, fill=ORANGE)
    draw_tianzige(d, (170, 350, W - 170, 890), ch)
    d.text((90, 950), "描一描 · 读一读 · 组个词", font=font(F_YAHEI, 34), fill=MUTED)
    d.text((90, 1030), "%d / %d" % (idx, total), font=font(F_YAHEI, 28), fill=MUTED)
    return img


def pinyin_card(token, idx, total, label):
    img, d = card_base("拼音卡", label)
    # 四线三格
    x0, x1, top, gap = 120, W - 120, 400, 130
    for i in range(4):
        y = top + i * gap
        d.line((x0, y, x1, y), fill="#E8C9A0" if i in (0, 3) else "#EFD9BC", width=4)
    f = font(F_YAHEI_B, 132)
    d.text((W / 2, top + gap * 1.5), token, font=f, fill=INK, anchor="mm")
    d.text((90, 950), "四线三格 · 写一写", font=font(F_YAHEI, 34), fill=MUTED)
    d.text((90, 1030), "%d / %d" % (idx, total), font=font(F_YAHEI, 28), fill=MUTED)
    return img


# ---------------------------------------------------------------- 抽帧

def still_frames(video, out_dir, ffmpeg, max_pages=14, sample_every=2.0):
    """每 sample_every 秒抽一帧，找连续静止段，取段中间帧当讲解页。

    帧通过 image2pipe 直接读进内存，不落临时文件：批量生成上百集时
    不会产生几万个临时 jpg，也避开批量删除的安全限制。
    """
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(video),
           "-vf", "fps=1/%s,scale=960:-1" % sample_every,
           "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "3", "pipe:1"]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=300)
    raw = proc.stdout
    # 按 SOI/EOI 切出一张张 JPEG
    blobs, start, i = [], None, 0
    while i < len(raw) - 1:
        if raw[i] == 0xFF and raw[i + 1] == 0xD8:
            start = i
        elif raw[i] == 0xFF and raw[i + 1] == 0xD9 and start is not None:
            blobs.append(raw[start:i + 2]); start = None; i += 1
        i += 1
    if len(blobs) < 4:
        return []
    frames = [Image.open(io.BytesIO(b)).convert("RGB") for b in blobs]
    small = [im.resize((120, 68)) for im in frames]
    diffs = [sum(ImageStat.Stat(ImageChops.difference(small[i], small[i - 1])).mean) / 3
             for i in range(1, len(small))]

    # 连续「几乎不变」的段 = 画面停住（字卡/板书页）；用户接受杂质，规则从宽
    segments, start = [], None
    for i, dv in enumerate(diffs):
        if dv < 6 and start is None:
            start = i
        elif dv >= 6 and start is not None:
            if i - start >= 1:
                segments.append((start, i))
            start = None
    if start is not None and len(diffs) - start >= 1:
        segments.append((start, len(diffs)))

    picks = []
    for a, b in segments:
        mid = (a + b) // 2
        if 0 <= mid < len(frames):
            picks.append(mid)
    # 相似页去重 + 数量上限
    kept, prev = [], None
    for idx in picks:
        sm = small[idx]
        if prev is not None and sum(ImageStat.Stat(ImageChops.difference(sm, prev)).mean) / 3 < 7:
            continue
        kept.append((frames[idx], sm))
        prev = sm
        if len(kept) >= max_pages:
            break
    if not kept:
        return []
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    for old in pages_dir.glob("page-*.jpg"):   # 覆盖式重跑，不留旧页
        old.unlink()
    out = []
    for i, (frame, _) in enumerate(kept, 1):
        # 统一贴到 3:4 奶油底页面上，App 里翻页时视觉与字卡一致
        page, pd = card_base("课堂讲解页", "第 %d 页 / 共 %d 页" % (i, len(kept)))
        frame = frame.copy()
        frame.thumbnail((W - 120, 720))
        x, y = (W - frame.width) // 2, 300 + (720 - frame.height) // 2
        page.paste(frame, (x, y))
        pd.rounded_rectangle((x, y, x + frame.width, y + frame.height),
                             radius=8, outline="#F2E8DA", width=3)
        pd.text((90, 1080), "这一集的重点画面", font=font(F_YAHEI, 30), fill=MUTED)
        dst = pages_dir / ("page-%03d.jpg" % i)
        page.save(dst, quality=88)
        out.append(dst)
    return out


# ---------------------------------------------------------------- 解析与组装

def hanzi_tokens(title):
    """识字课标题形如「01人从坐口伞女子好…」→ 汉字列表。"""
    s = re.sub(r"^\d+\s*", "", title)
    return [c for c in s if "\u4e00" <= c <= "\u9fff"]


def pinyin_tokens(title):
    """拼音课标题里的拉丁字母/音节，如「单韵母a o e的认识」→ [a, o, e]。"""
    s = re.sub(r"^\d+\s*", "", title)
    return re.findall(r"[a-zA-ZüÜ]{1,6}", s)


def make_printable(images, pdf_path):
    if not images:
        return
    rgb = [im.convert("RGB") for im in images]
    rgb[0].save(pdf_path, save_all=True, append_images=rgb[1:], resolution=110.0)


def rel(p):
    return str(p.relative_to(HERE)).replace("\\", "/")


def main():
    global HERE
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(HERE))
    ap.add_argument("--sample", action="store_true", help="只出 5 张字卡 + 1 集抽帧样张")
    ap.add_argument("--full", action="store_true", help="全量 124 集")
    args = ap.parse_args()
    if not args.sample and not args.full:
        ap.error("请指定 --sample 或 --full")

    HERE = Path(args.root).resolve()
    ffmpeg = find_ffmpeg()
    courses = json.loads((HERE / "courses.json").read_text(encoding="utf-8"))
    pdfs = HERE / "pdfs"
    pdfs.mkdir(exist_ok=True)
    out_units = []

    # ---- 1) 识字字卡（40 集）
    u_h = next(u for u in courses["units"] if "识字" in u["name"])
    lessons_h = []
    src = u_h["lessons"][:1] if args.sample else u_h["lessons"]
    for n, l in enumerate(src, 1):
        chars = hanzi_tokens(l["title"])[:6] if args.sample else hanzi_tokens(l["title"])
        if not chars:
            continue
        d = pdfs / "cw-hanzi" / ("%03d" % n)
        pages_dir = d / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)
        imgs, paths = [], []
        for i, ch in enumerate(chars, 1):
            im = hanzi_card(ch, i, len(chars), "第 %d 集 · 共 %d 字" % (n, len(chars)))
            p = pages_dir / ("page-%03d.jpg" % i)
            im.save(p, quality=88)
            imgs.append(im); paths.append(p)
        if not args.sample:
            make_printable(imgs, d / "printable.pdf")
        lessons_h.append({
            "id": "cwh-%03d" % n, "title": "第%d集 %s" % (n, "".join(chars[:8]) + ("…" if len(chars) > 8 else "")),
            "type": "pdf", "file": "", "pages": [rel(p) for p in paths],
            "poster": rel(paths[0]), "durationSec": 0, "order": n,
        })
        print("[字卡] 第%d集 %d 字" % (n, len(chars)))
    out_units.append({"id": "cw-hanzi", "name": "识字字卡", "interest": True,
                      "lessons": lessons_h})

    # ---- 2) 拼音卡（22 集）
    u_p = next(u for u in courses["units"] if "拼音" in u["name"])
    lessons_p = []
    src = u_p["lessons"][:2] if args.sample else u_p["lessons"]
    for n, l in enumerate(src, 1):
        tokens = pinyin_tokens(l["title"])[:6]
        if not tokens:
            continue
        d = pdfs / "cw-pinyin" / ("%03d" % n)
        pages_dir = d / "pages"; pages_dir.mkdir(parents=True, exist_ok=True)
        imgs, paths = [], []
        for i, t in enumerate(tokens, 1):
            im = pinyin_card(t, i, len(tokens), "第 %d 集" % n)
            p = pages_dir / ("page-%03d.jpg" % i); im.save(p, quality=88)
            imgs.append(im); paths.append(p)
        if not args.sample:
            make_printable(imgs, d / "printable.pdf")
        lessons_p.append({
            "id": "cwp-%03d" % n, "title": l["title"], "type": "pdf", "file": "",
            "pages": [rel(p) for p in paths], "poster": rel(paths[0]),
            "durationSec": 0, "order": n,
        })
    if lessons_p:
        out_units.append({"id": "cw-pinyin", "name": "拼音卡", "interest": True,
                          "lessons": lessons_p})

    # ---- 3) 124 集静止段讲解页
    all_lessons = [(u, l) for u in courses["units"] for l in u["lessons"]]
    if args.sample:
        all_lessons = all_lessons[:1]
    lessons_v = []
    t0 = time.time()
    for i, (u, l) in enumerate(all_lessons, 1):
        video = HERE / l["file"]
        if not video.is_file():
            print("[抽帧] 跳过（无视频）", l["file"]); continue
        d = pdfs / "cw-video" / ("%03d" % i)
        pages = still_frames(video, d, ffmpeg)
        if not pages:
            continue
        lessons_v.append({
            "id": "cwv-%03d" % i,
            "title": "%s · %s" % (u["name"], l["title"][:28]),
            "type": "pdf", "file": "", "pages": [rel(p) for p in pages],
            "poster": rel(pages[0]), "durationSec": 0, "order": i,
        })
        if i % 10 == 0 or args.sample:
            print("[抽帧] %d/%d 本集%d页 累计%.0fs" % (i, len(all_lessons), len(pages), time.time() - t0), flush=True)
    if lessons_v:
        out_units.append({"id": "cw-video", "name": "课堂讲解页", "interest": True,
                          "lessons": lessons_v})

    # ---- 4) 增量书架清单
    catalog = {"version": 1, "generatedAt": time.strftime("%Y-%m-%d %H:%M"),
               "units": out_units}
    (pdfs / "catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    total_pages = sum(len(l["pages"]) for u in out_units for l in u["lessons"])
    print("完成：%d 个栏目 / %d 课 / %d 页 → %s" %
          (len(out_units), sum(len(u["lessons"]) for u in out_units), total_pages,
           pdfs / "catalog.json"))


if __name__ == "__main__":
    main()
