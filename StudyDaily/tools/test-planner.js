/*
 * 排课引擎单元测试（Node 直接跑：node tools/test-planner.js）
 * 覆盖：周课表（周一/周三 识字+数学、周二 识字+拼音、周四 识字+英语、
 *       周五轮换、周六自由拓展、周日只复习）、平日/周末预算、整节不拆、
 *       科目内部不跳课、顺延不追赶、复习到期、"没学会"次日重看、超长单节防卡死
 *
 * 测试用周：2026-11-09(一) ~ 2026-11-15(日)
 */
const assert = require('assert');
const P = require('../planner.js');

const M = '2026-11-09'; // 周一 识字+数学
const T = '2026-11-10'; // 周二 识字+拼音
const W = '2026-11-11'; // 周三 识字+数学
const TH = '2026-11-12'; // 周四 识字+英语
const F = '2026-11-13'; // 周五 识字+轮换
const SA = '2026-11-14'; // 周六 自由拓展
const SU = '2026-11-15'; // 周日 休息

/** 四科课程表：u01 拼音(p) / u02 识字(h) / u03 数学(m) / u04 英语(e) */
function catalog4(opts) {
  opts = opts || {};
  function unit(id, name, n, mins) {
    const lessons = [];
    for (let i = 1; i <= n; i++) {
      const m = Array.isArray(mins) ? mins[i - 1] : (mins || 8);
      lessons.push({
        id: id + '-' + i,
        title: name + '第' + i + '课',
        file: 'videos/' + id + '/' + String(i).padStart(3, '0') + '.mp4',
        durationSec: m * 60,
        order: i
      });
    }
    return { id, name, lessons };
  }
  const units = [];
  if (opts.noPinyin !== true) units.push(unit('u01', '拼音', opts.pinyin != null ? opts.pinyin : 6));
  if (opts.noLiteracy !== true) units.push(unit('u02', '识字', opts.literacy != null ? opts.literacy : 6, opts.literacyMins));
  if (opts.noMath !== true) units.push(unit('u03', '数学', opts.math != null ? opts.math : 6, opts.mathMins));
  if (opts.noEnglish !== true) units.push(unit('u04', '英语', opts.english != null ? opts.english : 6));
  return { title: '测试课程', units };
}

let passed = 0;
function test(name, fn) {
  fn();
  passed++;
  console.log('  PASS  ' + name);
}

function subjectsOf(plan) {
  return plan.items.map(i => i.id);
}

console.log('StudyDaily 排课引擎测试\n');

// ---------- 周课表 ----------

test('周一：识字+数学 轮流交替装箱，整节不拆', () => {
  const c = catalog4();
  const r = P.buildPlan({}, c, M);
  assert.strictEqual(r.created, true);
  assert.strictEqual(r.state.plan.budgetMin, 30);
  assert.strictEqual(r.state.plan.theme, '识字阅读 + 数学');
  // 8+8+8=24，下一节 8 分装不下（32>30）
  assert.deepStrictEqual(subjectsOf(r.state.plan), ['u02-1', 'u03-1', 'u02-2']);
  assert.strictEqual(r.state.plan.plannedMin, 24);
});

test('周二：识字+拼音（英语不出现，防音混淆）', () => {
  const c = catalog4();
  const r = P.buildPlan({}, c, T);
  assert.strictEqual(r.state.plan.theme, '识字阅读 + 拼音');
  const ids = subjectsOf(r.state.plan);
  assert.ok(!ids.some(id => id[0] === 'u04'), '周二不应排英语');
  assert.ok(!ids.some(id => id[0] === 'u03'), '周二不应排数学');
  assert.deepStrictEqual(ids, ['u02-1', 'u01-1', 'u02-2']);
});

test('周三：识字+数学（同周一）', () => {
  const c = catalog4();
  const r = P.buildPlan({}, c, W);
  assert.deepStrictEqual(subjectsOf(r.state.plan), ['u02-1', 'u03-1', 'u02-2']);
});

