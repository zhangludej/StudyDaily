#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
生成孩子界面的语音片段（离线打包，不依赖平板 TTS 引擎）

用法（只需在电脑上跑一次，需要联网）：
    pip install edge-tts
    python tools/make_voice.py                 # 生成到 voice/
    python tools/make_voice.py --voice zh-CN-XiaoyiNeural --rate -12%
    python tools/make_voice.py --force         # 重新生成已有片段

产出：
    voice/v_<key>.mp3        每句一个音频
    voice/index.json         {phrases: {原文: 文件名}, lessons: {课id: 文件名}}

为什么预生成而不是用系统 TTS：
    1. 安卓 WebView 不带 speechSynthesis，App 里系统 TTS 要靠原生桥，部分精简平板没有中文引擎；
    2. 预生成音色统一、语速可控，孩子听着不突兀；
    3. 完全离线，公共场合关掉即可。
"""
import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path

try:
    import edge_tts
except ImportError:
    sys.exit("缺少 edge-tts：先执行  pip install edge-tts")

DEFAULT_VOICE = "zh-CN-XiaoyiNeural"   # 温柔女声，比播报腔更适合孩子
DEFAULT_RATE = "-12%"                   # 放慢一点，6 岁孩子听得清
EN_VOICE = "en-US-AnaNeural"            # 英语题用童声读字母，和孩子的发音接近
EN_RATE = "-8%"


def unit_of(lesson: dict) -> str:
    return re.sub(r"^\d+\s*", "", str(lesson.get("unitName") or ""))


def lesson_text(lesson: dict) -> str:
    """与前端 lessonSpeech() 保持完全一致，否则命中不到片段"""
    title = str(lesson.get("title") or "").replace(",", "，")
    return "这一节是" + unit_of(lesson) + "，" + title


def build_phrases(catalog: dict) -> dict:
    """key -> 要朗读的原文。key 全部是 ASCII，避免文件名出问题"""
    p = {
        "newday": "新的一天，今天的任务已经排好啦",
        "guide": "我是小熊。点下面的大按钮，我们一起学习吧",
        "alldone": "今天的任务全部完成，你真厉害！",
        "pdfend": "看完了，点绿色的按钮",
        "empty": "今天没有新课，好好休息吧",
        "again": "没关系，明天再看一次",
        "start": "我们开始吧",
        "look": "看看今天学什么",
        # —— 学完做题（与 app.js 的 QUIZ_SAY 一字不差） ——
        "quintro": "学完啦，来做几个小题目吧",
        "quok": "答对啦，真棒！",
        "quretry": "再试一次，仔细听",
        "qushow": "没关系，记住它，下次就会了",
        "quperfect": "全对，太厉害啦！",
        "qudone": "做完啦，你很努力！",
        "quskip": "先不做题，我们继续",
        "wbshelf": "这里是练习册，选一本开始吧",
        "wbopen": "翻开啦，点下面的箭头翻页",
        "wbend": "这是最后一页啦",
        "wbback": "回到书架啦",
        "quone": "第一题",
        "qutwo": "第二题",
        "quthree": "第三题",
    }
    # 今天有 N 节课 / 还剩 N 节
    for n in range(1, 13):
        p["n%02d" % n] = "今天有 %d 节课" % n
        p["r%02d" % n] = "还剩 %d 节" % n
    # 表扬 + 继续/休息（组合成整句，避免两句拼接的停顿不自然）
    praises = ["太棒了！", "真厉害！", "好样的！", "你真棒！", "做得很好！"]
    for i, pr in enumerate(praises):
        body = pr.rstrip("！")
        p["c%dg" % i] = body + "，我们继续"
        p["c%dr" % i] = body + "，休息一下吧"
    return p


def collect_lessons(catalog: dict) -> dict:
    out = {}
    for u in catalog.get("units", []):
        for l in u.get("lessons", []):
            lid = str(l.get("id") or "")
            if not lid:
                continue
            merged = dict(l)
            merged.setdefault("unitName", u.get("name"))
            out["l_" + lid] = lesson_text(merged)
    return out


def collect_quiz(path: Path, skip: set) -> dict:
    """题库里所有题干都要有语音，否则孩子听不懂题。
    同一句只生成一次（题库里「找出一样的」会出现几十次），用文本哈希做文件名。"""
    if not path.exists():
        print("（没有 quiz.json，跳过题库语音）")
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for qs in data.get("lessons", {}).values():
        for item in qs:
            text = str(item.get("say") or "").strip()
            if not text or text in skip:
                continue
            lang = "en" if item.get("lang") == "en" else "zh"
            key = "qk" + hashlib.sha1((lang + "|" + text).encode("utf-8")).hexdigest()[:10]
            out[key] = (text, lang)
            skip.add(text)
    return out


def collect_workbooks(path: Path, skip: set) -> dict:
    """练习册书名也要能念出来（孩子不识字，靠听和封面认书）"""
    if not path.exists():
        print("（没有 workbooks.json，跳过练习册语音）")
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for b in data.get("books", []):
        t = str(b.get("say") or "").strip()
        if not t or t in skip:
            continue
        out["wb_" + str(b.get("id", ""))] = (t, "zh")
        skip.add(t)
    return out


async def synth(text: str, path: Path, voice: str, rate: str, sem: asyncio.Semaphore) -> bool:
    if path.exists() and path.stat().st_size > 800:
        return True
    for attempt in range(3):
        async with sem:
            try:
                c = edge_tts.Communicate(text, voice, rate=rate)
                await c.save(str(path))
                if path.exists() and path.stat().st_size > 800:
                    return True
            except Exception as e:  # 网络抖动重试
                if attempt == 2:
                    print("  [失败] %-14s %s (%s)" % (path.stem, text[:18], type(e).__name__))
        await asyncio.sleep(0.6 * (attempt + 1))
    return False


async def main_async(args, jobs: dict, out_dir: Path):
    sem = asyncio.Semaphore(args.concurrency)
    tasks = []
    for key, (text, lang) in jobs.items():
        p = out_dir / ("v_%s.mp3" % key)
        if p.exists() and args.force:
            p.unlink()
        voice, rate = (args.voice, args.rate) if lang == "zh" else (EN_VOICE, EN_RATE)
        tasks.append(synth(text, p, voice, rate, sem))
    done = 0
    for i, fut in enumerate(asyncio.as_completed(tasks), 1):
        if await fut:
            done += 1
        if i % 25 == 0 or i == len(tasks):
            print("  进度 %d/%d，成功 %d" % (i, len(tasks), done))
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default=DEFAULT_VOICE)
    ap.add_argument("--rate", default=DEFAULT_RATE)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--catalog", default="courses.json")
    ap.add_argument("--quiz", default="quiz.json")
    ap.add_argument("--workbooks", default="workbooks.json")
    ap.add_argument("--out", default="voice")
    args = ap.parse_args()

    cat_path = Path(args.catalog)
    if not cat_path.exists():
        sys.exit("找不到 %s，请先在项目根目录运行" % cat_path)
    catalog = json.loads(cat_path.read_text(encoding="utf-8"))

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    phrases = build_phrases(catalog)
    lessons = collect_lessons(catalog)
    jobs = {}
    skip = set()
    for key, text in phrases.items():
        jobs[key] = (text, "zh")
        skip.add(text)
    for key, text in lessons.items():
        if text in skip:
            continue
        jobs[key] = (text, "zh")
        skip.add(text)
    quiz = collect_quiz(Path(args.quiz), skip)
    jobs.update(quiz)
    workbooks = collect_workbooks(Path(args.workbooks), skip)
    jobs.update(workbooks)

    print("音色：%s  语速：%s（英语题用 %s）" % (args.voice, args.rate, EN_VOICE))
    print("待生成：%d 句（固定 %d + 课程 %d + 题目 %d + 练习册 %d）" % (
        len(jobs), len(phrases), len(lessons), len(quiz), len(workbooks)))

    asyncio.run(main_async(args, jobs, out_dir))

    # index.json：原文 -> 文件（前端 Speech.say 直接查表，中英文同一个表）
    index = {"voice": args.voice, "phrases": {}, "lessons": {}, "quiz": 0}
    for key, (text, lang) in jobs.items():
        f = out_dir / ("v_%s.mp3" % key)
        if not f.exists():
            continue
        if key.startswith("l_"):
            index["lessons"][key[2:]] = f.name
        else:
            index["phrases"][text] = f.name
            if key.startswith("qk"):
                index["quiz"] += 1
    (out_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")

    files = list(out_dir.glob("*.mp3"))
    total = sum(f.stat().st_size for f in files)
    print("\n完成：%d 个片段，共 %.2f MB" % (len(files), total / 1024 / 1024))
    print("索引：%s（固定 %d 句 / 课程 %d 节 / 题目 %d 句）" % (
        out_dir / "index.json", len(index["phrases"]) - index["quiz"],
        len(index["lessons"]), index["quiz"]))


if __name__ == "__main__":
    main()
