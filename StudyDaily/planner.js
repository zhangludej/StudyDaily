/*
 * StudyDaily - 排课引擎
 * 纯逻辑、无 DOM 依赖。浏览器里挂到 window.Planner，Node 里可 require。
 *
 * 规则（已与需求确认）：
 *  - 平日 30 分钟 / 周末 15 分钟，家长端可改
 *  - 按星期排课（周课表）：识字每天必排，第二科按星期搭配
 *    周一/周三 识字+数学；周二 识字+拼音；周四 识字+英语；
 *    周五 识字+数学/拼音（隔周自动轮换，家长可固定）；
 *    周六 自由拓展（不限科目）；周日 休息/复盘（只复习，不排新课）
 *  - 同一科目内部严格按顺序学，整节不拆；多科目轮流出，均衡搭配
 *  - 今日队列 = 昨日未完成（原顺序置顶）-> 到期复习 -> 未学新课按周课表装箱
 *  - 顺延不压缩、不追赶：预算恒定，不会因为欠账而加量
 *  - 学完进入 1/3/7/15 天复习；"没学会"次日重看，不给孩子出题
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.Planner = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var VERSION = 2;
  // 排课逻辑版本：升级后当天的旧计划自动重排（plan.engine 不一致即重建）
  var ENGINE = 3;
  var collator = typeof Intl !== 'undefined' && Intl.Collator
    ? new Intl.Collator('zh-CN', { numeric: true, sensitivity: 'base' })
    : null;

  /** 科目识别：单元名里带关键词即归入该科目，识别不出则不限科目 */
  var SUBJECT_RULES = [
    ['pinyin', /拼音|pinyin/i],
    ['literacy', /识字|语文|阅读|汉字|国学|古诗/i],
    ['math', /数学|思维|math/i],
    ['english', /英语|英文|english/i]
  ];
  var SUBJECT_NAMES = { literacy: '识字阅读', math: '数学', pinyin: '拼音', english: '英语' };

  /** 周课表默认值：0=周日 … 6=周六 */
  var WEEK_DEFAULT = {
    0: 'rest',
    1: ['literacy', 'math'],
    2: ['literacy', 'pinyin'],
    3: ['literacy', 'math'],
    4: ['literacy', 'english'],
    5: ['literacy', 'rotate'],
    6: 'all'
  };

  function subjectOfName(name) {
    var n = String(name || '');
    for (var i = 0; i < SUBJECT_RULES.length; i++) {
      if (SUBJECT_RULES[i][1].test(n)) return SUBJECT_RULES[i][0];
    }
    return null;
  }

  /** 第几周（用于周五轮换）：自 1970 年起按自然周取模，隔周切换 */
  function weekParity(dateStr) {
    var p = String(dateStr).split('-');
    var days = Math.floor(Date.UTC(+p[0], +p[1] - 1, +p[2]) / 86400000);
    return Math.floor((days + 4) / 7) % 2;
  }

  /**
   * 某天允许的科目列表。
   * 返回 null 表示不限科目（周六自由拓展）；返回 [] 表示休息日（只复习）。
   */
  function resolveSubjects(dateStr, settings) {
    var w = dayOfWeek(dateStr);
    var s = settings || {};
    var cfg = (s.weeklySchedule && s.weeklySchedule[w] !== undefined)
      ? s.weeklySchedule[w] : WEEK_DEFAULT[w];
    if (cfg === 'rest') return [];
    if (!cfg || cfg === 'all') return null;
    var out = [];
    (Array.isArray(cfg) ? cfg : [cfg]).forEach(function (x) {
      if (x === 'rotate') {
        var focus = s.fridayFocus;
        if (focus !== 'math' && focus !== 'pinyin') {
          focus = weekParity(dateStr) === 0 ? 'math' : 'pinyin';
        }
        out.push(focus);
      } else if (SUBJECT_NAMES[x]) {
        out.push(x);
      }
    });
    return out;
  }

  function themeLabel(dateStr, settings) {
    var subs = resolveSubjects(dateStr, settings);
    if (subs === null) return '自由拓展';
    if (!subs.length) return '休息日';
    return subs.map(function (x) { return SUBJECT_NAMES[x]; }).join(' + ');
  }

  function naturalCompare(a, b) {
    if (collator) return collator.compare(String(a), String(b));
    return String(a).localeCompare(String(b), undefined, { numeric: true });
  }

  function defaults() {
    return {
      weekdayMinutes: 30,
      weekendMinutes: 15,
      reviewEnabled: true,
      reviewIntervals: [1, 3, 7, 15],
      completionRatio: 0.9,
      maxReviewsPerDay: 2,
      pin: '1234',
      speechEnabled: true,
      soundEnabled: true,
      quizEnabled: true,
      weeklySchedule: WEEK_DEFAULT,
      fridayFocus: 'auto'
    };
  }

  function todayStr(d) {
    var x = d || new Date();
    var y = x.getFullYear();
    var m = String(x.getMonth() + 1).padStart(2, '0');
    var day = String(x.getDate()).padStart(2, '0');
    return y + '-' + m + '-' + day;
  }

  function addDays(dateStr, n) {
    var p = String(dateStr).split('-');
    var d = new Date(Date.UTC(+p[0], +p[1] - 1, +p[2]));
    d.setUTCDate(d.getUTCDate() + n);
    return d.toISOString().slice(0, 10);
  }

  function dayOfWeek(dateStr) {
    var p = String(dateStr).split('-');
    return new Date(Date.UTC(+p[0], +p[1] - 1, +p[2])).getUTCDay();
  }

  function isWeekend(dateStr) {
    var w = dayOfWeek(dateStr);
    return w === 0 || w === 6;
  }

  function budgetFor(dateStr, settings) {
    return isWeekend(dateStr) ? settings.weekendMinutes : settings.weekdayMinutes;
  }

  function minutesOf(lesson) {
    var sec = Number(lesson && lesson.durationSec) || 0;
    return Math.max(1, Math.ceil(sec / 60));
  }

  /**
   * 把 catalog.units 展开成有序 lesson 数组，并补上 unitName / order。
   * 带 interest:true 的单元不进排课（孩子自由点播，不算每日任务）。
   */
  function flatten(catalog, wantInterest) {
    var out = [];
    var units = (catalog && catalog.units) || [];
    units.forEach(function (unit, ui) {
      var isInterest = !!(unit && unit.interest);
      if (isInterest !== !!wantInterest) return;
      var lessons = (unit && unit.lessons) || [];
      lessons.slice()
        .sort(function (a, b) {
          var oa = Number(a && a.order) || 0, ob = Number(b && b.order) || 0;
          if (oa !== ob) return oa - ob;
          return naturalCompare((a && a.title) || '', (b && b.title) || '');
        })
        .forEach(function (l, li) {
          // 透传 type / pages / pageCount / renderMode 等扩展字段，
          // 让 PDF 课件能与视频一样参与排课，前端再按 type 分流渲染。
          out.push(Object.assign({}, l, {
            id: String(l.id || (unit.id + '-' + (li + 1))),
            title: String(l.title || ('第' + (li + 1) + '课')),
            file: String(l.file || ''),
            type: l.type === 'pdf' ? 'pdf' : 'video',
            durationSec: Number(l.durationSec) || 0,
            unitId: String(unit.id || ('u' + (ui + 1))),
            unitName: String(unit.name || ('单元 ' + (ui + 1))),
            subject: subjectOfName(unit.name),
            order: out.length + 1
          }));
        });
    });
    return out;
  }

  function flatLessons(catalog) {
    return flatten(catalog, false);
  }

  /** 兴趣课清单（不参与排课，只给「兴趣课堂」自由点播用） */
  function flatInterest(catalog) {
    return flatten(catalog, true);
  }

  function lessonMap(catalog) {
    var map = {};
    flatLessons(catalog).forEach(function (l) { map[l.id] = l; });
    return map;
  }

  function normalize(state, catalog) {
    var s = state && typeof state === 'object' ? state : {};
    var d = defaults();
    s.version = VERSION;
    s.settings = Object.assign({}, d, s.settings || {});
    if (!Array.isArray(s.settings.reviewIntervals) || !s.settings.reviewIntervals.length) {
      s.settings.reviewIntervals = d.reviewIntervals.slice();
    }
    s.settings.weekdayMinutes = clampMinutes(s.settings.weekdayMinutes, d.weekdayMinutes);
    s.settings.weekendMinutes = clampMinutes(s.settings.weekendMinutes, d.weekendMinutes);
    s.settings.maxReviewsPerDay = Math.max(0, Math.min(6, Number(s.settings.maxReviewsPerDay) || 0));
    if (!s.settings.weeklySchedule || typeof s.settings.weeklySchedule !== 'object') {
      s.settings.weeklySchedule = WEEK_DEFAULT;
    }
    if (['auto', 'math', 'pinyin'].indexOf(s.settings.fridayFocus) < 0) {
      s.settings.fridayFocus = 'auto';
    }
    s.lessons = s.lessons && typeof s.lessons === 'object' ? s.lessons : {};
    s.quiz = s.quiz && typeof s.quiz === 'object' ? s.quiz : {};
    s.quizDay = s.quizDay && typeof s.quizDay === 'object' ? s.quizDay : {};
    s.workbook = s.workbook && typeof s.workbook === 'object' ? s.workbook : {};
    s.history = s.history && typeof s.history === 'object' ? s.history : {};
    s.skipped = Array.isArray(s.skipped) ? s.skipped : [];
    s.plan = s.plan && typeof s.plan === 'object' ? s.plan : null;
    // 自定义顺序：以 catalog 为准补齐，去掉已不存在的课
    var valid = flatLessons(catalog).map(function (l) { return l.id; });
    var validSet = {};
    valid.forEach(function (id) { validSet[id] = true; });
    var custom = Array.isArray(s.order) ? s.order.filter(function (id) { return validSet[id]; }) : [];
    custom.forEach(function (id) {
      var i = valid.indexOf(id);
      if (i >= 0) valid.splice(i, 1);
    });
    s.order = custom.concat(valid);
    return s;
  }

  function clampMinutes(v, fallback) {
    var n = Number(v);
    if (!isFinite(n) || n <= 0) return fallback;
    return Math.max(5, Math.min(480, Math.round(n)));
  }

  function newLessonState() {
    return {
      status: 'new',
      watchedSec: 0,
      positionSec: 0,
      completedAt: null,
      reviewStage: 0,
      nextReviewAt: null,
      failedCount: 0
    };
  }

  function ensureLesson(state, id) {
    if (!state.lessons[id]) state.lessons[id] = newLessonState();
    return state.lessons[id];
  }

  /** 到期复习：已学完且 nextReviewAt <= today，最多 maxReviewsPerDay 节 */
  function dueReviews(state, today, limit) {
    var out = [];
    Object.keys(state.lessons).forEach(function (id) {
      var ls = state.lessons[id];
      if (ls.status === 'done' && ls.nextReviewAt && ls.nextReviewAt <= today) {
        out.push({ id: id, due: ls.nextReviewAt });
      }
    });
    out.sort(function (a, b) { return a.due < b.due ? -1 : a.due > b.due ? 1 : 0; });
    return out.slice(0, limit);
  }

  /**
   * 生成（或复用）今日队列。
   * 同一天重复调用不会重排；但排课逻辑版本升级（ENGINE 变化）后，
   * 当天的旧计划会自动重排，避免「装了新版本今天还是老课表」。
   * 返回 { state, created } —— created=true 表示今天（重新）生成了计划。
   */
  function buildPlan(state, catalog, today) {
    today = today || todayStr();
    state = normalize(state, catalog);
    if (state.plan && state.plan.date === today && state.plan.engine === ENGINE) {
      return { state: state, created: false };
    }

    var map = lessonMap(catalog);
    var settings = state.settings;
    var budget = budgetFor(today, settings);
    // 周课表：null=不限科目（自由拓展），[]=休息日（只复习），数组=限定科目
    var allowed = resolveSubjects(today, settings);
    var restDay = allowed !== null && allowed.length === 0;
    // 搭配保证的弹性额度：识字 12 分钟 + 数学 22 分钟 = 34 分钟，
    // 比 30 分钟预算略高——宁可超 4 分钟，也不能让「周一/周三只有识字没有数学」
    var PAIR_SLACK = 10;
    var items = [];
    var used = 0;
    var planned = {};

    // 1) 昨日未完成的【新课】原顺序置顶，不压缩。
    //    未完成的复习不随天顺延（否则几天不学会堆积成灾），
    //    它们的 nextReviewAt 仍 <= 今天，由下面第 3 步每天限量重新挑入。
    //    休息日不带欠账，顺延到周一原序置顶。
    if (!restDay && state.plan && Array.isArray(state.plan.items)) {
      state.plan.items.forEach(function (it) {
        if (it.done || !map[it.id]) return;
        if (it.type === 'review' || it.origin === 'review') return;
        if (planned[it.id]) return;
        planned[it.id] = true;
        items.push({
          id: it.id,
          type: 'carry',
          origin: 'new',
          minutes: Number(it.minutes) || minutesOf(map[it.id]),
          done: false
        });
        used += Number(it.minutes) || minutesOf(map[it.id]);
      });
    }

    // 已覆盖科目：今天队列里已有该科的课（顺延/复习都算），搭配时就不用强排
    var covered = {};
    items.forEach(function (it) {
      var l = map[it.id];
      if (l && l.subject) covered[l.subject] = true;
    });

    // 2) 未学新课按周课表分科排队（科目内严格按顺序，绝不跳课）
    var skipped = {};
    state.skipped.forEach(function (id) { skipped[id] = true; });
    var queues = [];
    if (!restDay) {
      var bucket = {};
      var seq = [];
      state.order.forEach(function (id) {
        var l = map[id];
        if (!l || planned[id] || skipped[id]) return;
        var ls = state.lessons[id];
        if (ls && ls.status === 'done') return;
        if (allowed && l.subject && allowed.indexOf(l.subject) < 0) return;
        var key = l.subject || '_extra';
        if (!bucket[key]) { bucket[key] = []; seq.push(key); }
        bucket[key].push(id);
      });
      if (allowed) {
        allowed.forEach(function (sub) {
          if (bucket[sub]) queues.push({ list: bucket[sub], idx: 0, subject: sub });
        });
        // 识别不出科目的单元（如自定义内容）：休息日外的每天都可排
        if (bucket['_extra']) queues.push({ list: bucket['_extra'], idx: 0, subject: '_extra' });
      } else {
        seq.forEach(function (key) { queues.push({ list: bucket[key], idx: 0, subject: key }); });
      }
    }

    // 3) 搭配保证：当天安排的每个科目先各排一节（「识字→数学」而不是「识字→识字」）。
    //    只在指定的搭配日启用（自由拓展日不强制）；已有该科课时（顺延/复习）不再强排。
    if (!restDay && allowed && allowed.length) {
      queues.forEach(function (q) {
        if (q.idx >= q.list.length) return;
        if (covered[q.subject]) return;
        var id = q.list[q.idx];
        var m = minutesOf(map[id]);
        if (used <= budget && used + m <= budget + PAIR_SLACK) {
          planned[id] = true;
          items.push({ id: id, type: 'new', origin: 'new', minutes: m, done: false });
          used += m;
          q.idx++;
          covered[q.subject] = true;
        }
      });
    }

    // 4) 到期复习，每天最多 N 节；搭配之后排在后面，装不下就明天再看
    if (settings.reviewEnabled) {
      dueReviews(state, today, settings.maxReviewsPerDay).forEach(function (r) {
        if (planned[r.id] || !map[r.id]) return;
        var m = minutesOf(map[r.id]);
        if (used + m > budget + PAIR_SLACK) return;
        planned[r.id] = true;
        items.push({ id: r.id, type: 'review', origin: 'review', minutes: m, done: false });
        used += m;
      });

      // 防饿死：如果今天一节复习都没有，至少补入最老的一节到期复习
      // （同样受预算+弹性约束，避免顺延日越滚越长）
      if (!items.some(function (it) { return it.origin === 'review'; })) {
        var overdue = dueReviews(state, today, settings.maxReviewsPerDay);
        for (var oi = 0; oi < overdue.length; oi++) {
          var oid = overdue[oi].id;
          if (planned[oid] || !map[oid]) continue;
          var om = minutesOf(map[oid]);
          if (used + om > budget + PAIR_SLACK) break;
          items.push({ id: oid, type: 'review', origin: 'review', minutes: om, done: false });
          used += om;
          planned[oid] = true;
          break;
        }
      }
    }

    // 5) 预算还有剩余时，各科轮流出课继续装（识字→数学→识字→数学…）
    //    某科下一节装不下时只暂停这一科，其他科目继续，不浪费当日预算。
    var guard = 0;
    while (used < budget && guard < 500) {
      guard++;
      var picked = false;
      for (var qi = 0; qi < queues.length; qi++) {
        var q = queues[qi];
        if (q.idx >= q.list.length) continue;
        var qid = q.list[q.idx];
        var qm = minutesOf(map[qid]);
        if (used + qm > budget) continue; // 这一科今天装不下下一节，另一科继续
        planned[qid] = true;
        items.push({ id: qid, type: 'new', origin: 'new', minutes: qm, done: false });
        used += qm;
        q.idx++;
        picked = true;
      }
      if (!picked) break;
    }

    // 6) 防卡死：若某节课本身比当日预算还长，今天又没有任何其他任务，
    //    就让它独占今天（仍是整节不拆），否则这节课会永远排不上。
    //    休息日或当天科目都已学完时，自然留空 = 休息。
    if (items.length === 0 && !restDay) {
      for (var k = 0; k < state.order.length; k++) {
        var nid = state.order[k];
        var nl = map[nid];
        if (!nl || skipped[nid]) continue;
        if (allowed && nl.subject && allowed.indexOf(nl.subject) < 0) continue;
        var nls = state.lessons[nid];
        if (nls && nls.status === 'done') continue;
        var nm = minutesOf(nl);
        items.push({ id: nid, type: 'new', origin: 'new', minutes: nm, done: false });
        used += nm;
        break;
      }
    }

    state.plan = {
      date: today,
      engine: ENGINE,
      budgetMin: budget,
      plannedMin: used,
      isWeekend: isWeekend(today),
      theme: themeLabel(today, settings),
      rest: restDay,
      items: items
    };
    return { state: state, created: true };
  }

  /**
   * 完成一节课。
   * rating: 'good'（默认）| 'again'（家长标"没学会了"，次日重看）
   */
  function completeItem(state, catalog, id, rating, today) {
    today = today || todayStr();
    state = normalize(state, catalog);
    if (!state.plan || state.plan.date !== today) {
      state = buildPlan(state, catalog, today).state;
    }
    var item = null;
    state.plan.items.forEach(function (it) { if (it.id === id) item = it; });
    if (!item || item.done) return state;

    item.done = true;
    var ls = ensureLesson(state, id);
    var iv = state.settings.reviewIntervals;
    var again = rating === 'again';

    if (item.origin === 'review') {
      if (again) {
        ls.reviewStage = 0;
        ls.failedCount = (ls.failedCount || 0) + 1;
        ls.nextReviewAt = addDays(today, 1);
      } else {
        ls.reviewStage = (ls.reviewStage || 0) + 1;
        ls.nextReviewAt = ls.reviewStage < iv.length
          ? addDays(today, iv[ls.reviewStage])
          : null;
      }
    } else {
      // 新课第一次学完
      if (ls.status !== 'done') {
        ls.status = 'done';
        ls.completedAt = today;
      }
      ls.reviewStage = 0;
      if (again) {
        ls.failedCount = (ls.failedCount || 0) + 1;
        ls.nextReviewAt = addDays(today, 1);
      } else {
        ls.nextReviewAt = state.settings.reviewEnabled ? addDays(today, iv[0]) : null;
      }
    }
    ls.positionSec = 0;
    ls.watchedSec = (ls.watchedSec || 0) + item.minutes * 60;

    if (!state.history[today]) state.history[today] = { doneCount: 0, minutes: 0 };
    state.history[today].doneCount += 1;
    state.history[today].minutes += item.minutes;
    return state;
  }

  /**
   * 撤销「学完了」：孩子误点时用。这一节退回可学状态，
   * 本次产生的复习安排和今日统计一起回退，就像没点过一样。
   */
  function undoComplete(state, catalog, id, today) {
    today = today || todayStr();
    state = normalize(state, catalog);
    var item = null;
    if (state.plan && state.plan.date === today && Array.isArray(state.plan.items)) {
      state.plan.items.forEach(function (it) { if (it.id === id) item = it; });
    }

    // 不在今日队列里（比如以前学的）：只把这节退回未学，不动统计
    if (!item || !item.done) {
      var only = state.lessons[id];
      if (only && only.status === 'done') {
        only.status = 'new';
        only.completedAt = null;
        only.reviewStage = 0;
        only.nextReviewAt = null;
        only.failedCount = 0;
        only.positionSec = 0;
      }
      return state;
    }

    item.done = false;
    var ls = state.lessons[id];
    if (ls) {
      if (item.origin === 'review') {
        // 复习被误标：回退一档，并让它今天仍算到期
        ls.reviewStage = Math.max(0, (ls.reviewStage || 0) - 1);
        ls.nextReviewAt = today;
      } else if (!ls.completedAt || ls.completedAt === today) {
        // 新课第一次学完被误标：整节退回未学
        ls.status = 'new';
        ls.completedAt = null;
        ls.reviewStage = 0;
        ls.nextReviewAt = null;
      }
      ls.watchedSec = Math.max(0, (ls.watchedSec || 0) - item.minutes * 60);
      ls.positionSec = 0;
    }
    if (state.history[today]) {
      state.history[today].doneCount = Math.max(0, (state.history[today].doneCount || 0) - 1);
      state.history[today].minutes = Math.max(0, (state.history[today].minutes || 0) - item.minutes);
    }
    return state;
  }

  /** 家长端：今天不学这节，之后也不再排 */
  function skipItem(state, catalog, id, today) {
    state = normalize(state, catalog);
    if (state.plan && Array.isArray(state.plan.items)) {
      state.plan.items = state.plan.items.filter(function (it) { return it.id !== id; });
    }
    if (state.skipped.indexOf(id) < 0) state.skipped.push(id);
    return state;
  }

  /** 家长端：在今日队列里上移/下移 */
  function moveItem(state, catalog, id, delta) {
    state = normalize(state, catalog);
    if (!state.plan) return state;
    var arr = state.plan.items;
    var i = -1;
    arr.forEach(function (it, idx) { if (it.id === id) i = idx; });
    var j = i + delta;
    if (i < 0 || j < 0 || j >= arr.length) return state;
    var t = arr[i]; arr[i] = arr[j]; arr[j] = t;
    return state;
  }

  /** 家长端：手动把某节重新加回今日队列（用于撤销跳过或加练） */
  function addToPlan(state, catalog, id, origin, today) {
    today = today || todayStr();
    state = normalize(state, catalog);
    var map = lessonMap(catalog);
    if (!map[id]) return state;
    if (!state.plan || state.plan.date !== today) state = buildPlan(state, catalog, today).state;
    var exists = state.plan.items.some(function (it) { return it.id === id; });
    if (exists) return state;
    state.skipped = state.skipped.filter(function (x) { return x !== id; });
    var m = minutesOf(map[id]);
    state.plan.items.push({
      id: id, type: origin === 'review' ? 'review' : 'new',
      origin: origin === 'review' ? 'review' : 'new', minutes: m, done: false
    });
    state.plan.plannedMin = (state.plan.plannedMin || 0) + m;
    return state;
  }

  /** 记录播放位置（断点续播），不触发完成 */
  function setPosition(state, catalog, id, sec) {
    state = normalize(state, catalog);
    var ls = ensureLesson(state, id);
    ls.positionSec = Math.max(0, Math.round(Number(sec) || 0));
    return state;
  }

  function planProgress(plan) {
    if (!plan || !plan.items || !plan.items.length) return { done: 0, total: 0, percent: 0, allDone: true };
    var done = plan.items.filter(function (i) { return i.done; }).length;
    return {
      done: done,
      total: plan.items.length,
      percent: Math.round(done / plan.items.length * 100),
      allDone: done === plan.items.length
    };
  }

  function stats(state, catalog) {
    state = normalize(state, catalog);
    var all = flatLessons(catalog);
    var done = 0;
    all.forEach(function (l) {
      if (state.lessons[l.id] && state.lessons[l.id].status === 'done') done++;
    });
    var days = Object.keys(state.history).sort();
    var streak = 0;
    var cursor = todayStr();
    // 今天没学也允许从昨天开始算连续
    if (!state.history[cursor]) cursor = addDays(cursor, -1);
    while (state.history[cursor] && state.history[cursor].doneCount > 0) {
      streak++;
      cursor = addDays(cursor, -1);
    }
    var totalMinutes = 0;
    days.forEach(function (d) { totalMinutes += state.history[d].minutes || 0; });
    return {
      total: all.length,
      done: done,
      percent: all.length ? Math.round(done / all.length * 100) : 0,
      streak: streak,
      studyDays: days.length,
      totalMinutes: totalMinutes,
      remaining: all.length - done
    };
  }

  /** 最近 weeks 周的热力图数据（从周一开始的连续格子） */
  function heatmap(state, weeks, today) {
    weeks = weeks || 12;
    today = today || todayStr();
    var end = dayOfWeek(today); // 0=Sun..6=Sat
    var mondayOffset = end === 0 ? -6 : 1 - end;
    var thisMonday = addDays(today, mondayOffset);
    var start = addDays(thisMonday, -(weeks - 1) * 7);
    var cells = [];
    for (var i = 0; i < weeks * 7; i++) {
      var d = addDays(start, i);
      var h = state.history && state.history[d];
      cells.push({ date: d, count: h ? h.doneCount : 0, minutes: h ? h.minutes : 0, future: d > today });
    }
    return cells;
  }

  return {
    VERSION: VERSION,
    ENGINE: ENGINE,
    defaults: defaults,
    normalize: normalize,
    flatLessons: flatLessons,
    flatInterest: flatInterest,
    lessonMap: lessonMap,
    naturalCompare: naturalCompare,
    todayStr: todayStr,
    addDays: addDays,
    isWeekend: isWeekend,
    budgetFor: budgetFor,
    minutesOf: minutesOf,
    subjectOfName: subjectOfName,
    weekParity: weekParity,
    resolveSubjects: resolveSubjects,
    themeLabel: themeLabel,
    SUBJECT_NAMES: SUBJECT_NAMES,
    WEEK_DEFAULT: WEEK_DEFAULT,
    buildPlan: buildPlan,
    completeItem: completeItem,
    undoComplete: undoComplete,
    skipItem: skipItem,
    moveItem: moveItem,
    addToPlan: addToPlan,
    setPosition: setPosition,
    planProgress: planProgress,
    stats: stats,
    heatmap: heatmap,
    dueReviews: dueReviews
  };
});