test('周四：识字+英语（拼音不出现）', () => {
  const c = catalog4();
  const r = P.buildPlan({}, c, TH);
  assert.strictEqual(r.state.plan.theme, '识字阅读 + 英语');
  const ids = subjectsOf(r.state.plan);
  assert.ok(!ids.some(id => id[0] === 'u01'), '周四不应排拼音');
  assert.deepStrictEqual(ids, ['u02-1', 'u04-1', 'u02-2']);
});

test('周五：auto 隔周轮换；固定 math / pinyin 生效', () => {
  const c = catalog4();
  // auto：相邻两个周五的第二科必须不同
  const a1 = P.resolveSubjects(F, P.defaults());
  const a2 = P.resolveSubjects('2026-11-20', P.defaults());
  assert.strictEqual(a1.length, 2);
  assert.strictEqual(a1[0], 'literacy');
  assert.notStrictEqual(a1[1], a2[1], '相邻两周周五轮换科目应不同');
  assert.ok(a1[1] === 'math' || a1[1] === 'pinyin');
  // 固定数学
  let s = P.normalize({}, c);
  s.settings.fridayFocus = 'math';
  s = P.buildPlan(s, c, F).state;
  assert.strictEqual(s.plan.theme, '识字阅读 + 数学');
  assert.deepStrictEqual(subjectsOf(s.plan), ['u02-1', 'u03-1', 'u02-2']);
  // 固定拼音
  s = P.normalize({}, c);
  s.settings.fridayFocus = 'pinyin';
  s = P.buildPlan(s, c, F).state;
  assert.strictEqual(s.plan.theme, '识字阅读 + 拼音');
  assert.deepStrictEqual(subjectsOf(s.plan), ['u02-1', 'u01-1', 'u02-2']);
});

test('周六：自由拓展不限科目，周末 15 分钟只排 1 节', () => {
  const c = catalog4();
  assert.strictEqual(P.resolveSubjects(SA, P.defaults()), null, '周六返回 null = 不限科目');
  const r = P.buildPlan({}, c, SA);
  assert.strictEqual(r.state.plan.isWeekend, true);
  assert.strictEqual(r.state.plan.budgetMin, 15);
  assert.strictEqual(r.state.plan.theme, '自由拓展');
  assert.strictEqual(r.state.plan.items.length, 1);
  assert.strictEqual(r.state.plan.items[0].id, 'u01-1', '按课程自然顺序先排拼音');
});

test('周日：休息日只复习，不排新课、不带顺延', () => {
  const c = catalog4();
  let s = P.normalize({}, c);
  s.settings.reviewEnabled = true;
  s.lessons['u01-1'] = {
    status: 'done', watchedSec: 0, positionSec: 0, completedAt: '2026-11-12',
    reviewStage: 0, nextReviewAt: SU, failedCount: 0
  };
  // 假设周六有没学完的课：周日不应顺延
  s.plan = {
    date: SA, budgetMin: 15, plannedMin: 8, isWeekend: true,
    items: [{ id: 'u02-1', type: 'new', origin: 'new', minutes: 8, done: false }]
  };
  s = P.buildPlan(s, c, SU).state;
  assert.strictEqual(s.plan.rest, true);
  assert.strictEqual(s.plan.theme, '休息日');
  assert.ok(s.plan.items.some(i => i.id === 'u01-1' && i.type === 'review'), '休息日保留到期复习（复盘）');
  assert.ok(!s.plan.items.some(i => i.id === 'u02-1'), '周日不带顺延的新课');
  assert.ok(!s.plan.items.some(i => i.type === 'new'), '周日不排任何新课');
});

test('周日：无到期复习时完全休息', () => {
  const c = catalog4();
  const r = P.buildPlan({}, c, SU);
  assert.strictEqual(r.state.plan.items.length, 0);
  assert.strictEqual(r.state.plan.rest, true);
});

test('科目当天全学完则自然休息（不留空转）', () => {
  const c = catalog4({ pinyin: 0, english: 0 });
  let s = P.normalize({}, c);
  // 识字和数学全部学完
  Object.keys(P.lessonMap(c)).forEach(id => {
    s.lessons[id] = {
      status: 'done', watchedSec: 0, positionSec: 0, completedAt: '2026-11-08',
      reviewStage: 3, nextReviewAt: null, failedCount: 0
    };
  });
  s = P.buildPlan(s, c, M).state;
  assert.strictEqual(s.plan.items.length, 0, '当天科目都学完，今天就休息');
});

