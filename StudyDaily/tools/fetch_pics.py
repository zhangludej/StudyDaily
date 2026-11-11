#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
下载题目插图（Microsoft Fluent Emoji 3D，MIT 许可，可自由使用）

题库里凡是 emoji，都在界面上换成真实插画（而不是系统字体画的彩色符号）——
同样是 🍎，3D 插画在平板上大图显示时好看得多，也更像「绘本」。

用法：
    python tools/fetch_pics.py              # 缺什么下什么，已存在就跳过
    python tools/fetch_pics.py --force      # 全部重下
    python tools/fetch_pics.py --check      # 只检查清单里有没有缺

产出：
    assets/pics/<slug>.png         192px 透明背景 PNG
    assets/pics/manifest.json      {emoji: 文件名}
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit('缺少 Pillow：先执行  pip install pillow')

CDN = 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/'
UE_JSON = 'https://cdn.jsdelivr.net/npm/unicode-emoji-json@0.6.0/data-by-emoji.json'
SIZE = 192
VS16 = '\ufe0f'
OUT = Path('assets/pics')

# 少数插画在仓库里的目录名和 unicode 名不一致，单独指定
FOLDER = {
    '⛈️': 'Cloud with lightning and rain',
    '☀️': 'Sun',
    '☁️': 'Cloud',
    '⚡': 'High voltage',
    '☂️': 'Umbrella',
    '❤️': 'Red heart',
    '✋': 'Raised hand',
    '✏️': 'Pencil',
    '🕊️': 'Dove',
    '👁️': 'Eye',
    '⛰️': 'Mountain',
    '🕰️': 'Mantelpiece clock',
    '🕳️': 'Hole',
    '🕸️': 'Spider web',
    '🛏️': 'Bed',
    '🥇': '1st place medal',
    '🌤️': 'Sun behind small cloud',
    '🌬️': 'Wind face',
    '🌧️': 'Cloud with rain',
    '🍴': 'Fork and knife',
    '🪞': 'Mirror',
    '🪡': 'Sewing needle',
    '🧍': 'Person standing',
    '🧒': 'Child',
    '👨': 'Man',
    '👩': 'Woman',
    '👴': 'Old man',
    '👶': 'Baby',
}


def used_emoji() -> set:
    """把题库里出现的所有 emoji 收集起来（含识字题的字→图映射）"""
    emoji = set()
    q = Path('quiz.json')
    if q.exists():
        data = json.loads(q.read_text(encoding='utf-8'))

        def walk(o):
            if isinstance(o, str):
                for ch in o:
                    if ord(ch) >= 0x2190 and not ch.isascii() and not ('\u4e00' <= ch <= '\u9fff'):
                        emoji.add(ch)
            elif isinstance(o, dict):
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)

        walk(data)

    # 识字字卡：build_quiz.py 里的字→图映射也要有图
    try:
        sys.path.insert(0, 'tools')
        import build_quiz
        emoji |= set(build_quiz.EMOJI_OF.values())
    except Exception:
        pass

    # 只保留真正的图形字符（去掉标点、箭头留给 CSS 画）
    drop = set('、，。？！：；…—')
    emoji = {e for e in emoji if e not in drop}
    return emoji


def fetch(url: str, timeout=12) -> bytes:
    req = urllib.request.Request(url, headers={'User-Agent': 'StudyDaily/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def one(e: str, meta: dict, force: bool) -> tuple:
    """下一个图，返回 (emoji, slug, err)"""
    info = meta.get(e) or meta.get(e + VS16) or meta.get(e.replace(VS16, ''))
    if not info:
        return (e, '', '没有名称')
    slug = info['slug']
    dest = OUT / (slug + '.png')
    if dest.exists() and dest.stat().st_size > 2000 and not force:
        return (e, slug, None)
    folder = FOLDER.get(e) or (info['name'][0].upper() + info['name'][1:])
    q = urllib.parse.quote(folder)
    # 有肤色的表情（人、手、脚）文件在 Default/3D 下，名字还多一个 _default
    us = [slug, slug.replace('-', '_')]
    urls = []
    for u0 in us:
        urls.append(CDN + q + '/3D/' + u0 + '_3d.png')
        urls.append(CDN + q + '/Default/3D/' + u0 + '_3d_default.png')
    err = ''
    for u in urls:
        for _ in range(2):
            try:
                save_png(fetch(u), dest)
                return (e, slug, None)
            except Exception as ex:
                err = '%s %s' % (folder, type(ex).__name__)
    return (e, slug, err)


def save_png(raw: bytes, dest: Path):
    tmp = dest.with_suffix('.raw.png')
    tmp.write_bytes(raw)
    try:
        im = Image.open(tmp).convert('RGBA')
        im.thumbnail((SIZE, SIZE), Image.LANCZOS)
        im.save(dest, 'PNG', optimize=True)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    emoji = used_emoji()
    print('题库用到 %d 个表情' % len(emoji))

    meta = {}
    try:
        meta = json.loads(fetch(UE_JSON))
    except Exception as e:
        print('取不到表情名称表（%s），改用内置猜测' % type(e).__name__)

    manifest = {}
    miss = []
    todo = []
    for e in sorted(emoji):
        if e.strip() in ('', VS16) or e == VS16:
            continue
        info = meta.get(e) or meta.get(e + VS16) or meta.get(e.replace(VS16, ''))
        if not info:
            miss.append((e, '没有名称'))
            continue
        manifest[e] = info['slug'] + '.png'
        if not args.check:
            todo.append(e)

    if args.check:
        for e in todo:
            if not (OUT / manifest[e]).exists():
                miss.append((e, '缺文件'))
    else:
        done = 0
        with ThreadPoolExecutor(max_workers=10) as pool:
            for e, slug, err in pool.map(lambda x: one(x, meta, args.force), todo):
                done += 1
                if err:
                    miss.append((e, err))
                if done % 20 == 0:
                    print('  进度 %d/%d' % (done, len(todo)))

    if not args.check:
        (OUT / 'manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=0, sort_keys=True),
            encoding='utf-8')

    files = list(OUT.glob('*.png'))
    total = sum(f.stat().st_size for f in files)
    print('\n落地 %d 张插画，共 %.2f MB' % (len(files), total / 1024 / 1024))
    if miss:
        print('未取到 %d 个：' % len(miss))
        for e, why in miss:
            print('   %s  %s' % (e, why))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
