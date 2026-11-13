#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把导出的 PDF 课件导入「每日学习」兴趣课堂。

流程：把从网盘「AI 课件」导出的 PDF（可按子文件夹分类）放进一个源目录，运行：
    python tools/import_pdfs.py --src "E:/我的PDF课件"
每个 PDF 会：转成逐页图片（跨端体验一致，手机不用装 PDF 阅读器）→
在 interest.json 里生成一个「课件」栏目（unit），每份 PDF 是一课。
重复运行是安全的：同名 PDF 已导入会跳过。

注意：页面转换依赖 PyMuPDF。当前机器装在豆包 3.12 环境里：
    C:/Users/飞翔/Doubao/chats/2026-09-12/new-chat/voice-pro/installer_files/env/Scripts/python.exe
"""
import argparse
import json
import re
import shutil
import sys
import time
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent   # StudyDaily/
try:
    import fitz  # PyMuPDF
except ImportError:
    print("缺少 PyMuPDF：pip install PyMuPDF（或用装好的豆包 3.12 环境运行本脚本）")
    sys.exit(1)

ZOOM = 1.5          # 渲染倍率，1.5 ≈ 108dpi，识字卡足够清晰且体积小
MAX_PAGES = 200     # 单个 PDF 页数上限，防呆


def slugify(name: str) -> str:
    """中文/符号转成安全的目录名：拼音没有就用 unicode 编码。"""
    name = unicodedata.normalize("NFKC", name).strip()
    name = re.sub(r"[\\/:*?\"<>|\s]+", "-", name)
    return name[:40] or "pdf"


def render_pdf(pdf: Path, out_dir: Path) -> list:
    """把 pdf 逐页渲染成 page-001.jpg...，返回相对 pages 路径列表。"""
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf)
    n = min(doc.page_count, MAX_PAGES)
    paths = []
    for i in range(n):
        out = pages_dir / ("page-%03d.jpg" % (i + 1))
        if not out.exists():                       # 幂等：已渲染的页跳过
            pix = doc[i].get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM))
            pix.save(str(out))
        paths.append(str(out.relative_to(HERE)).replace("\\", "/"))
    doc.close()
    return paths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="PDF 源目录（子文件夹=栏目名）")
    ap.add_argument("--group", default="", help="只有一个平铺目录时，指定栏目名")
    ap.add_argument("--catalog", default=str(HERE / "interest.json"))
    args = ap.parse_args()

    src = Path(args.src)
    if not src.is_dir():
        print("源目录不存在：" + str(src)); sys.exit(1)

    catalog_path = Path(args.catalog)
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    units = catalog.setdefault("units", [])
    existing_titles = {
        l.get("title"): u for u in units for l in u.get("lessons", [])
    }

    # 收集 (栏目名, pdf路径)
    jobs = []
    if args.group:
        jobs += [(args.group, p) for p in sorted(src.glob("*.pdf"))]
    for sub in sorted(src.iterdir()):
        if sub.is_dir():
            jobs += [(sub.name, p) for p in sorted(sub.glob("*.pdf"))]
        elif sub.suffix.lower() == ".pdf":
            jobs.append((args.group or "课件", sub))
    if not jobs:
        print("没找到 PDF 文件"); sys.exit(1)

    added, skipped = 0, 0
    for group, pdf in jobs:
        title = pdf.stem
        if title in existing_titles:
            print("跳过（已导入）：" + title); skipped += 1; continue

        # 栏目：同名存在则复用，否则新建 pNN
        unit = next((u for u in units if u.get("name") == group), None)
        if unit is None:
            uid = "p%02d" % (1 + sum(1 for u in units if u["id"].startswith("p")))
            unit = {"id": uid, "name": group, "interest": True, "lessons": []}
            units.append(unit)

        out_dir = HERE / "pdfs" / (unit["id"] + "-" + slugify(title))
        print("转换：%s (%s)" % (title, pdf.name))
        t0 = time.time()
        pages = render_pdf(pdf, out_dir)
        if not pages:
            print("  空文件，跳过"); continue

        lesson_id = unit["id"] + "-%03d" % (len(unit["lessons"]) + 1)
        lesson = {
            "id": lesson_id,
            "title": title,
            "file": str((out_dir / (pdf.stem + ".pdf"))).replace("\\", "/"),
            "type": "pdf",
            "pages": pages,
            "poster": pages[0],
            "durationSec": 0,
            "order": len(unit["lessons"]) + 1,
        }
        # 原件复制一份进 pdfs/（离线包要带上，原生模式备用）
        if pdf.resolve() != (out_dir / pdf.name).resolve():
            shutil.copy2(pdf, out_dir / pdf.name)
            lesson["file"] = str((out_dir / pdf.name).relative_to(HERE)).replace("\\", "/")
        unit["lessons"].append(lesson)
        existing_titles[title] = unit
        added += 1
        print("  %d 页 / %.1fs" % (len(pages), time.time() - t0))

    catalog_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    print("完成：新增 %d 课，跳过 %d。栏目数 %d" % (added, skipped, len(units)))


if __name__ == "__main__":
    main()