// ---------- 顺延 / 复习 ----------

test('顺延：昨日未完成原序置顶（可跨科目），预算恒定不追赶', () => {
  const c = catalog4();
  let s = P.buildPlan({}, c, M).state; // [h1, m1, h2]
  s = P.completeItem(s, c, 'u02-1', 'good', M); // 完成 h1，剩 m1、h2 欠账
  s = P.buildPlan(s, c, T).state;
  const items = s.plan.items;
  assert.strictEqual(s.plan.budgetMin, 30, '预算不因欠账增加');
  assert.strictEqual(items[0].id, 'u03-1');
  assert.strictEqual(items[0].type, 'carry');
  assert.strictEqual(items[1].id, 'u02-2');
  assert.strictEqual(items[1].type, 'carry');
  // 顺延 16 分 + 搭配强排拼音 8 分 = 24，复习再进 8 分 = 32：
  // 搭配与保底复习允许最多超出预算 10 分钟
  assert.ok(items.some(i => i.id === 'u02-1' && i.type === 'review'), 'h1 次日到期复习');
  assert.ok(s.plan.plannedMin <= 30 + 10, '超预算不超过搭配弹性（10 分钟）');
});

test('学完进入 1 天后的复习队列', () => {
  const c = catalog4();
  let s = P.buildPlan({}, c, M).state;
  s = P.completeItem(s, c, 'u02-1', 'good', M);
  assert.strictEqual(s.lessons['u02-1'].status, 'done');
  assert.strictEqual(s.lessons['u02-1'].nextReviewAt, T);
  s = P.buildPlan(s, c, T).state;
  assert.ok(s.plan.items.some(i => i.id === 'u02-1' && i.type === 'review'), '次日应出现复习项');
});

test('复习"会了"推进到 3 天后；"没学会"次日重看', () => {
  const c = catalog4();
  let s = P.normalize({}, c);
  s.lessons['u02-1'] = {
    status: 'done', watchedSec: 0, positionSec: 0, completedAt: '2026-11-12',
    reviewStage: 0, nextReviewAt: F, failedCount: 0
  };
  s = P.buildPlan(s, c, F).state;
  s = P.completeItem(s, c, 'u02-1', 'good', F);
  assert.strictEqual(s.lessons['u02-1'].reviewStage, 1);
  assert.strictEqual(s.lessons['u02-1'].nextReviewAt, '2026-11-16'); // +3

  s = P.normalize({}, c);
  s.lessons['u02-1'] = {
    status: 'done', watchedSec: 0, positionSec: 0, completedAt: '2026-11-12',
    reviewStage: 0, nextReviewAt: F, failedCount: 0
  };
  s = P.buildPlan(s, c, F).state;
  s = P.completeItem(s, c, 'u02-1', 'again', F);
  assert.strictEqual(s.lessons['u02-1'].reviewStage, 0);
  assert.strictEqual(s.lessons['u02-1'].failedCount, 1);
  assert.strictEqual(s.lessons['u02-1'].nextReviewAt, SA);
});

test('复习每天最多 2 节', () => {
  const c = catalog4();
  let s = P.normalize({}, c);
  ['u01-1', 'u01-2', 'u02-1', 'u02-2'].forEach(id => {
    s.lessons[id] = {
      status: 'done', watchedSec: 0, positionSec: 0, completedAt: '2026-11-12',
      reviewStage: 0, nextReviewAt: TH, failedCount: 0
    };
  });
  s = P.buildPlan(s, c, TH).state;
  const reviews = s.plan.items.filter(i => i.type === 'review');
  assert.strictEqual(reviews.length, 2);
});

test('同一天重复生成不会重排', () => {
  const c = catalog4();
  let s = P.buildPlan({}, c, M).state;
  const before = JSON.stringify(s.plan.items);
  s = P.buildPlan(s, c, M).state;
  assert.strictEqual(JSON.stringify(s.plan.items), before);
});

// ---------- 顺序与保护 ----------

