#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
生成「学完三小题」题库（quiz.json）

面向 6 岁不识字的孩子，所以每道题都满足三条硬规则：
  1. 题干一定有语音（say 字段），孩子不需要读字；
  2. 选项是大字 / 大图 / 大形状，不是小号文字；
  3. 每道题只有一个正确答案，错两次会把正确答案点亮并重播题干，不扣分不惩罚。

题目不是通用题库，而是按 courses.json 里每一节课的实际内容生成的：
  拼音 → 字形匹配 / 听呼读音选字母 / 形近字母找不同
  识字 → 听音选字 / 看图选字 / 字形匹配
  数学 → 数数 / 加减 / 比较 / 时钟 / 图形 / 规律
  英语 → 听英文找大写 / 找小写 / 元音

用法：
    python tools/build_quiz.py                 # 生成 quiz.json
    python tools/build_quiz.py --per-lesson 2  # 每节 2 题（默认 3）
    python tools/build_quiz.py --check         # 只校验已有 quiz.json
"""
import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

random.seed(20261111)   # 固定随机，重跑结果一致

# ---------------------------------------------------------------- 基础题目构造

def q(say, opts, ans, stage=None, q_text='', lang='zh', kind='choice'):
    return {'say': say, 'q': q_text, 'opts': opts, 'ans': ans,
            'stage': stage, 'lang': lang, 'kind': kind}


def char(c):
    """字卡：有公认图形的字顺手带上配图，答对时补一张小图帮孩子记意思"""
    o = {'t': c, 'k': 'char'}
    e = EMOJI_OF.get(c)
    if e:
        o['e'] = e
    return o


def letter(c):
    return {'t': c, 'k': 'letter'}


def num(n):
    return {'t': str(n), 'k': 'num'}


def emo(e):
    return {'t': e, 'k': 'emoji'}


def shape(s):
    return {'t': s, 'k': 'shape'}


def word(s):
    return {'t': s, 'k': 'text'}


def stage_big(v, cls='char'):
    return {'k': 'big', 'v': v, 'cls': cls}


def stage_emojis(e, n):
    return {'k': 'emojis', 'v': e, 'n': n}


def stage_clock(h, m):
    return {'k': 'clock', 'h': h, 'm': m}


def stage_grid(cells):
    return {'k': 'grid', 'cells': cells}


def stage_seq(items):
    return {'k': 'seq', 'v': items}


def stage_pair(a, b):
    return {'k': 'pair', 'a': a, 'b': b}


def shuffled_yay(correct, wrongs, rng):
    """把正确答案混进干扰项，返回 (opts, ans)"""
    opts = [correct] + list(wrongs)
    rng.shuffle(opts)
    return opts, opts.index(correct)


# ---------------------------------------------------------------- 拼音

# 每节课对应的字母组（按 u01 里的顺序）
PINYIN_GROUPS = {
    1: ['a', 'o', 'e', 'i'],
    2: ['a', 'o', 'e'],
    3: ['i', 'u', 'ü'],
    4: ['b', 'p', 'm', 'f'],
    5: ['d', 't', 'n', 'l'],
    6: ['g', 'k', 'h'],
    7: ['j', 'q', 'x'],
    8: ['z', 'c', 's'],
    9: ['zh', 'ch', 'sh', 'r'],
    10: ['y', 'w'],
    11: ['ai', 'ei', 'ui'],
    12: ['ou', 'iu'],
    13: ['ie', 'üe', 'er'],
    14: ['an', 'en', 'in'],
    15: ['un', 'ün'],
    16: ['ang', 'eng', 'ing', 'ong'],
    17: ['zhi', 'chi', 'shi', 'ri'],
    18: ['zi', 'ci', 'si'],
    19: ['yi', 'wu', 'yu'],
    20: ['ye', 'yue'],
    21: ['yuan', 'yin', 'yun', 'ying'],
    22: ['a', 'b', 'ai', 'zhi'],
}

# 声母呼读音：孩子在学校就是这么念的，所以语音说「波」比说「b」清楚得多
PINYIN_SOUND = {
    'b': '波', 'p': '泼', 'm': '摸', 'f': '佛', 'd': '得', 't': '特',
    'n': '讷', 'l': '勒', 'g': '哥', 'k': '科', 'h': '喝', 'j': '鸡',
    'q': '七', 'x': '西', 'zh': '知', 'ch': '吃', 'sh': '诗', 'r': '日',
    'z': '资', 'c': '次', 's': '思', 'y': '衣', 'w': '乌',
}

# 形近字母：专门用来做「找不同」和干扰项，是幼小衔接最容易混的地方
PINYIN_CONFUSE = {
    'b': ['d', 'p', 'q'], 'd': ['b', 'p', 'q'], 'p': ['q', 'b', 'd'],
    'q': ['p', 'b', 'd'], 'm': ['n', 'w'], 'n': ['m', 'u'], 'u': ['ü', 'n'],
    'ü': ['u', 'u'], 'f': ['t', 'r'], 't': ['f', 'l'], 'e': ['a', 'o'],
    'a': ['o', 'e'], 'o': ['a', 'e'], 'i': ['l', 'j'],
    'ie': ['ei', 'ia'], 'ei': ['ie', 'ai'], 'ui': ['iu', 'ui'],
    'iu': ['ui', 'ou'], 'ou': ['uo', 'ao'], 'ai': ['ia', 'ei'],
    'an': ['ang', 'ai'], 'ang': ['an', 'eng'], 'en': ['eng', 'an'],
    'eng': ['en', 'ong'], 'in': ['ing', 'en'], 'ing': ['in', 'eng'],
    'ong': ['eng', 'ang'], 'un': ['ün', 'en'], 'ün': ['un', 'u'],
    'zh': ['z', 'ch'], 'ch': ['c', 'zh'], 'sh': ['s', 'zh'],
    'z': ['zh', 'c'], 'c': ['ch', 'z'], 's': ['sh', 'z'],
    'zhi': ['zh', 'zi'], 'chi': ['ch', 'ci'], 'shi': ['sh', 'si'],
    'ri': ['r', 'shi'], 'zi': ['zhi', 'ci'], 'ci': ['chi', 'zi'],
    'si': ['shi', 'ci'], 'yi': ['i', 'yu'], 'wu': ['u', 'yu'],
    'yu': ['u', 'ü'], 'ye': ['yi', 'yue'], 'yue': ['ye', 'yu'],
    'yuan': ['yun', 'yan'], 'yin': ['ying', 'in'], 'yun': ['yuan', 'ün'],
    'ying': ['yin', 'ing'], 'g': ['q', 'c'], 'k': ['h', 'x'],
    'h': ['n', 'k'], 'j': ['i', 'q'], 'x': ['s', 'k'], 'r': ['n', 'sh'],
    'y': ['w', 'i'], 'w': ['y', 'm'], 'l': ['t', 'i'],
}

SAY_SAME = '哪一个和上面的一样？'
SAY_ODD = '哪一个和别的不一样？'


def pick_group(idx):
    return PINYIN_GROUPS.get(idx, PINYIN_GROUPS[22])


def build_pinyin(lesson, idx, rng, per):
    group = pick_group(idx)
    out = []

    def fill(target, n=2):
        """干扰项：先取同组字母，再取形近字母，最后随便补，永远不重复"""
        pool = []
        for src in (group, list(PINYIN_CONFUSE.get(target, [])), list(PINYIN_SOUND)):
            for c in src:
                if c != target and c not in pool:
                    pool.append(c)
        rng.shuffle(pool)
        # 形近的排前面
        near = [c for c in PINYIN_CONFUSE.get(target, []) if c != target]
        pool = [c for c in near if c in pool] + [c for c in pool if c not in near]
        return pool[:n]

    # 1) 字形匹配：上面给一个，下面找出一样的（最基础、最不挫败）
    target = group[0]
    wrongs = fill(target, 2)
    if len(wrongs) >= 2:
        opts, ans = shuffled_yay(letter(target), [letter(w) for w in wrongs], rng)
        out.append(q(SAY_SAME, opts, ans, stage=stage_big(target, 'letter'),
                     q_text='找出一样的', kind='same'))

    # 2) 听呼读音选字母（声母有统一呼读音；韵母没有，就再认一次）
    sounds = [(g, PINYIN_SOUND[g]) for g in group if g in PINYIN_SOUND]
    if sounds:
        t, s = sounds[0] if len(sounds) == 1 else sounds[min(1, len(sounds) - 1)]
        pool = fill(t, 2)
        if len(pool) >= 2:
            opts, ans = shuffled_yay(letter(t), [letter(p) for p in pool], rng)
            out.append(q('哪一个是 %s？' % s, opts, ans, q_text='听一听，选字母',
                         kind='sound'))
    elif len(group) > 1:
        t = group[1]
        pool = fill(t, 2)
        if len(pool) >= 2:
            opts, ans = shuffled_yay(letter(t), [letter(p) for p in pool], rng)
            out.append(q(SAY_SAME, opts, ans, stage=stage_big(t, 'letter'),
                         q_text='找出一样的', kind='same'))

    # 3) 找不同（形近字母辨析，最容易混的地方）
    base = group[0]
    odd = next((c for c in PINYIN_CONFUSE.get(base, []) if c != base), None)
    if odd:
        cells = [letter(base), letter(base), letter(odd), letter(base)]
        rng.shuffle(cells)
        ans = next(i for i, c in enumerate(cells) if c['t'] == odd)
        out.append(q(SAY_ODD, cells, ans, q_text='找出不一样的', kind='odd'))

    return out[:per]


# ---------------------------------------------------------------- 识字

# 多音字：孩子读错会被判错，干脆不作为题目的答案或干扰项
POLYPHONE = set('长分干少好会为中相只还空数乐着都得应差强兴重行朝更落尽便处传藏曲转角系结假累难量挑扎卷背卡铺印率圈蒙塞澄剥削血熟露薄吓喝倒期说谁都号种倒发为间看觉漂干和扇缝')

# 有公认图形、孩子一眼能认的字（用于看图选字）
EMOJI_OF = {
    '人': '🧍', '口': '👄', '伞': '☂️', '女': '👩', '子': '👶', '牙': '🦷',
    '龟': '🐢', '火': '🔥', '舌': '👅', '龙': '🐉', '儿': '🧒', '山': '⛰️',
    '刀': '🔪', '叉': '🍴', '竹': '🎋', '心': '❤️', '爪': '🐾', '飞': '🕊️',
    '天': '🌤️', '星': '⭐', '光': '✨', '月': '🌙', '日': '☀️', '门': '🚪',
    '田': '🌾', '左': '👈', '右': '👉', '水': '💧', '风': '🌬️', '云': '☁️',
    '雨': '🌧️', '瓜': '🍉', '木': '🌳', '林': '🌲', '森': '🌳', '草': '🌱',
    '土': '🟫', '井': '🕳️', '苹': '🍎', '果': '🍎', '车': '🚗', '手': '✋',
    '目': '👁️', '耳': '👂', '足': '🦶', '马': '🐴', '妈': '👩', '父': '👨',
    '爷': '👴', '菜': '🥬', '羊': '🐑', '牛': '🐮', '狗': '🐕', '猫': '🐈',
    '肉': '🍖', '衣': '👕', '网': '🕸️', '花': '🌸', '朵': '🌺', '禾': '🌾',
    '苗': '🌱', '麦': '🌾', '书': '📖', '包': '🎒', '布': '🧵', '谷': '🌾',
    '叶': '🍃', '灯': '💡', '虫': '🐛', '米': '🍚', '虎': '🐅', '鱼': '🐟',
    '鸟': '🐦', '鸡': '🐔', '鸭': '🦆', '蛋': '🥚', '蜜': '🍯', '蝶': '🦋',
    '蛇': '🐍', '虾': '🦐', '蚁': '🐜', '蚕': '🐛', '面': '🍜', '饭': '🍚',
    '饼': '🥞', '药': '💊', '床': '🛏️', '窗': '🪟', '鞋': '👟', '帽': '🧢',
    '石': '🪨', '玉': '💎', '金': '🥇', '跑': '🏃', '走': '🚶', '笑': '😄',
    '哭': '😭', '船': '⛵', '桃': '🍑', '树': '🌳', '笔': '✏️', '钟': '🕰️',
    '象': '🐘', '兔': '🐰', '鼠': '🐭', '猪': '🐷', '狼': '🐺', '狮': '🦁',
    '蝶': '🦋', '蜂': '🐝', '雪': '❄️', '雷': '⛈️', '电': '⚡', '弓': '🏹',
    '刀': '🔪', '针': '🪡', '镜': '🪞', '门': '🚪', '窗': '🪟',
}

SAY_WHAT_CHAR = '图片上是什么？选出它的字'
SAY_FIND_CHAR = '哪一个是 %s？'


def lesson_chars(title):
    t = re.sub(r'^\s*\d+\s*', '', str(title)).strip()
    return [c for c in t if '\u4e00' <= c <= '\u9fff']


def build_literacy(lesson, idx, rng, per):
    chars = lesson_chars(lesson['title'])
    safe = [c for c in chars if c not in POLYPHONE]
    if len(safe) < 4:
        safe = chars[:]
    out = []
    used = set()

    # 挑三个「有图、常见」的字当主角，剩下的做干扰项
    pictorial = [c for c in safe if c in EMOJI_OF]
    target_pic = pictorial[0] if pictorial else None

    # 1) 看图选字（最直观，先来一个容易的建立信心）
    if target_pic:
        pool = [c for c in safe if c != target_pic and c not in used]
        rng.shuffle(pool)
        if len(pool) >= 2:
            opts, ans = shuffled_yay(char(target_pic),
                                     [char(p) for p in pool[:2]], rng)
            out.append(q(SAY_WHAT_CHAR, opts, ans,
                         stage=stage_big(EMOJI_OF[target_pic], 'emoji'),
                         q_text='看图，选出这个字', kind='pic'))
            used.add(target_pic)

    # 2) 听音选字 ×2（真正的识字考查：听读音找字）
    rest = [c for c in safe if c not in used]
    rng.shuffle(rest)
    for t in rest[:2]:
        pool = [c for c in safe if c != t and c not in (used & {t})]
        rng.shuffle(pool)
        pool = pool[:2]
        if len(pool) < 2:
            continue
        opts, ans = shuffled_yay(char(t), [char(p) for p in pool], rng)
        out.append(q(SAY_FIND_CHAR % t, opts, ans, q_text='听一听，选出这个字',
                     kind='sound'))
        used.add(t)

    # 3) 不够就补字形匹配
    while len(out) < per and len(safe) >= 3:
        t = next((c for c in safe if c not in used), None)
        if not t:
            break
        pool = [c for c in safe if c != t][:2]
        opts, ans = shuffled_yay(char(t), [char(p) for p in pool], rng)
        out.append(q(SAY_SAME, opts, ans, stage=stage_big(t, 'char'),
                     q_text='找出一样的', kind='same'))
        used.add(t)
    return out[:per]


# ---------------------------------------------------------------- 数学

NUM_EMOJI = ['🍎', '🍓', '🐟', '⭐', '🎈', '🐰', '🍪', '🌻', '🚗', '🍇']


def count_q(n, e, rng):
    opts, ans = shuffled_yay(num(n), [num(x) for x in near_numbers(n, 3)], rng)
    return q('数一数，有几个？', opts, ans, stage=stage_emojis(e, n),
             q_text='数一数', kind='count')


def near_numbers(n, k, lo=0, hi=100):
    """几个错得不太离谱的数字做干扰项"""
    cands = []
    for d in (1, -1, 2, -2, 3, 10, -10, 5, -5):
        v = n + d
        if lo <= v <= hi and v != n:
            cands.append(v)
    out = []
    for c in cands:
        if c not in out:
            out.append(c)
        if len(out) >= k:
            break
    return out


def add_q(a, b, rng):
    r = a + b
    opts, ans = shuffled_yay(num(r), [num(x) for x in near_numbers(r, 3, 0, 20)], rng)
    return q('%d 加 %d 等于几？' % (a, b), opts, ans, q_text='算一算', kind='math')


def sub_q(a, b, rng):
    r = a - b
    opts, ans = shuffled_yay(num(r), [num(x) for x in near_numbers(r, 3, 0, 20)], rng)
    return q('%d 减 %d 等于几？' % (a, b), opts, ans, q_text='算一算', kind='math')


def build_math(lesson, idx, rng, per):
    out = []
    e = NUM_EMOJI[idx % len(NUM_EMOJI)]

    if idx == 1:   # 0-9 认读、书写
        for d in (3, 7, 0):
            opts, ans = shuffled_yay(num(d), [num(x) for x in near_numbers(d, 3, 0, 9)], rng)
            out.append(q('哪一个是 %d？' % d, opts, ans, q_text='找出这个数字'))
    elif idx == 2:  # 整十
        for t in (30, 70, 90):
            pool = [x for x in (10, 20, 40, 50, 60, 80, 90) if x != t][:2]
            opts, ans = shuffled_yay(num(t), [num(x) for x in pool], rng)
            out.append(q('哪一个是 %d？' % t, opts, ans, q_text='找出这个数字'))
    elif idx == 3:  # 5 以内数量对应
        for n in (3, 5, 2):
            out.append(count_q(n, e, rng))
    elif idx == 4:  # 10 以内数量对应
        for n in (4, 7, 9):
            out.append(count_q(n, e, rng))
    elif idx == 5:  # 单数 双数 相邻数
        opts, ans = shuffled_yay(num(6), [num(5), num(7), num(9)], rng)
        out.append(q('哪一个是双数？', opts, ans, q_text='找出双数'))
        opts, ans = shuffled_yay(num(8), [num(4), num(9), num(7)], rng)
        out.append(q('哪一个是双数？', opts, ans, q_text='找出双数'))
        opts, ans = shuffled_yay(num(5), [num(3), num(6), num(7)], rng)
        out.append(q('3 的旁边是几？', opts, ans, q_text='找相邻数'))
    elif idx == 6:  # 序数
        row = ['🐰', '🐻', '🐼', '🦊', '🐯']
        for pos in (3, 5, 1):
            opts, ans = shuffled_yay(emo(row[pos - 1]),
                                     [emo(row[i]) for i in (0, 1, 3) if i != pos - 1], rng)
            out.append(q('从左边数，第 %d 个是谁？' % pos, opts, ans,
                         stage=stage_seq(row), q_text='数一数第几个'))
    elif idx == 7:  # 顺数倒数
        opts, ans = shuffled_yay(num(6), [num(4), num(5), num(7)], rng)
        out.append(q('5 后面是几？', opts, ans, q_text='往后数'))
        opts, ans = shuffled_yay(num(4), [num(5), num(6), num(3)], rng)
        out.append(q('5 前面是几？', opts, ans, q_text='往前数'))
        opts, ans = shuffled_yay(num(3), [num(2), num(4), num(5)], rng)
        out.append(q('倒着数，4、3、2，再往下是几？', opts, ans, q_text='倒着数'))
    elif idx in (8, 9):  # 100 以内认读 / 书写
        for t in (37, 64):
            pool = [x for x in (t + 1, t - 1, t + 10, t - 10, t + 3) if 0 <= x <= 99][:3]
            opts, ans = shuffled_yay(num(t), [num(x) for x in pool], rng)
            out.append(q('哪一个是 %d？' % t, opts, ans, q_text='找出这个数字'))
        opts, ans = shuffled_yay(num(10), [num(1), num(5), num(100)], rng)
        out.append(q('10 个十是多少？', opts, ans, q_text='想一想'))
    elif idx == 10:  # 组成与分解
        for total, part in ((5, 2), (8, 3), (9, 4)):
            rest = total - part
            opts, ans = shuffled_yay(num(rest), [num(x) for x in near_numbers(rest, 3, 0, 10)], rng)
            out.append(q('%d 可以分成 %d 和几？' % (total, part), opts, ans, q_text='分一分'))
    elif idx == 11:  # 10 以内加法
        for a, b in ((2, 3), (4, 5), (3, 6)):
            out.append(add_q(a, b, rng))
    elif idx == 12:  # 10 以内减法
        for a, b in ((5, 2), (8, 3), (9, 6)):
            out.append(sub_q(a, b, rng))
    elif idx == 13:  # 20 以内加法
        for a, b in ((8, 5), (9, 7), (13, 6)):
            out.append(add_q(a, b, rng))
    elif idx == 14:  # 20 以内减法
        for a, b in ((15, 7), (13, 5), (18, 9)):
            out.append(sub_q(a, b, rng))
    elif idx == 15:  # 按群计算
        for step, start in ((2, 2), (2, 8), (5, 5)):
            nxt = start + step
            opts, ans = shuffled_yay(num(nxt), [num(x) for x in near_numbers(nxt, 3, 0, 20)], rng)
            out.append(q('%d 个 %d 个数，%d 后面是几？' % (step, step, start),
                         opts, ans, q_text='按群数'))
    elif idx == 16:  # 时钟整点半点
        for h, m in ((3, 0), (8, 30), (12, 0)):
            opts = [{'t': '%02d:%02d' % (h, m), 'k': 'clock', 'h': h, 'm': m},
                    {'t': '07:00', 'k': 'clock', 'h': 7, 'm': 0},
                    {'t': '03:30', 'k': 'clock', 'h': 3, 'm': 30},
                    {'t': '12:00', 'k': 'clock', 'h': 12, 'm': 0}]
            opts = list({o['t']: o for o in opts}.values())
            rng.shuffle(opts)
            ans = next(i for i, o in enumerate(opts) if o['t'] == '%02d:%02d' % (h, m))
            say = ('%d 点是哪一个钟？' % h) if m == 0 else ('%d 点半是哪一个钟？' % h)
            out.append(q(say, opts, ans, q_text='看钟表'))
    elif idx == 17:  # 看日历
        opts, ans = shuffled_yay(num(7), [num(5), num(6), num(8)], rng)
        out.append(q('一个星期有几天？', opts, ans, q_text='想一想'))
        opts, ans = shuffled_yay(num(14), [num(7), num(10), num(12)], rng)
        out.append(q('两个星期一共几天？', opts, ans, q_text='算一算'))
        opts, ans = shuffled_yay(num(30), [num(7), num(14), num(100)], rng)
        out.append(q('一个月大约有几天？', opts, ans, q_text='想一想'))
    elif idx == 18:  # 方位
        cells = ['', '', '', '', '🐻', '🍎', '', '', '']
        opts, ans = shuffled_yay(emo('➡️'), [emo('⬅️'), emo('⬆️'), emo('⬇️')], rng)
        out.append(q('苹果在小熊的哪一边？', opts, ans,
                     stage={'k': 'grid', 'cells': ['', '', '', '', '🐻', '🍎', '', '', '']},
                     q_text='看方位'))
        opts, ans = shuffled_yay(emo('⬇️'), [emo('⬆️'), emo('⬅️'), emo('➡️')], rng)
        out.append(q('苹果在小熊的哪一边？', opts, ans,
                     stage={'k': 'grid', 'cells': ['', '🍎', '', '', '🐻', '', '', '', '']},
                     q_text='看方位'))
        opts, ans = shuffled_yay(emo('⬆️'), [emo('⬇️'), emo('⬅️'), emo('➡️')], rng)
        out.append(q('苹果在小熊的哪一边？', opts, ans,
                     stage={'k': 'grid', 'cells': ['', '', '', '', '🐻', '', '', '🍎', '']},
                     q_text='看方位'))
    elif idx == 19:  # 圆 三角形 正方形
        opts, ans = shuffled_yay(shape('triangle'),
                                 [shape('circle'), shape('square')], rng)
        out.append(q('哪一个是三角形？', opts, ans, q_text='认图形'))
        opts, ans = shuffled_yay(shape('square'),
                                 [shape('circle'), shape('triangle')], rng)
        out.append(q('哪一个是正方形？', opts, ans, q_text='认图形'))
        opts, ans = shuffled_yay(shape('circle'),
                                 [shape('square'), shape('triangle')], rng)
        out.append(q('哪一个是圆形？', opts, ans, q_text='认图形'))
    elif idx in (20, 21, 22):  # 守恒 / 长方形椭圆梯形 / 图形组合
        if idx == 20:
            opts, ans = shuffled_yay(num(4), [num(3), num(5), num(6)], rng)
            out.append(q('正方形有几条边？', opts, ans, q_text='想一想'))
        else:
            opts, ans = shuffled_yay(shape('rect'), [shape('circle'), shape('triangle')], rng)
            out.append(q('哪一个是长方形？', opts, ans, q_text='认图形'))
            opts, ans = shuffled_yay(shape('oval'), [shape('rect'), shape('triangle')], rng)
            out.append(q('哪一个是椭圆形？', opts, ans, q_text='认图形'))
        opts, ans = shuffled_yay(shape('square'), [shape('triangle'), shape('circle')], rng)
        out.append(q('两个一样的三角形拼起来，像哪个图形？', opts, ans, q_text='拼一拼'))
        opts, ans = shuffled_yay(shape('rect'), [shape('oval'), shape('circle')], rng)
        out.append(q('两个一样的正方形拼起来，像哪个图形？', opts, ans, q_text='拼一拼'))
    elif idx == 23:  # 球 正方体 长方体 圆柱
        opts, ans = shuffled_yay(emo('⚽'), [emo('🎲'), emo('📦'), emo('🥫')], rng)
        out.append(q('哪一个是球？', opts, ans, q_text='认形状'))
        opts, ans = shuffled_yay(emo('🎲'), [emo('⚽'), emo('📦'), emo('🥫')], rng)
        out.append(q('哪一个是正方体？', opts, ans, q_text='认形状'))
        opts, ans = shuffled_yay(emo('🥫'), [emo('⚽'), emo('📦'), emo('🎲')], rng)
        out.append(q('哪一个是圆柱？', opts, ans, q_text='认形状'))
    elif idx == 24:  # 图形等分
        for a, b in ((4, 2), (6, 3), (8, 4)):
            opts, ans = shuffled_yay(num(a // b), [num(x) for x in near_numbers(a // b, 3, 0, 8)], rng)
            out.append(q('%d 个%s平均分给 %d 个人，每人几个？' % (a, e, b), opts, ans,
                         stage=stage_emojis(e, a), q_text='平均分'))
    elif idx == 25:  # 找相同 找不同
        base, odd = '🍎', '🍌'
        opts = [emo(base), emo(base), emo(odd), emo(base)]
        rng.shuffle(opts)
        ans = next(i for i, o in enumerate(opts) if o['t'] == odd)
        out.append(q(SAY_ODD, opts, ans, q_text='找出不一样的', kind='odd'))
        base, odd = '🐟', '🦋'
        opts = [emo(base), emo(odd), emo(base), emo(base)]
        rng.shuffle(opts)
        ans = next(i for i, o in enumerate(opts) if o['t'] == odd)
        out.append(q(SAY_ODD, opts, ans, q_text='找出不一样的', kind='odd'))
        base, odd = '🌻', '🌷'
        opts = [emo(base), emo(base), emo(base), emo(odd)]
        rng.shuffle(opts)
        ans = next(i for i, o in enumerate(opts) if o['t'] == odd)
        out.append(q(SAY_ODD, opts, ans, q_text='找出不一样的', kind='odd'))
    elif idx == 26:  # 按特征排序
        opts, ans = shuffled_yay(num(1), [num(2), num(3), num(4)], rng)
        out.append(q('1、2、3、4 里最小的是几？', opts, ans, q_text='比一比'))
        opts, ans = shuffled_yay(num(4), [num(1), num(2), num(3)], rng)
        out.append(q('1、2、3、4 里最大的是几？', opts, ans, q_text='比一比'))
        opts, ans = shuffled_yay(num(5), [num(2), num(7), num(9)], rng)
        out.append(q('2、5、7、9 里第二小的是几？', opts, ans, q_text='排一排'))
    elif idx == 27:  # 按特征分类
        opts, ans = shuffled_yay(emo('🥕'), [emo('🍎'), emo('🍌'), emo('🍇')], rng)
        out.append(q('哪一个是蔬菜？', opts, ans, q_text='分一分'))
        opts, ans = shuffled_yay(emo('🚗'), [emo('🍎'), emo('🌻'), emo('🐟')], rng)
        out.append(q('哪一个是交通工具？', opts, ans, q_text='分一分'))
        opts, ans = shuffled_yay(emo('🍇'), [emo('🥕'), emo('🥦'), emo('🌽')], rng)
        out.append(q('哪一个是水果？', opts, ans, q_text='分一分'))
    elif idx == 28:  # 按规律排序
        out.append(q('下一个应该是什么？', [emo('🔴'), emo('🔵'), emo('🟡')], 0,
                     stage=stage_seq(['🔴', '🔵', '🔴', '🔵', '❓']), q_text='找规律',
                     kind='pattern'))
        out.append(q('下一个应该是什么？', [emo('🐰'), emo('🐻'), emo('🐼')], 1,
                     stage=stage_seq(['🐻', '🐼', '🐻', '🐼', '❓']), q_text='找规律',
                     kind='pattern'))
        out.append(q('下一个应该是什么？',
                     [emo('⭐'), emo('🌙'), emo('☀️')], 2,
                     stage=stage_seq(['⭐', '🌙', '☀️', '⭐', '🌙', '❓']), q_text='找规律'))
    elif idx == 29:  # 大小长短
        opts, ans = shuffled_yay(emo('🐘'), [emo('🐜')], rng)
        out.append(q('哪一个更大？', opts, ans, stage=stage_pair('🐘', '🐜'), q_text='比一比'))
        opts, ans = shuffled_yay(emo('🐍'), [emo('🐛')], rng)
        out.append(q('哪一个更长？', opts, ans, stage=stage_pair('🐍', '🐛'), q_text='比一比'))
        opts, ans = shuffled_yay(emo('🐜'), [emo('🐘')], rng)
        out.append(q('哪一个更小？', opts, ans, stage=stage_pair('🐘', '🐜'), q_text='比一比'))
    elif idx == 30:  # 粗细厚薄高矮轻重
        opts, ans = shuffled_yay(emo('🪨'), [emo('🪶')], rng)
        out.append(q('哪一个更重？', opts, ans, stage=stage_pair('🪨', '🪶'), q_text='比一比'))
        opts, ans = shuffled_yay(emo('🦒'), [emo('🐜')], rng)
        out.append(q('哪一个更高？', opts, ans, stage=stage_pair('🦒', '🐜'), q_text='比一比'))
        opts, ans = shuffled_yay(emo('🪵'), [emo('📄')], rng)
        out.append(q('哪一个更厚？', opts, ans, stage=stage_pair('🪵', '📄'), q_text='比一比'))
    elif idx == 31:  # 多少比较
        out.append(q('哪边多？', [{'t': '🍎', 'k': 'emojis', 'n': 5},
                                 {'t': '🍎', 'k': 'emojis', 'n': 2}], 0, q_text='比一比'))
        out.append(q('哪边少？', [{'t': '🐟', 'k': 'emojis', 'n': 3},
                                 {'t': '🐟', 'k': 'emojis', 'n': 6}], 0, q_text='比一比'))
        opts, ans = shuffled_yay(num(5), [num(3), num(8), num(2)], rng)
        out.append(q('3 和 5，哪个多？', opts, ans, q_text='比一比'))
    elif idx == 32:  # 测量
        opts, ans = shuffled_yay(num(5), [num(4), num(6), num(10)], rng)
        out.append(q('尺子从 0 量到 5，是几厘米？', opts, ans, q_text='量一量'))
        opts, ans = shuffled_yay(num(3), [num(2), num(4), num(5)], rng)
        out.append(q('3 厘米和 5 厘米，哪个短？', opts, ans, q_text='比一比'))
        opts, ans = shuffled_yay(num(10), [num(5), num(8), num(12)], rng)
        out.append(q('尺子从 0 量到 10，是几厘米？', opts, ans, q_text='量一量'))
    elif idx == 33:  # 量的传递
        opts, ans = shuffled_yay(emo('🦒'), [emo('🐕'), emo('🐜')], rng)
        out.append(q('长颈鹿比小狗高，小狗比蚂蚁高，谁最高？', opts, ans, q_text='想一想'))
        opts, ans = shuffled_yay(emo('🐜'), [emo('🐕'), emo('🦒')], rng)
        out.append(q('长颈鹿比小狗高，小狗比蚂蚁高，谁最矮？', opts, ans, q_text='想一想'))
        opts, ans = shuffled_yay(emo('🐕'), [emo('🐜'), emo('🦒')], rng)
        out.append(q('长颈鹿比小狗高，小狗比蚂蚁高，谁在中间？', opts, ans, q_text='想一想'))
    else:  # 34 长度容积守恒
        opts, ans = shuffled_yay(word('一样多'), [word('变多了'), word('变少了')], rng)
        out.append(q('同样多的水倒进细杯子里，水一样多吗？', opts, ans, q_text='想一想'))
        opts, ans = shuffled_yay(word('一样多'), [word('变多了'), word('变少了')], rng)
        out.append(q('同样多的果汁倒进又高又细的杯子，会变多吗？', opts, ans,
                     q_text='想一想'))

    # 统一补齐：题目不够就用数数补
    while len(out) < per:
        out.append(count_q(rng.randint(2, 9), e, rng))
    return out[:per]


# ---------------------------------------------------------------- 英语

LETTERS = list('ABCDEFGHIJKLMNOPQRSTUVWXYZ')
EN_CONFUSE = {
    'A': ['E', 'H', 'R'], 'B': ['D', 'P', 'R'], 'C': ['G', 'O', 'E'],
    'D': ['B', 'O', 'P'], 'E': ['F', 'B', 'L'], 'F': ['E', 'P', 'T'],
    'G': ['C', 'Q', 'O'], 'H': ['N', 'A', 'M'], 'I': ['J', 'L', 'T'],
    'J': ['I', 'G', 'L'], 'K': ['X', 'R', 'H'], 'L': ['I', 'J', 'T'],
    'M': ['N', 'W', 'H'], 'N': ['M', 'H', 'Z'], 'O': ['Q', 'C', 'D'],
    'P': ['B', 'R', 'D'], 'Q': ['O', 'G', 'P'], 'R': ['P', 'B', 'K'],
    'S': ['Z', 'C', 'G'], 'T': ['I', 'F', 'L'], 'U': ['V', 'O', 'Y'],
    'V': ['U', 'W', 'Y'], 'W': ['M', 'V', 'U'], 'X': ['K', 'Y', 'Z'],
    'Y': ['V', 'X', 'U'], 'Z': ['S', 'N', 'X'],
}


def en_letters(title):
    """从「字母Aa,Bb拼写与认读教学（上）」里取出 A、B"""
    found = re.findall(r'([A-Za-z])\s*([A-Za-z])', title)
    out = []
    for a, b in found:
        if a.upper() == b.upper() and a.isalpha():
            out.append(a.upper())
    if not out:
        out = [c.upper() for c in re.findall(r'[A-Za-z]', title) if c.isalpha()][:2]
    return out[:2]


def build_english(lesson, idx, rng, per):
    title = lesson['title']
    out = []
    if idx == 1:  # 认识 26 个字母
        opts, ans = shuffled_yay(num(26), [num(24), num(28), num(20)], rng)
        out.append(q('一共有几个英文字母？', opts, ans, q_text='想一想'))
        opts, ans = shuffled_yay(letter('A'), [letter('H'), letter('R')], rng)
        out.append(q('Find big A.', opts, ans, q_text='找出大写 A', lang='en', kind='sound'))
        opts, ans = shuffled_yay(letter('a'), [letter('e'), letter('o')], rng)
        out.append(q('Find small a.', opts, ans, q_text='找出小写 a', lang='en', kind='sound'))
        return out[:per]
    if idx == 28:  # 26 字母总复习
        opts, ans = shuffled_yay(letter('Z'), [letter('S'), letter('N')], rng)
        out.append(q('Find big Z.', opts, ans, q_text='找出大写 Z', lang='en', kind='sound'))
        opts, ans = shuffled_yay(letter('q'), [letter('p'), letter('g')], rng)
        out.append(q('Find small q.', opts, ans, q_text='找出小写 q', lang='en', kind='sound'))
        opts, ans = shuffled_yay(letter('M'), [letter('W'), letter('N')], rng)
        out.append(q('Find big M.', opts, ans, q_text='找出大写 M', lang='en', kind='sound'))
        return out[:per]

    letters = en_letters(title) or ['A', 'B']
    big_letters = [l.upper() for l in letters]
    small_letters = [l.lower() for l in letters]

    # 1) 听英文找大写
    t = big_letters[0]
    pool = EN_CONFUSE.get(t, [x for x in LETTERS if x != t])[:2]
    opts, ans = shuffled_yay(letter(t), [letter(p) for p in pool], rng)
    out.append(q('Find big %s.' % t, opts, ans, q_text='找出大写 %s' % t, lang='en', kind='sound'))

    # 2) 找小写（大小写配对，幼小衔接重点）
    s = small_letters[0]
    pool = [p.lower() for p in EN_CONFUSE.get(s.upper(), LETTERS)[:2]]
    opts, ans = shuffled_yay(letter(s), [letter(p) for p in pool], rng)
    out.append(q('Find small %s.' % s, opts, ans, q_text='找出小写 %s' % s, lang='en', kind='sound'))

    # 3) 形近字母找不同 / 第二个字母
    if len(big_letters) > 1:
        t2 = big_letters[1]
        pool = [x for x in EN_CONFUSE.get(t2, LETTERS) if x != t2][:2]
        opts, ans = shuffled_yay(letter(t2), [letter(p) for p in pool], rng)
        out.append(q('Find big %s.' % t2, opts, ans, q_text='找出大写 %s' % t2, lang='en', kind='sound'))
    else:
        odd = [x for x in EN_CONFUSE.get(t, LETTERS) if x != t][0]
        cells = [letter(t), letter(t), letter(odd), letter(t)]
        rng.shuffle(cells)
        ans = next(i for i, c in enumerate(cells) if c['t'] == odd)
        out.append(q('Which one is different?', cells, ans, q_text='找出不一样的', lang='en', kind='odd'))

    return out[:per]


# ---------------------------------------------------------------- 汇总 + 校验

BUILDERS = {
    'u01': build_pinyin,
    'u02': build_literacy,
    'u03': build_math,
    'u04': build_english,
}


def validate(quiz):
    errs = []
    for lid, qs in quiz['lessons'].items():
        if not qs:
            errs.append('%s 没有题目' % lid)
            continue
        for i, item in enumerate(qs, 1):
            tag = '%s#%d' % (lid, i)
            opts = item.get('opts') or []
            if not (2 <= len(opts) <= 4):
                errs.append('%s 选项数量 %d' % (tag, len(opts)))
            if not (0 <= item.get('ans', -1) < len(opts)):
                errs.append('%s 答案下标越界' % tag)
            texts = [(o.get('t'), o.get('n')) for o in opts]
            # 「找不同」题天然是 3 个一样 + 1 个不同，允许重复
            if item.get('kind') != 'odd' and len(set(texts)) != len(texts):
                errs.append('%s 选项重复 %s' % (tag, texts))
            if not str(item.get('say') or '').strip():
                errs.append('%s 没有语音文本' % tag)
            st = item.get('stage')
            if st and st.get('k') == 'big' and st.get('v') == texts[item['ans']] \
                    and item['q'] != '找出一样的':
                # 题目里已经把答案亮出来了
                errs.append('%s 题干显示的就是答案' % tag)
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--catalog', default='courses.json')
    ap.add_argument('--out', default='quiz.json')
    ap.add_argument('--per-lesson', type=int, default=3)
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args()

    cat = json.loads(Path(args.catalog).read_text(encoding='utf-8'))
    if args.check:
        quiz = json.loads(Path(args.out).read_text(encoding='utf-8'))
        errs = validate(quiz)
        print('题目总数：%d，覆盖 %d 节' % (
            sum(len(v) for v in quiz['lessons'].values()), len(quiz['lessons'])))
        print('校验：%s' % ('通过' if not errs else '%d 个问题' % len(errs)))
        for e in errs[:20]:
            print('  -', e)
        return 1 if errs else 0

    rng = random.Random(20261111)
    lessons = {}
    stats = Counter()
    for u in cat.get('units', []):
        uid = u.get('id')
        builder = BUILDERS.get(uid)
        for i, l in enumerate(u.get('lessons', []), 1):
            if not builder:
                continue
            try:
                qs = builder(l, i, rng, args.per_lesson)
            except Exception as e:  # 生成失败不能让整批中断
                print('  [跳过] %s %s (%s)' % (l.get('id'), l.get('title'), e))
                continue
            lessons[l['id']] = qs
            stats[uid] += len(qs)

    quiz = {
        'version': 1,
        'generatedAt': __import__('datetime').datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        'perLesson': args.per_lesson,
        'lessons': lessons,
    }
    Path(args.out).write_text(json.dumps(quiz, ensure_ascii=False, indent=1), encoding='utf-8')

    errs = validate(quiz)
    total = sum(len(v) for v in lessons.values())
    says = {i['say'] for v in lessons.values() for i in v}
    print('生成 %d 道题，覆盖 %d 节课（%s）' % (total, len(lessons), dict(stats)))
    print('唯一语音文本 %d 条' % len(says))
    print('校验：%s' % ('通过' if not errs else '%d 个问题' % len(errs)))
    for e in errs[:20]:
        print('  -', e)
    return 1 if errs else 0


if __name__ == '__main__':
    sys.exit(main())
