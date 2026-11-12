#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把网盘下载的兴趣课程原始视频整理进 StudyDaily：
  videos/iXX/NNN.mp4  +  posters/iXX/NNN.jpg  +  interest.json

- 幂等：已存在的视频/海报跳过，可中断重跑
- 海报：ffmpeg 抽第 5 秒一帧，缩放到 640 宽
- 超过 95MB 的视频先压到 720p H.264（GitHub 单文件 100MB 限制）
用法： python tools/build_interest.py --src E:/StudyDaily-interest-src
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

# (本地相对目录, 单元id, 单元名, 标题清理说明)
UNITS = [
    ("美术绘画/幼儿简笔画 快乐画画之怎么画动画人物合集", "i01", "01 简笔画动画人物", "cut-en"),
    ("美术绘画/儿童绘画小课堂", "i02", "02 绘画小课堂", "plain"),
    ("美术绘画/趣味幼儿涂色简笔画", "i03", "03 趣味涂色", "hash"),
    ("情绪品格/儿童心理科普动画片《齐宝和志宝的情绪小故事》", "i04", "04 情绪小故事", "series"),
    ("情绪品格/宝宝巴士儿歌启蒙：好情绪好品格", "i05", "05 好情绪好品格", "plain"),
    ("习惯养成/垃圾分类", "i06", "06 学垃圾分类", "series"),
    ("习惯养成/宝宝巴士之奇妙好习惯20集全", "i07", "07 奇妙好习惯", "series"),
    ("舞蹈", "i08", "08 快乐舞蹈", "dance"),
    ("奥数入门/1 一年级组合数学（1）视频", "i09", "09 奥数 组合数学一", "plain"),
    ("奥数入门/2 一年级组合数学（2）视频", "i10", "10 奥数 组合数学二", "plain"),
    ("奥数入门/3 一年级计算视频", "i11", "11 奥数 计算", "plain"),
    ("奥数入门/4 一年级数字谜视频", "i12", "12 奥数 数字谜", "plain"),
    ("奥数入门/5 一年级应用题视频", "i13", "13 奥数 应用题", "plain"),
    ("奥数入门/6 一年级几何视频", "i14", "14 奥数 几何", "plain"),
    ("奥数入门/7 一年级计数视频", "i15", "15 奥数 计数", "plain"),
    ("情商性格", "i16", "16 情商小故事", "plain"),
]

MAX_UPLOAD_BYTES = 95 * 1024 * 1024  # GitHub 单文件 100MB，留余量


def natural_key(p: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.stem)]


def clean_title(raw: str, mode: str) -> str:
    s = raw
    s = re.sub(r"^\s*\d+\s*[、._\-:：]\s*", "", s)      # 023- / 09. / 01 空格
    s = re.sub(r"^第\s*\d+\s*[集讲節节课时]\s*", "", s)  # 第13集
    if mode == "hash":
        s = re.sub(r"#\S+?#", "", s)                     # #早教美术#
    if mode == "series":
        # 去掉整段重复的系列名（宝宝巴士之学垃圾分类 / 儿童心理科普…）
        s = re.sub(r"^.*?(宝宝巴士之[^\s]*?|儿童心理科普动画片《[^》]*》)\s*", "", s)
        s = re.sub(r"^宝宝巴士之奇妙好习惯\s*第\s*\d+\s*集\s*", "", s)
    if mode == "cut-en":
        s = re.split(r"\s*[-−]?\s*How to draw", s, flags=re.I)[0]
        s = re.sub(r"[（(]\s*简笔画\s*[)）]", "", s)
    if mode == "dance":
        m = re.search(r"《(.+?)》", s)
        if m:
            s = m.group(1)
            extra = re.sub(r".*?》", "", raw).strip(" ，,。")
            if extra and len(extra) <= 14:
                s = s + "（" + extra + "）"
        else:
            s = re.sub(r"^【[^】]*】", "", s)
    s = s.replace("_", "·")
    s = re.sub(r"\s{2,}", " ", s).strip(" .。,，-－")
    return s or raw


def probe_seconds(path: Path):
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=60)
        return max(1, round(float(out.stdout.strip())))
    except Exception:
        return 0


def compress(src: Path, dst: Path):
    """超过 95MB 的视频压到 720p H.264 + AAC"""
    cmd = [FFMPEG, "-y", "-i", str(src), "-vf", "scale=-2:min(720\\,ih)",
           "-c:v", "libx264", "-preset", "medium", "-crf", "26",
           "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(dst)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=3600)


def make_poster(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FFMPEG, "-y", "-ss", "5", "-i", str(src), "-frames:v", "1",
                    "-vf", "scale=640:-2", "-q:v", "5", str(dst)],
                   capture_output=True, timeout=120)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    args = ap.parse_args()
    src_root = Path(args.src)

    videos_dir = HERE / "videos"
    posters_dir = HERE / "posters"
    catalog = {"version": 1, "title": "兴趣课堂", "interestOnly": True, "units": []}
    problems = []

    for rel, uid, uname, mode in UNITS:
        folder = src_root / rel
        if not folder.is_dir():
            problems.append("缺目录：" + rel)
            continue
        files = sorted([p for p in folder.iterdir() if p.suffix.lower() == ".mp4"],
                       key=natural_key)
        if not files:
            problems.append("目录无 mp4：" + rel)
            continue
        out_unit = {"id": uid, "name": uname, "interest": True, "lessons": []}
        dst_dir = videos_dir / uid
        dst_dir.mkdir(parents=True, exist_ok=True)
        (posters_dir / uid).mkdir(parents=True, exist_ok=True)
        for i, f in enumerate(files, 1):
            name = "%03d.mp4" % i
            dst = dst_dir / name
            if not dst.exists():
                if f.stat().st_size > MAX_UPLOAD_BYTES:
                    print(f"  压缩 {f.name} ({f.stat().st_size/1048576:.0f}MB) -> {dst}")
                    compress(f, dst)
                else:
                    shutil.copy2(f, dst)
            dur = probe_seconds(dst) or probe_seconds(f)
            poster = "posters/%s/%s" % (uid, name.replace(".mp4", ".jpg"))
            pj = HERE / poster
            if not pj.exists():
                make_poster(dst, pj)
            title = clean_title(f.stem, mode)
            out_unit["lessons"].append({
                "id": "%s-%03d" % (uid, i),
                "title": title,
                "file": "videos/%s/%s" % (uid, name),
                "durationSec": dur,
                "order": i,
                "poster": poster,
            })
            print(f"  {uid}/{name}  {dur}s  {title}")
        catalog["units"].append(out_unit)

    out = HERE / "interest.json"
    out.write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(len(u["lessons"]) for u in catalog["units"])
    size = sum((HERE / l["file"]).stat().st_size for u in catalog["units"] for l in u["lessons"] if (HERE / l["file"]).exists())
    print(f"\n完成：{len(catalog['units'])} 门 / {total} 集 / {size/1048576:.0f} MB -> interest.json")
    if problems:
        print("问题：")
        for p in problems:
            print("  " + p)


if __name__ == "__main__":
    main()