test('科目内部不跳课：下一节装不下时不越过它', () => {
  // 数学课时长 [5,5,5,5,5,8,6,4]：第 6 节 8 分装不下（25+8=33>30），
  // 第 7 节 6 分、第 8 节 4 分更短——不能跳过第 6 节去排第 7/8 节。
  const c = catalog4({
    pinyin: 0, english: 0, literacy: 0, math: 8,
    mathMins: [5, 5, 5, 5, 5, 8, 6, 4]
  });
  const s = P.buildPlan({}, c, M).state; // 周一允许数学
  assert.deepStrictEqual(
    subjectsOf(s.plan),
    ['u03-1', 'u03-2', 'u03-3', 'u03-4', 'u03-5'],
    '装不下就该停在第 5 节，而不是跳到更短的第 7/8 节'
  );
  assert.ok(s.plan.plannedMin <= 30);
});

test('家长跳过：从今日移除且以后不再排', () => {
  const c = catalog4();
  let s = P.buildPlan({}, c, M).state;
  s = P.skipItem(s, c, 'u02-1', M);
  assert.ok(!s.plan.items.some(i => i.id === 'u02-1'));
  s = P.buildPlan(s, c, T).state;
  assert.ok(!s.plan.items.some(i => i.id === 'u02-1'));
});

test('超长单节（40 分钟 > 30 预算）不会永远排不上', () => {
  const c = catalog4({ pinyin: 0, math: 0, english: 0, literacy: 1, literacyMins: 40 });
  const s = P.buildPlan({}, c, M).state;
  assert.strictEqual(s.plan.items.length, 1, '超长课独占当天');
  assert.strictEqual(s.plan.items[0].minutes, 40);
});

test('周末顺延占满 15 分钟预算时，到期复习仍至少保留一节', () => {
  const c = catalog4();
  let s = P.normalize({}, c);
  s.lessons['u01-1'] = {
    status: 'done', watchedSec: 0, positionSec: 0, completedAt: '2026-11-12',
    reviewStage: 0, nextReviewAt: SA, failedCount: 0
  };
  s.plan = {
    date: F, budgetMin: 30, plannedMin: 16, isWeekend: false,
    items: [
      { id: 'u02-2', type: 'new', origin: 'new', minutes: 8, done: false },
      { id: 'u03-1', type: 'new', origin: 'new', minutes: 8, done: false }
    ]
  };
  s = P.buildPlan(s, c, SA).state; // 周六，15 分钟，顺延已 16 分
  assert.ok(s.plan.items.some(i => i.type === 'review' && i.id === 'u01-1'),
    '复习不能被顺延无限饿死');
});

// ---------- 统计 / 混排 ----------

test('统计与热力图可用', () => {
  const c = catalog4();
  let s = P.buildPlan({}, c, M).state;
  s = P.completeItem(s, c, 'u02-1', 'good', M);
  const st = P.stats(s, c);
  assert.strictEqual(st.total, 24);
  assert.strictEqual(st.done, 1);
  const cells = P.heatmap(s, 12, M);
  assert.strictEqual(cells.length, 84);
  assert.ok(cells.some(x => x.date === M && x.count === 1));
});

test('视频与 PDF 混排：type/pages 透传，PDF 按自身时长参与装箱与复习', () => {
  const c = { units: [{ id: 'u02', name: '识字', lessons: [
    { id: 'u02-1', title: '山', file: 'videos/u02/001.mp4', durationSec: 420, order: 1 },
    { id: 'u02-2', title: '描红练习', type: 'pdf', file: 'files/u02/002.pdf',
      pages: ['pages/u02/002/p001.jpg', 'pages/u02/002/p002.jpg'], pageCount: 2,
      durationSec: 300, order: 2 },
    { id: 'u02-3', title: '水', file: 'videos/u02/003.mp4', durationSec: 480, order: 3 }
  ] }] };
  const list = P.flatLessons(c);
  assert.strictEqual(list[0].type, 'video');
  assert.strictEqual(list[1].type, 'pdf');
  assert.strictEqual(list[1].pages.length, 2);
  assert.strictEqual(list[1].pageCount, 2);
  assert.strictEqual(list[0].subject, 'literacy', '单元名"识字"应识别为识字科目');
  let s = P.buildPlan({}, c, M).state;
  assert.strictEqual(s.plan.items.length, 3);
  assert.strictEqual(s.plan.plannedMin, 20, '7 + 5 + 8 分钟');
  s = P.completeItem(s, c, 'u02-2', 'good', M);
  assert.strictEqual(s.lessons['u02-2'].status, 'done');
  assert.strictEqual(s.lessons['u02-2'].nextReviewAt, T);
});

