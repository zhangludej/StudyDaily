#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
StudyDaily - 练习册导入工具

把扫描版 PDF 练习册（如「春雨幼教·幼小衔接整合教材」）逐页渲染成 JPG，
放到 StudyDaily/workbooks/<书id>/pNNN.jpg，并生成 workbooks.json 清单。

练习册在应用里是一个「书架」：孩子点开一本书就像翻纸质练习册一样一页页看，
照着屏幕上的题在纸上做。不参与每日排课，随时可看。

    python tools/build_workbooks.py --src "C:/path/练习册文件夹"
    python tools/build_workbooks.py --src ... --width 1000 --quality 72 --force

已渲染过的页会跳过（断点续跑）；--force 全部重画。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def render_page(doc, idx: int, dest: Path, width: int, quality: int) -> bool:
    """渲染第 idx 页（0 起）到 dest，返回是否成功。"""
    import pypdfium2 as pdfium  # noqa: F401  确认可用
    try:
        page = doc[idx]
        pw, ph = page.get_size()
        scale = width / pw
        bitmap = page.render(scale=scale)
        img = bitmap.to_pil()
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(dest, "JPEG", quality=quality, optimize=True)
        page.close()
        return True
    except Exception as e:
        print("  渲染失败 p%03d: %s" % (idx + 1, e))
        return False


def classify(filename: str) -> dict:
    """从文件名猜科目和显示名。"""
    name = Path(filename).stem
    subject = "pinyin"
    if "拼音" in name:
        subject = "pinyin"
    elif "识字" in name or "汉字" in name:
        subject = "hanzi"
    elif "数学" in name:
        subject = "math"
    elif "英语" in name:
        subject = "english"

    vol = ""
    m = re.search(r"([上下])册", name)
    if m:
        vol = "1" if m.group(1) == "上" else "2"
    m2 = re.search(r"[①②③④⑤]", name)
    if m2:
        vol = str("①②③④⑤".index(m2.group(0)) + 1)
    practice = "1日1练" in name

    # 显示名去掉品牌前缀，孩子和家长看到的是短名
    short = re.sub(r"^春雨幼教[·・]?", "", name).replace("幼小衔接", "").strip()
    short = short.replace("整合教材", "").replace("1日1练", "每日一练").strip()
    return {
        "subject": subject,
        "practice": practice,
        "vol": vol,
        "title": short or name,
        "say": short or name,  # 语音念的书名
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="练习册 PDF 所在文件夹")
    ap.add_argument("--out", default=str(ROOT))
    ap.add_argument("--width", type=int, default=1000, help="页面渲染宽度（默认 1000px）")
    ap.add_argument("--quality", type=int, default=72, help="JPEG 质量（默认 72）")
    ap.add_argument("--force", action="store_true", help="重画已存在的页")
    args = ap.parse_args()

    src_dir = Path(args.src)
    if not src_dir.is_dir():
        sys.exit("找不到目录：%s" % src_dir)
    pdfs = sorted(src_dir.glob("*.pdf"))
    if not pdfs:
        sys.exit("目录里没有 PDF：%s" % src_dir)

    root = Path(args.out)
    wb_dir = root / "workbooks"
    wb_dir.mkdir(parents=True, exist_ok=True)

    SUBJ_ORDER = {"pinyin": 0, "hanzi": 1, "math": 2, "english": 3}
    SUBJ_ICON = {"pinyin": "🗣", "hanzi": "📖", "math": "🔢", "english": "🌍"}

    books = []
    total_pages = 0
    total_bytes = 0

    import pypdfium2 as pdfium

    for pdf in pdfs:
        meta = classify(pdf.name)
        doc = pdfium.PdfDocument(str(pdf))
        n = len(doc)

        # 稳定的书 id：科目 + 练习册/教材 + 册号，纯 ASCII（安卓与 URL 安全）
        if not meta["vol"]:
            meta["vol"] = "1"  # 没标册次的按第一册
        bid = meta["subject"] + ("-p" if meta["practice"] else "-t") + meta["vol"]
        book_dir = wb_dir / bid
        book_dir.mkdir(parents=True, exist_ok=True)

        done = 0
        for i in range(n):
            dest = book_dir / ("p%03d.jpg" % (i + 1))
            if dest.exists() and dest.stat().st_size > 4000 and not args.force:
                done += 1
                continue
            if render_page(doc, i, dest, args.width, args.quality):
                done += 1
        doc.close()

        pages = sorted(book_dir.glob("p*.jpg"))
        size = sum(p.stat().st_size for p in pages)
        total_pages += len(pages)
        total_bytes += size

        books.append({
            "id": bid,
            "title": meta["title"],
            "say": meta["say"],
            "subject": meta["subject"],
            "icon": SUBJ_ICON[meta["subject"]],
            "pages": len(pages),
            "folder": "workbooks/" + bid,
            "cover": "workbooks/%s/p001.jpg" % bid,
        })
        print("%-30s -> %s  %3d/%3d 页  %6.1f MB" % (
            meta["title"], bid, done, n, size / 1048576))

    books.sort(key=lambda b: (SUBJ_ORDER[b["subject"]], b["title"]))

    manifest = {
        "version": 1,
        "books": books,
        "totalPages": total_pages,
    }
    out_json = root / "workbooks.json"
    out_json.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n共 %d 本 / %d 页 / %.1f MB" % (
        len(books), total_pages, total_bytes / 1048576))
    print("清单：%s" % out_json)


if __name__ == "__main__":
    main()