test('科目识别：单元名关键词映射', () => {
  assert.strictEqual(P.subjectOfName('01 拼音'), 'pinyin');
  assert.strictEqual(P.subjectOfName('02 识字'), 'literacy');
  assert.strictEqual(P.subjectOfName('03 数学'), 'math');
  assert.strictEqual(P.subjectOfName('04 英语'), 'english');
  assert.strictEqual(P.subjectOfName('自定义内容'), null);
});

// ---------- 搭配保证 / 复习不堆积 / 版本重建 ----------

test('搭配保证：第二科课太长时也必须出现（允许小幅超预算）', () => {
  // 识字 12 分钟 ×3、数学 23 分钟 ×1，预算 30：
  // 老逻辑会排成 [识1, 识2] 把数学挤掉；现在必须 [识1, 数1] 两科都出现
  const c = catalog4({
    pinyin: 0, english: 0, literacy: 3, literacyMins: 12,
    math: 1, mathMins: 23
  });
  const s = P.buildPlan({}, c, M).state;
  const ids = subjectsOf(s.plan);
  assert.ok(ids.includes('u02-1'), '识字要排');
  assert.ok(ids.includes('u03-1'), '数学科太长也不能被挤掉');
  assert.ok(s.plan.plannedMin <= 30 + 10, '超出预算不超过 10 分钟弹性');
  assert.strictEqual(s.plan.plannedMin, 35);
});

test('未完成的复习不隔天堆积，改由到期机制每天限量重排', () => {
  const c = catalog4();
  let s = P.normalize({}, c);
  // 3 节复习 + 1 节新课都没学完（几天没打开 App 的典型状态）
  ['u01-1', 'u01-2', 'u01-3'].forEach(id => {
    s.lessons[id] = {
      status: 'done', watchedSec: 0, positionSec: 0, completedAt: '2026-11-08',
      reviewStage: 0, nextReviewAt: M, failedCount: 0
    };
  });
  s.plan = {
    date: '2026-11-08', budgetMin: 30, plannedMin: 30, isWeekend: false,
    items: [
      { id: 'u01-1', type: 'review', origin: 'review', minutes: 8, done: false },
      { id: 'u01-2', type: 'review', origin: 'review', minutes: 8, done: false },
      { id: 'u01-3', type: 'review', origin: 'review', minutes: 8, done: false },
      { id: 'u02-1', type: 'new', origin: 'new', minutes: 8, done: false }
    ]
  };
  s = P.buildPlan(s, c, T).state; // 周二
  const reviews = s.plan.items.filter(i => i.type === 'review');
  const carries = s.plan.items.filter(i => i.type === 'carry');
  assert.strictEqual(carries.length, 1, '只有新课进顺延');
  assert.strictEqual(carries[0].id, 'u02-1');
  assert.strictEqual(reviews.length, 2, '复习每天最多 2 节，不把 3 节全堆进来');
});

test('升级排课逻辑后，当天的旧计划自动重排', () => {
  const c = catalog4();
  let s = P.normalize({}, c);
  // 模拟旧版本生成的当日计划（无 engine 字段、全是拼音）
  s.plan = {
    date: M, budgetMin: 30, plannedMin: 24, isWeekend: false,
    items: [
      { id: 'u01-1', type: 'new', origin: 'new', minutes: 8, done: false },
      { id: 'u01-2', type: 'new', origin: 'new', minutes: 8, done: false },
      { id: 'u01-3', type: 'new', origin: 'new', minutes: 8, done: false }
    ]
  };
  const r = P.buildPlan(s, c, M);
  assert.strictEqual(r.created, true, 'engine 不一致必须重排');
  assert.strictEqual(r.state.plan.theme, '识字阅读 + 数学');
  assert.strictEqual(r.state.plan.engine, P.ENGINE);
  // 旧计划里没学完的新课按顺延原则保留置顶，之后接上当天该学的识字
  assert.deepStrictEqual(
    r.state.plan.items.slice(0, 4).map(i => [i.id, i.type]),
    [['u01-1', 'carry'], ['u01-2', 'carry'], ['u01-3', 'carry'], ['u02-1', 'new']]
  );
  // 同一天再次调用不重排
  const r2 = P.buildPlan(r.state, c, M);
  assert.strictEqual(r2.created, false);
});

test('自由拓展日（周六）不做搭配强排，严格按预算装箱', () => {
  const c = catalog4({
    pinyin: 0, english: 0, literacy: 2, literacyMins: 12, math: 1, mathMins: 23
  });
  const s = P.buildPlan({}, c, SA).state; // 周六 15 分钟
  assert.strictEqual(s.plan.plannedMin, 12, '严格按预算装箱，不为搭配超预算');
  assert.deepStrictEqual(subjectsOf(s.plan), ['u02-1']);
});

test('撤销「学完了」：新课退回未学，今日统计一起回退', () => {
  const c = catalog4({ pinyin: 0, english: 0, literacy: 4, math: 4 });
  let s = P.buildPlan({}, c, M).state;
  const first = s.plan.items[0].id;
  const beforeCount = (s.history && s.history[M]) ? s.history[M].doneCount : 0;
  s = P.completeItem(s, c, first, 'good', M);
  assert.strictEqual(s.lessons[first].status, 'done', '完成后标记 done');
  assert.strictEqual(s.plan.items.find(i => i.id === first).done, true);

  s = P.undoComplete(s, c, first, M);
  assert.strictEqual(s.lessons[first].status, 'new', '退回未学');
  assert.strictEqual(s.lessons[first].completedAt, null, '清掉完成日期');
  assert.strictEqual(s.lessons[first].nextReviewAt, null, '不再安排复习');
  assert.strictEqual(s.plan.items.find(i => i.id === first).done, false, '今日重新变未完成');
  assert.strictEqual((s.history[M] || {}).doneCount || 0, beforeCount, '今日完成数回退');
  // 退回后可以重新学并再次完成
  s = P.completeItem(s, c, first, 'good', M);
  assert.strictEqual(s.lessons[first].status, 'done', '可以重新学完');
});

test('撤销「学完了」：复习节回退一档且今天仍算到期', () => {
  const c = catalog4({ pinyin: 0, english: 0, literacy: 4, math: 4 });
  let s = P.buildPlan({}, c, M).state;
  const id = s.plan.items[0].id;
  // 先在周一学完，并把复习时间改到今天
  s = P.completeItem(s, c, id, 'good', M);
  s.lessons[id].reviewStage = 1;
  s.lessons[id].nextReviewAt = W;
  s = P.buildPlan(s, c, W).state; // 周三应排进来复习
  const rev = s.plan.items.find(i => i.id === id && i.origin === 'review');
  assert.ok(rev, '周三应有这节的复习');
  s = P.completeItem(s, c, id, 'good', W);
  assert.strictEqual(s.lessons[id].reviewStage, 2, '复习推进到 2');

  s = P.undoComplete(s, c, id, W);
  assert.strictEqual(s.lessons[id].reviewStage, 1, '复习阶段回退一档');
  assert.strictEqual(s.lessons[id].nextReviewAt, W, '今天仍算到期');
  assert.strictEqual(s.lessons[id].status, 'done', '复习回退不影响"已学过"');
});

test('撤销「学完了」：不在今日队列的旧课也能重置', () => {
  const c = catalog4({ pinyin: 0, english: 0, literacy: 4, math: 4 });
  let s = P.buildPlan({}, c, M).state;
  const id = s.plan.items[0].id;
  s = P.completeItem(s, c, id, 'good', M);
  // 换到周三（今日队列不含这节的完成项）
  s = P.buildPlan(s, c, W).state;
  s = P.undoComplete(s, c, id, W);
  assert.strictEqual(s.lessons[id].status, 'new', '旧课也能退回未学');
  assert.strictEqual(s.lessons[id].nextReviewAt, null, '清掉复习安排');
});

console.log('\n全部 ' + passed + ' 项通过');
