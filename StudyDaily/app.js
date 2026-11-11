/*
 * StudyDaily - 应用主逻辑
 * 孩子端：不识字也能用——图卡 + 大按钮 + 语音引导 + 即时鼓励
 * 家长端：PIN 进入，可改设置、跳课、标记没学会、看统计、导入导出、查看部署帮助
 * 进度：优先写服务端 progress.json（App 内置 / serve.py），否则存 localStorage 并可导出备份
 */
(function () {
  'use strict';

  var P = window.Planner;
  var $ = function (s) { return document.querySelector(s); };
  var $$ = function (s) { return Array.prototype.slice.call(document.querySelectorAll(s)); };
  var KEY = 'studydaily.state.v1';

  var catalog = null, state = null, lessonMap = {};
  var serverProgress = false;
  var appMode = false; // 运行在安卓 App 壳里（内置服务），文案与浏览器模式区分开
  var currentId = null, currentType = 'video', completedGuard = false;
  var pdfPages = [], pdfIndex = 0;
  var pendingPosition = 0, lastPosSave = 0;
  var saveTimer = null, nextTimer = null, nextId = null, confettiTimer = null;
  var tapCount = 0, tapTimer = null, pressTimer = null;
  var activeTab = 'today';
  var praiseIdx = 0;

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function encodePath(p) {
    return String(p || '').split('/').map(encodeURIComponent).join('/');
  }
  function dateText() {
    var d = new Date();
    var w = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'][d.getDay()];
    return (d.getMonth() + 1) + '月' + d.getDate() + '日 ' + w;
  }
  function toast(msg, ms) {
    var t = $('#toast');
    t.textContent = msg;
    t.classList.remove('hidden');
    clearTimeout(t._timer);
    t._timer = setTimeout(function () { t.classList.add('hidden'); }, ms || 2200);
  }
  function showBanner(msg, kind) {
    var b = $('#banner');
    b.textContent = msg;
    b.className = 'banner' + (kind === 'warn' ? ' warn' : '');
    b.classList.remove('hidden');
  }

  // ---------------- 单元外观：图标 + 主题色 ----------------
  // 孩子不认识「拼音认读」四个字，靠图标和颜色区分科目。
  // 每个单元一个「看得懂」的图标：孩子靠画面认科目，不靠字
  var UNIT_LOOK = [
    { re: /拼音|声母|韵母|拼读/, emoji: '🗣️', img: 'speaking_head', color: '#3D7DFF' },
    { re: /识字|汉字|认字|字卡|写字/, emoji: '📖', img: 'open_book', color: '#FF9F43' },
    { re: /数学|算数|计算|思维|数数/, emoji: '🔢', img: 'input_numbers', color: '#35C077' },
    { re: /英语|英文|字母|abc/i, emoji: '🌍', img: 'globe', color: '#8B5CF6' },
    { re: /古诗|国学|成语|弟子规/, emoji: '🏮', img: 'books', color: '#E4572E' },
    { re: /绘本|阅读|故事|说话|看图写话/, emoji: '📚', img: 'open_book', color: '#0EA5E9' },
    { re: /专注|注意|观察/, emoji: '🎯', img: 'brain', color: '#F43F5E' },
    { re: /安全|礼仪|习惯|品格|情商|财商/, emoji: '🌟', img: 'light_bulb', color: '#F59E0B' },
    { re: /美术|绘画|手工/, emoji: '🎨', img: 'artist_palette', color: '#EC4899' },
    { re: /音乐|儿歌|舞蹈|律动/, emoji: '🎵', img: 'musical_notes', color: '#14B8A6' }
  ];
  function lookOf(name) {
    var s = String(name || '');
    for (var i = 0; i < UNIT_LOOK.length; i++) {
      if (UNIT_LOOK[i].re.test(s)) return UNIT_LOOK[i];
    }
    return { emoji: '✨', img: 'sparkles', color: '#64748B' };
  }
  // 单元图标：优先用插画，加载失败退化回 emoji
  function unitIcon(look, cls) {
    if (!look) return '';
    var img = look.img
      ? '<img src="assets/img/' + look.img + '.png" alt="" loading="lazy" decoding="async"'
        + ' onerror="this.style.display=\'none\';this.parentNode.classList.add(\'noimg\')">'
      : '';
    return '<span class="' + (cls || 'unit-icon') + '">' + img
      + '<i class="emoji-fb">' + look.emoji + '</i></span>';
  }
  // 去掉「第12集」「03 」这类前缀，孩子不需要在标题里再读一次序号
  function shortTitle(t) {
    var s = String(t || '').trim();
    var r = s.replace(/^第\s*\d+\s*[集讲節节课时]\s*/i, '')
             .replace(/^\d+\s*[、._\-:：]\s*/, '')
             .trim();
    return r || s;
  }

  // ---------------- 语音引导 ----------------
  // 优先播放预生成的中文语音片段（voice/*.mp3，音色统一、完全离线）；
  // 片段缺失时才退化为原生 TTS 桥（AndroidTTS）或浏览器 Web Speech。
  var Speech = (function () {
    var enabled = true, last = '', lastAt = 0;
    var idx = null, loading = false, readyCbs = [];
    var audio = null, queue = [], playing = false, pending = null;

    function fireReady() {
      var cbs = readyCbs;
      readyCbs = [];
      for (var i = 0; i < cbs.length; i++) {
        try { cbs[i](); } catch (e) {}
      }
    }

    // 索引是异步来的；要等它到位的调用方传回调，避免"第一次用 TTS、之后用录音"音色跳变
    function loadIndex(cb) {
      if (cb) { if (idx) { try { cb(); } catch (e) {} } else readyCbs.push(cb); }
      if (idx || loading) return;
      loading = true;
      try {
        fetch('voice/index.json', { cache: 'no-store' })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (j) { idx = j || {}; fireReady(); pump(); })
          .catch(function () { idx = {}; fireReady(); });
      } catch (e) { idx = {}; fireReady(); }
    }
    function bridge() {
      var b = window.AndroidTTS;
      return (b && typeof b.speak === 'function') ? b : null;
    }
    function webOk() { return !!(window.speechSynthesis && window.SpeechSynthesisUtterance); }

    function tts(text) {
      var t = String(text || '');
      if (!t) return;
      try {
        var b = bridge();
        if (b) { b.speak(t); return; }
        if (webOk()) {
          window.speechSynthesis.cancel();
          var u = new window.SpeechSynthesisUtterance(t);
          u.lang = 'zh-CN'; u.rate = 0.92; u.pitch = 1.15; u.volume = 1;
          window.speechSynthesis.speak(u);
        }
      } catch (e) {}
    }
    function ensureAudio() {
      if (!audio) {
        audio = new Audio();
        audio.preload = 'auto';
        audio.addEventListener('ended', function () { playing = false; pump(); });
        audio.addEventListener('error', function () {
          playing = false;
          var it = queue.shift();
          if (it) tts(it.text);
          pump();
        });
      }
      return audio;
    }
    function fileFor(text) {
      if (!idx || !idx.phrases) return '';
      return idx.phrases[String(text)] || '';
    }
    function lessonFile(id) {
      if (!idx || !idx.lessons) return '';
      return idx.lessons[String(id)] || '';
    }
    // 依次播放一串发音项（能播片段就播片段，否则朗读文字）
    function pump() {
      if (!enabled || playing) return;
      var it = queue.shift();
      if (!it) return;
      var f = it.file || fileFor(it.text);
      if (!f) {
        // 没有录音片段，只能用系统 TTS：它没有可靠的结束回调，
        // 按字数估一个时长占住队列，否则多句会同时念出来变成"两个人声"
        tts(it.text);
        playing = true;
        var wait = Math.min(6000, 400 + String(it.text || '').length * 220);
        setTimeout(function () { playing = false; pump(); }, wait);
        return;
      }
      var a = ensureAudio();
      playing = true;
      a.src = 'voice/' + f;
      var pr = a.play();
      if (pr && pr.catch) {
        pr.catch(function () {
          // 浏览器/WebView 拦截自动播放：留到下一次点按时再播
          playing = false;
          pending = { file: f, text: it.text };
        });
      }
    }
    function speak(parts, force) {
      if (!enabled) return;
      var list = (parts || []).filter(function (x) { return x; });
      if (!list.length) return;
      var key = list.join('|');
      var now = Date.now();
      if (!force && key === last && now - lastAt < 3000) return;
      last = key; lastAt = now;
      if (force) {
        // 强制播报前先把上一条彻底掐掉，否则两条会同时出声
        queue = []; playing = false; pending = null;
        try { if (audio) audio.pause(); } catch (e) {}
        try { if (webOk()) window.speechSynthesis.cancel(); } catch (e) {}
        try { var b = bridge(); if (b && b.stop) b.stop(); } catch (e) {}
      }
      // 等语音索引就绪再播，否则第一句会退化成系统 TTS，和后面的录音音色对不上
      loadIndex(function () {
        queue = list.map(function (t) { return { text: t }; });
        playing = false;
        pump();
      });
    }
    return {
      // 一句话；能命中预生成片段就播片段
      say: function (text, force) {
        if (!text) return;
        speak([String(text)], force);
      },
      // 一句课的介绍：按课程 id 找片段（124 节各有一句）
      sayLesson: function (id, text) {
        loadIndex(function () {
          var f = lessonFile(id);
          if (f) {
            // 有整句录音就只播录音，绝不能再让 TTS 念一遍（否则两个声音叠在一起）
            Speech.stop();
            queue = [{ file: f, text: text }];
            playing = false;
            pump();
            return;
          }
          speak([text || ''], true);
        });
      },
      // 多句连读（表扬 + 继续/休息）
      sayParts: function (arr, force) { speak(arr || [], true); },
      unlock: function () {
        loadIndex();
        ensureAudio();
        if (pending) {
          var it = pending; pending = null;
          queue.unshift(it);
          playing = false;
          pump();
        }
      },
      stop: function () {
        queue = [];
        playing = false;
        pending = null;
        try { if (audio) audio.pause(); } catch (e) {}
        try { var b = bridge(); if (b && b.stop) b.stop(); } catch (e) {}
        try { if (webOk()) window.speechSynthesis.cancel(); } catch (e) {}
      },
      setEnabled: function (v) {
        enabled = !!v;
        if (!v) {
          queue = []; playing = false;
          try { if (audio) audio.pause(); } catch (e) {}
          try { if (webOk()) window.speechSynthesis.cancel(); } catch (e) {}
        }
      },
      isEnabled: function () { return enabled; }
    };
  })();

  // ---------------- 音效（WebAudio 合成，不依赖音频文件） ----------------
  var Sfx = (function () {
    var ctx = null, enabled = true;
    function ac() {
      var C = window.AudioContext || window.webkitAudioContext;
      if (!C) return null;
      if (!ctx) { try { ctx = new C(); } catch (e) { return null; } }
      if (ctx.state === 'suspended') { try { ctx.resume(); } catch (e) {} }
      return ctx;
    }
    function beep(freq, at, dur, vol, type) {
      var c = ac(); if (!c) return;
      try {
        var o = c.createOscillator(), g = c.createGain();
        o.type = type || 'sine';
        o.frequency.value = freq;
        var t0 = c.currentTime + at;
        g.gain.setValueAtTime(0.0001, t0);
        g.gain.exponentialRampToValueAtTime(vol || 0.16, t0 + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
        o.connect(g); g.connect(c.destination);
        o.start(t0); o.stop(t0 + dur + 0.06);
      } catch (e) {}
    }
    return {
      unlock: function () { ac(); },
      tap: function () { if (enabled) beep(680, 0, 0.08, 0.10, 'triangle'); },
      ding: function () { if (!enabled) return; beep(784, 0, 0.16, 0.16); beep(1047, 0.10, 0.24, 0.13); },
      fanfare: function () {
        if (!enabled) return;
        [523, 659, 784, 1047].forEach(function (f, i) { beep(f, i * 0.13, 0.30, 0.13, 'triangle'); });
      },
      setEnabled: function (v) { enabled = !!v; },
      isEnabled: function () { return enabled; }
    };
  })();

  // ---------------- 学完闯关（小游戏） ----------------
  // 不是「考试」，是「再玩一下」：小熊带路走三级台阶，答对一步亮一颗星，
  // 答错不打叉也不扣分，只是变暗 + 「再试一次」，连正确答案都不会直接给。
  var Quiz = (function () {
    var SAY = {
      intro: '学完啦，来做几个小题目吧',
      ok: '答对啦，真棒！',
      retry: '再试一次，仔细听',
      show: '没关系，记住它，下次就会了',
      perfect: '全对，太厉害啦！',
      done: '做完啦，你很努力！',
      skip: '先不做题，我们继续',
      ord: ['第一题', '第二题', '第三题', '第四题']
    };

    var bank = null, pics = {}, ready = false, loading = false;
    var list = [], idx = 0, lid = null, tries = 0, solved = 0;
    var totalTries = 0, combo = 0, locked = false, cb = null, cur = null, preview = false;

    function norm(e) {
      return String(e || '').replace(/\ufe0f/g, '');
    }
    function picOf(e) {
      var k = norm(e);
      return pics[k] || '';
    }
    // emoji → 插画，没有插画就退回系统字体
    function picHtml(e, cls) {
      var f = picOf(e);
      if (f) return '<img class="' + (cls || 'qz-pic') + '" src="assets/pics/' + f + '" alt="" decoding="async">';
      return '<span class="' + (cls || 'qz-pic') + '" style="font-size:clamp(40px,9vw,72px);line-height:1">' + esc(e) + '</span>';
    }

    function load() {
      if (ready || loading) return;
      loading = true;
      fetch('assets/pics/manifest.json', { cache: 'force-cache' })
        .then(function (r) { return r.ok ? r.json() : {}; })
        .then(function (j) {
          var map = {}, k;
          for (k in j) { if (j.hasOwnProperty(k)) { map[norm(k)] = j[k]; } }
          pics = map;
        })
        .catch(function () { pics = {}; });
      fetch('quiz.json', { cache: 'no-store' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) { bank = (j && j.lessons) || {}; ready = true; })
        .catch(function () { bank = {}; ready = true; });
    }

    function has(id) { return !!(bank && bank[id] && bank[id].length); }

    // ---------- 图形 ----------
    function shapeSvg(kind) {
      var c = { fill: '#6EC6FF', stroke: '#3D7DFF' };
      var body = '';
      if (kind === 'circle') body = '<circle cx="50" cy="50" r="40" fill="#FFC93C" stroke="#E9A800" stroke-width="4"/>';
      else if (kind === 'triangle') body = '<polygon points="50,10 92,86 8,86" fill="#FF8FAB" stroke="#E4587F" stroke-width="4" stroke-linejoin="round"/>';
      else if (kind === 'square') body = '<rect x="12" y="12" width="76" height="76" rx="6" fill="#6EC6FF" stroke="#3D7DFF" stroke-width="4"/>';
      else if (kind === 'rect') body = '<rect x="6" y="24" width="88" height="52" rx="6" fill="#7BE0A8" stroke="#27A15F" stroke-width="4"/>';
      else if (kind === 'oval') body = '<ellipse cx="50" cy="50" rx="44" ry="26" fill="#C4A2FF" stroke="#7C4DFF" stroke-width="4"/>';
      else body = '<circle cx="50" cy="50" r="40" fill="' + c.fill + '"/>';
      return '<svg viewBox="0 0 100 100" aria-hidden="true">' + body + '</svg>';
    }

    function clockSvg(h, m) {
      var ticks = '';
      for (var i = 0; i < 12; i++) {
        var a = i * 30 * Math.PI / 180;
        var r1 = 40, r2 = 45;
        ticks += '<line x1="' + (50 + Math.sin(a) * r1).toFixed(1) + '" y1="' + (50 - Math.cos(a) * r1).toFixed(1)
          + '" x2="' + (50 + Math.sin(a) * r2).toFixed(1) + '" y2="' + (50 - Math.cos(a) * r2).toFixed(1)
          + '" stroke="#B9C6DE" stroke-width="2.4" stroke-linecap="round"/>';
      }
      var ha = ((h % 12) + m / 60) * 30 * Math.PI / 180;
      var ma = m * 6 * Math.PI / 180;
      return '<svg class="qz-clock" viewBox="0 0 100 100">'
        + '<circle cx="50" cy="50" r="48" fill="#fff" stroke="#3D7DFF" stroke-width="4"/>'
        + ticks
        + '<line x1="50" y1="50" x2="' + (50 + Math.sin(ha) * 22).toFixed(1) + '" y2="' + (50 - Math.cos(ha) * 22).toFixed(1)
        + '" stroke="#2B3A63" stroke-width="6" stroke-linecap="round"/>'
        + '<line x1="50" y1="50" x2="' + (50 + Math.sin(ma) * 32).toFixed(1) + '" y2="' + (50 - Math.cos(ma) * 32).toFixed(1)
        + '" stroke="#FF9F43" stroke-width="4" stroke-linecap="round"/>'
        + '<circle cx="50" cy="50" r="4" fill="#2B3A63"/></svg>';
    }

    // ---------- 渲染 ----------
    function drawStage(st) {
      var box = $('#qzStage');
      if (!st) { box.innerHTML = ''; box.classList.add('hidden'); return; }
      box.classList.remove('hidden');
      var html = '';
      if (st.k === 'big') {
        html = st.cls === 'emoji'
          ? picHtml(st.v)
          : '<div class="qz-big' + (st.cls === 'letter' ? ' letter' : '') + '">' + esc(st.v) + '</div>';
      } else if (st.k === 'emojis') {
        html = '<div class="qz-emojis">';
        for (var i = 0; i < (st.n || 1); i++) html += picHtml(st.v, 'qz-pic');
        html += '</div>';
      } else if (st.k === 'clock') {
        html = clockSvg(st.h, st.m);
      } else if (st.k === 'grid') {
        html = '<div class="qz-grid">';
        (st.cells || []).forEach(function (c) {
          html += '<div class="cell">' + (c ? picHtml(c, 'qz-pic') : '') + '</div>';
        });
        html += '</div>';
      } else if (st.k === 'seq') {
        html = '<div class="qz-seq">';
        (st.v || []).forEach(function (c) {
          html += c === '?' || c === '❓'
            ? '<span>❓</span>'
            : picHtml(c, 'qz-seqitem');
        });
        html += '</div>';
      } else if (st.k === 'pair') {
        html = '<div class="qz-pair">' + picHtml(st.a) + picHtml(st.b) + '</div>';
      }
      box.innerHTML = html;
    }

    function optHtml(o) {
      var k = o.k || 'char';
      if (k === 'char') return '<span class="g-char">' + esc(o.t) + '</span>';
      if (k === 'letter') return '<span class="g-letter">' + esc(o.t) + '</span>';
      if (k === 'num') return '<span class="g-num">' + esc(o.t) + '</span>';
      if (k === 'text') return '<span class="g-text">' + esc(o.t) + '</span>';
      if (k === 'shape') return shapeSvg(o.t);
      if (k === 'clock') return clockSvg(o.h, o.m);
      if (k === 'emojis') {
        var h = '<span class="g-emojis">';
        for (var i = 0; i < (o.n || 1); i++) h += picHtml(o.t, 'g-pic');
        return h + '</span>';
      }
      return picHtml(o.t, 'g-pic');
    }

    function drawOpts(opts) {
      var box = $('#qzOpts');
      var cols = opts.length >= 4 && window.innerWidth >= 700 ? 4 : Math.min(opts.length, 3);
      box.style.setProperty('--cols', cols);
      box.innerHTML = opts.map(function (o, i) {
        return '<button class="qz-opt" data-i="' + i + '" style="--d:' + (i * 0.06).toFixed(2) + 's">'
          + optHtml(o) + '</button>';
      }).join('');
    }

    function drawSteps() {
      var html = '<div class="qz-bearwalker" id="qzBearWalker"><img src="assets/img/bear.png" alt=""></div>';
      for (var i = 0; i < list.length; i++) {
        html += '<span class="qz-step" data-i="' + i + '"><span class="n">' + (i + 1) + '</span></span>';
      }
      html += '<span class="qz-goal" id="qzGoal"><img src="assets/img/trophy.png" alt=""></span>';
      $('#qzSteps').innerHTML = html;
    }

    function syncSteps() {
      var els = $$('#qzSteps .qz-step');
      els.forEach(function (el, i) {
        el.classList.toggle('done', i < solved);
        el.classList.toggle('now', i === idx);
      });
      var bear = $('#qzBearWalker');
      var target = els[Math.min(solved, els.length - 1)];
      if (bear && target) {
        var box = $('#qzSteps').getBoundingClientRect();
        var r = target.getBoundingClientRect();
        bear.style.transform = 'translateX(' + (r.left - box.left + r.width / 2 - 23) + 'px)';
      }
      if (solved >= list.length) {
        var g = $('#qzGoal');
        if (g) g.classList.add('reached');
      }
    }

    function speakPrompt() {
      if (!cur) return;
      var parts = [];
      if (SAY.ord[idx]) parts.push(SAY.ord[idx]);
      parts.push(cur.say);
      Speech.sayParts(parts, true);
    }

    function show() {
      $('#quizView').classList.remove('hidden');
      $('#qzFinish').classList.add('hidden');
      $('#qzFeedback').classList.add('hidden');
      $('#qzCombo').classList.add('hidden');
    }

    function hide() {
      $('#quizView').classList.add('hidden');
      $('#qzFeedback').classList.add('hidden');
      $('#qzFinish').classList.add('hidden');
      Speech.stop();
    }

    function start(id, done, isPreview) {
      load();
      lid = id; cb = done; preview = !!isPreview;
      list = (bank && bank[id] || []).map(function (q, i) { return q; });
      if (!list.length) { if (cb) cb({ skip: true, nothing: true }); return; }
      idx = 0; solved = 0; tries = 0; totalTries = 0; combo = 0; locked = false;
      show();
      drawSteps();
      renderQuestion();
      Speech.say(SAY.intro, true);
      setTimeout(function () { if (!$('#quizView').classList.contains('hidden')) speakPrompt(); }, 1500);
    }

    function renderQuestion() {
      cur = list[idx];
      tries = 0; locked = false;
      $('#qzSayText').textContent = cur.q || '动动脑筋';
      drawStage(cur.stage);
      drawOpts(cur.opts);
      syncSteps();
      speakPrompt();

      var box = $('#qzOpts');
      box.onclick = function (e) {
        var btn = e.target.closest ? e.target.closest('.qz-opt') : null;
        if (!btn || btn.classList.contains('bad') || btn.classList.contains('ok')) return;
        choose(parseInt(btn.getAttribute('data-i'), 10), btn);
      };
    }

    function sparkle(el) {
      var r = el.getBoundingClientRect();
      var glyphs = ['⭐', '✨', '🌟', '💫', '⭐'];
      for (var i = 0; i < 9; i++) {
        var s = document.createElement('i');
        s.className = 'spark';
        s.textContent = glyphs[i % glyphs.length];
        s.style.left = (r.left + r.width / 2 - 8) + 'px';
        s.style.top = (r.top + r.height / 2 - 8) + 'px';
        var a = Math.random() * Math.PI * 2, d = 55 + Math.random() * 75;
        s.style.setProperty('--x', Math.cos(a) * d + 'px');
        s.style.setProperty('--y', Math.sin(a) * d + 'px');
        s.style.setProperty('--r', (Math.random() * 360 - 180) + 'deg');
        document.body.appendChild(s);
        (function (n) { setTimeout(function () { if (n.parentNode) n.parentNode.removeChild(n); }, 820); }(s));
      }
    }

    function choose(i, btn) {
      if (locked) return;
      totalTries++;
      if (i === cur.ans) {
        locked = true;
        combo++; solved++;
        btn.classList.add('ok');
        btn.innerHTML += '<span class="tick">✓</span>';
        // 字卡答对后补一张图，帮孩子把「字 → 意思」连起来
        var o = cur.opts[i];
        if (o.k === 'char' && o.e && picOf(o.e)) {
          btn.innerHTML += '<img class="hintpic" src="assets/pics/' + picOf(o.e) + '" alt="">';
        }
        sparkle(btn);
        Sfx.ding();
        speakOn(SAY.ok);
        syncSteps();
        if (combo >= 2) {
          var c = $('#qzCombo');
          c.textContent = '连对 ' + combo + ' 题！';
          c.classList.remove('hidden');
        }
        setTimeout(next, 1000);
      } else {
        tries++;
        btn.classList.add('shake');
        setTimeout(function () { btn.classList.remove('shake'); btn.classList.add('bad'); }, 380);
        Sfx.tap();
        if (tries >= 2) {
          speakOn(SAY.show);
          var right = $('#qzOpts .qz-opt[data-i="' + cur.ans + '"]');
          if (right) right.classList.add('hint');
        } else {
          speakOn(SAY.retry);
          setTimeout(function () { if (!locked) speakPrompt(); }, 1500);
        }
      }
    }
    function speakOn(text) { Speech.say(text, true); }

    function next() {
      idx++;
      if (idx >= list.length) { finish(); return; }
      renderQuestion();
    }

    function finish() {
      var all = solved >= list.length;
      $('#qzFeedback').classList.add('hidden');
      $('#qzFinish').classList.remove('hidden');
      var stars = '';
      for (var i = 0; i < list.length; i++) {
        stars += '<span' + (i < solved ? '' : ' class="off"') + '>★</span>';
      }
      $('#qzFinishStars').innerHTML = stars;
      $('#qzFinishTitle').textContent = all ? '全部答对！' : (solved >= list.length - 1 ? '闯关成功！' : '完成啦！');
      $('#qzFinishSub').textContent = '答对 ' + solved + ' / ' + list.length + ' 题';
      confetti(all ? 40 : 22);
      Sfx.fanfare();
      speakOn(all ? SAY.perfect : SAY.done);
      record({ ok: solved, tries: totalTries, skip: false });
    }

    function record(res) {
      if (preview || !state || !lid) return;
      var today = P.todayStr();
      state.quiz = state.quiz || {};
      state.quiz[lid] = {
        at: today, total: list.length, ok: res.ok,
        tries: res.tries || 0, skip: !!res.skip
      };
      state.quizDay = state.quizDay || {};
      var d = state.quizDay[today] || { n: 0, ok: 0 };
      d.n += list.length; d.ok += res.ok;
      state.quizDay[today] = d;
      save(true);
    }

    function close(skip) {
      if (skip) {
        record({ ok: solved, tries: totalTries, skip: true });
        Speech.say(SAY.skip, true);
      }
      hide();
      var f = cb; cb = null;
      var res = { ok: solved, total: list.length, skip: !!skip };
      lid = null;
      if (f) f(res);
    }

    return {
      load: load,
      has: has,
      start: start,
      close: close,
      restart: function () { if (lid) start(lid, cb, preview); },
      replay: function () { if (cur) speakPrompt(); },
      say: SAY
    };
  })();

  // ---------------- 练习册（书架 + 翻页） ----------------
  // 扫描版练习册不参与排课，孩子想看就看：像翻纸质练习册一样一页页照着做。
  // 每本书记住上次看到第几页（state.workbook），家长在电脑上用
  // tools/build_workbooks.py 导入新书。
  var WB = (function () {
    var books = [];
    var cur = null;       // 当前翻开的书
    var page = 0;         // 当前页码（0 起）

    function load() {
      return fetch('workbooks.json')
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          books = (d && d.books) || [];
          return books.length;
        })
        .catch(function () { books = []; return 0; });
    }

    var WB_ICON = { pinyin: 'speaking_head.png', hanzi: 'open_book.png',
                    math: 'input_numbers.png', english: 'globe.png' };
    function renderShelf() {
      var grid = $('#wbGrid');
      if (!books.length) { grid.innerHTML = ''; return; }
      var pos = (state && state.workbook) || {};
      grid.innerHTML = books.map(function (b) {
        var p = pos[b.id] || 0;
        var badge = p > 0 ? '<span class="wb-pos">看到第 ' + (p + 1) + ' 页</span>' : '';
        var ic = '<img class="wb-ic" src="assets/img/' + (WB_ICON[b.subject] || 'books.png') + '" alt="">';
        return '<button class="wb-book" data-wb="' + esc(b.id) + '">'
          + '<img class="wb-cover" src="' + encodePath(b.cover) + '" alt="">'
          + '<span class="wb-meta">' + ic
          + '<span class="wb-name">' + esc(b.title) + '</span>'
          + badge + '</span></button>';
      }).join('');
    }

    function showShelf() {
      $('#wbReader').classList.add('hidden');
      $('#kidView').classList.add('hidden');
      $('#wbView').classList.remove('hidden');
      renderShelf();
      Speech.say('这里是练习册，选一本开始吧', true);
    }

    function openBook(id) {
      var b = null;
      books.forEach(function (x) { if (x.id === id) b = x; });
      if (!b || !b.pages) return;
      cur = b;
      page = Math.min((state.workbook && state.workbook[b.id]) || 0, b.pages - 1);
      $('#wbView').classList.add('hidden');
      $('#wbReader').classList.remove('hidden');
      $('#wbBookTitle').textContent = b.title;
      showPage(true);
      Speech.sayParts([b.say || b.title, '翻开啦，点下面的箭头翻页'], true);
    }

    function showPage(keep) {
      if (!cur) return;
      $('#wbPageImg').src = encodePath(cur.folder + '/p' + ('00' + (page + 1)).slice(-3) + '.jpg');
      $('#wbPageLabel').textContent = (page + 1) + ' / ' + cur.pages;
      $('#wbPrev').disabled = page === 0;
      $('#wbNext').disabled = page >= cur.pages - 1;
      if (!keep) {
        if (!state.workbook) state.workbook = {};
        state.workbook[cur.id] = page;
        save();
      }
    }

    function turn(delta) {
      if (!cur) return;
      var next = page + delta;
      if (next < 0 || next >= cur.pages) return;
      page = next;
      Sfx.tap();
      showPage(false);
      if (page >= cur.pages - 1) Speech.say('这是最后一页啦', true);
    }

    function backToShelf() {
      cur = null;
      $('#wbReader').classList.add('hidden');
      showShelf();
    }

    function closeShelf() {
      $('#wbView').classList.add('hidden');
      $('#kidView').classList.remove('hidden');
    }

    function has() { return books.length > 0; }

    return { load: load, showShelf: showShelf, openBook: openBook,
             turn: turn, backToShelf: backToShelf, closeShelf: closeShelf,
             has: has };
  })();

  // ---------------- 存储 ----------------
  function loadLocal() {
    try {
      var raw = localStorage.getItem(KEY);
      return raw ? JSON.parse(raw) : {};
    } catch (e) { return {}; }
  }
  function flushServer() {
    if (!serverProgress) return;
    try {
      fetch('api/progress', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(state)
      }).catch(function () {});
    } catch (e) {}
  }
  function save(immediate) {
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {}
    clearTimeout(saveTimer);
    if (immediate) flushServer();
    else saveTimer = setTimeout(flushServer, 500);
  }

  // ---------------- 启动 ----------------
  function boot() {
    // 调试/截图用：?noanim 关掉所有动画，方便核对最终布局
    if (/[?&]noanim/.test(location.search)) {
      document.documentElement.classList.add('noanim');
    }
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('sw.js').catch(function () {});
    }
    bindEvents();
    bindVideo();

    state = loadLocal();
    WB.load().then(function () { // 练习册清单独立加载，拿不到就隐藏入口
      $('#wbEntry').classList.toggle('hidden', !WB.has());
      // 调试/截图：?wbshelf 直接开书架，?wbbook=书id 直接翻书
      var mWb = location.search.match(/[?&]wbbook=([\w-]+)/);
      if (mWb) WB.openBook(mWb[1]);
      else if (/[?&]wbshelf/.test(location.search) && WB.has()) WB.showShelf();
    });
    fetch('courses.json', { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error('missing'); return r.json(); })
      .then(function (c) { init(c); })
      .catch(function () {
        init({ version: 1, title: '尚未导入课程', demo: false, units: [] });
        showBanner('没有找到 courses.json。请先在电脑上运行 tools/prepare_courses.py 导入课件；'
          + '想先看效果可运行 python tools/make_demo.py 生成演示课程。', 'warn');
      });
  }

  function init(c) {
    catalog = c;
    $('#courseTitle').textContent = catalog.title || '今天学什么';
    $('#demoTag').classList.toggle('hidden', !catalog.demo);
    if (catalog.demo) {
      showBanner('当前是演示课程。导入真实课件后本提示会自动消失。');
    }
    lessonMap = P.lessonMap(catalog);

    // 探测内置服务：用现成 HTTP 服务器 App / 纯浏览器时会自动降级
    fetch('api/ping', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j || !j.progressApi) return null;
        serverProgress = true;
        if (j.server === 'StudyDaily-App') appMode = true;
        return fetch('api/progress', { cache: 'no-store' }).then(function (r) { return r.json(); });
      })
      .then(function (remote) {
        if (serverProgress && remote && remote.version) state = remote;
        finish();
      })
      .catch(function () { finish(); });
  }

  function finish() {
    Quiz.load();
    $('#storageMode').textContent = appMode
      ? '进度自动保存在本机 App 内（progress.json），清理浏览器数据、换浏览器都不会丢。'
      : serverProgress
        ? '进度自动保存到 progress.json（由本地服务提供），换浏览器也不会丢。'
        : '进度保存在本浏览器。清理浏览器数据会丢失记录，请定期用「导出进度备份」保存。';
    // 课程清单换过（重新跑转码脚本 / 切换演示数据）时，当天也要重新排课
    var stamp = String(catalog.generatedAt || '') + '|' + P.flatLessons(catalog).length;
    if (state.catalogStamp && state.catalogStamp !== stamp) {
      state.plan = null;
      toast('课程清单已更新，今天的任务已重新排好');
    }
    state.catalogStamp = stamp;
    state = P.buildPlan(state, catalog, P.todayStr()).state;
    syncAudioSettings();
    save(true);
    renderAll();
    setInterval(checkDate, 30000);
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) checkDate();
    });
    if (state.onboarded) speakTodayCount();
    maybeShowGuide();
    maybeQuizPreview();
  }

  // 家长想先看看这节课出什么题：家长端「课程」里点「看题目」，
  // 或者直接用 #quiz=课程id 打开（也方便在电脑上核对题目）
  function maybeQuizPreview() {
    var m = /^#quiz=([\w-]+)$/.exec(location.hash || '');
    if (!m) return;
    var id = m[1];
    if (!lessonMap[id]) return;
    setTimeout(function () {
      Quiz.start(id, function () {
        try { history.replaceState(null, '', location.pathname); } catch (e) { location.hash = ''; }
      }, true);
    }, 400);
  }

  // 「今天有 N 节课」——孩子不用读标题也知道今天有几件事
  function speakTodayCount() {
    var plan = state.plan || { items: [] };
    var prog = P.planProgress(plan);
    if (!prog.total) { Speech.say('今天没有新课'); return; }
    if (prog.allDone) return;
    if (prog.done) Speech.say('还剩 ' + (prog.total - prog.done) + ' 节');
    else Speech.say('今天有 ' + prog.total + ' 节课');
  }

  function syncAudioSettings() {
    var s = state.settings || {};
    Speech.setEnabled(s.speechEnabled !== false);
    Sfx.setEnabled(s.soundEnabled !== false);
    $('#soundBtn').textContent = Speech.isEnabled() ? '🔊' : '🔇';
    $('#soundBtn').classList.toggle('off', !Speech.isEnabled());
  }

  function checkDate() {
    var t = P.todayStr();
    if (!state.plan || state.plan.date !== t) {
      state = P.buildPlan(state, catalog, t).state;
      save(true);
      renderAll();
      Speech.say('新的一天，今天的任务已经排好啦', true);
    }
  }

  // ---------------- 首次引导 ----------------
  function maybeShowGuide() {
    if (/^#quiz=/.test(location.hash || '')) return; // 预览题目时不弹引导
    if (/[?&]wb(shelf|book)/.test(location.search)) return; // 预览练习册时不弹引导
    var hasItems = state.plan && (state.plan.items || []).length > 0;
    if (state.onboarded || !hasItems) return;
    setTimeout(function () {
      $('#guideLayer').classList.remove('hidden');
      Speech.say('我是小熊。点下面的大按钮，我们一起学习吧', true);
    }, 900);
  }
  function closeGuide() {
    $('#guideLayer').classList.add('hidden');
    state.onboarded = true;
    save();
    speakTodayCount();
  }

  // ---------------- 渲染 ----------------
  function renderAll() {
    renderKid();
    if (!$('#parentView').classList.contains('hidden')) renderParent();
  }

  function praiseList() {
    return ['太棒了！', '真厉害！', '好样的！', '你真棒！', '做得很好！'];
  }

  function renderKid() {
    var plan = state.plan || { items: [], budgetMin: 0, plannedMin: 0, isWeekend: false };
    $('#dateLabel').textContent = dateText()
      + (plan.theme ? ' · 今天学：' + plan.theme : '');
    var prog = P.planProgress(plan);
    var total = prog.total, done = prog.done;
    var left = total - done;

    // 大标题：只用最短的口语句子
    var title;
    if (!total) title = '今天没有新课';
    else if (prog.allDone) title = '今天全部完成！';
    else if (done === 0) title = '今天有 ' + total + ' 节课';
    else title = '还剩 ' + left + ' 节';
    $('#greeting').textContent = title;

    // 星星进度
    renderStars($('#starRow'), total, done, false);

    var list = $('#todayList');
    var donePanel = $('#donePanel');
    var emptyPanel = $('#emptyPanel');
    var startBtn = $('#startBtn');
    donePanel.classList.add('hidden');
    emptyPanel.classList.add('hidden');

    if (!total) {
      list.innerHTML = '';
      startBtn.classList.add('hidden');
      if (plan.rest) {
        $('#emptyTitle').textContent = '今天是休息日';
        $('#emptyTip').textContent = '听听复习的小课，或者去外面玩一会儿吧';
      } else {
        $('#emptyTitle').textContent = '今天没有新课';
        $('#emptyTip').textContent = '休息一下吧';
      }
      emptyPanel.classList.remove('hidden');
      return;
    }
    if (prog.allDone) {
      list.innerHTML = '';
      startBtn.classList.add('hidden');
      renderStars($('#doneStars'), Math.min(total, 5), Math.min(total, 5), true);
      donePanel.classList.remove('hidden');
      celebrateAll();
      return;
    }

    // 主按钮：还有一个没学完就出现
    var firstUndone = null;
    plan.items.forEach(function (it) { if (!firstUndone && !it.done) firstUndone = it; });
    if (firstUndone) {
      startBtn.classList.remove('hidden');
      startBtn.setAttribute('data-id', firstUndone.id);
      $('#startLabel').textContent = done ? '继续学习' : '开始学习';
    } else {
      startBtn.classList.add('hidden');
    }

    list.innerHTML = plan.items.map(function (it, i) {
      var l = lessonMap[it.id];
      if (!l) return '';
      var look = lookOf(l.unitName);
      var poster = l.poster ? encodePath(l.poster) : '';
      var tag = '';
      if (it.type === 'carry') tag += '<span class="kcard-tag">昨天没学完</span>';
      else if (it.type === 'review') tag += '<span class="kcard-tag review">再看一遍</span>';
      if (l.type === 'pdf') tag += '<span class="kcard-tag pdf">练习纸</span>';
      return '<button class="kcard' + (it.done ? ' done' : '') + '" data-id="' + esc(it.id) + '">'
        + '<div class="kcard-thumb' + (poster ? '' : ' noimg') + '" style="background:' + look.color + '1f">'
        + (poster ? '<img src="' + poster + '" alt="" loading="lazy" decoding="async">' : '')
        + '<span class="kcard-num">' + (it.done ? '✓' : (i + 1)) + '</span>'
        + unitIcon(look, 'kcard-emoji')
        + '</div>'
        + '<div class="kcard-info">'
        + '<div class="kcard-unit" style="color:' + look.color + '">' + esc(l.unitName) + '</div>'
        + '<div class="kcard-title">' + esc(shortTitle(l.title)) + '</div>'
        + tag
        + '</div>'
        + '<div class="kcard-play">' + (it.done ? '✓' : '▶') + '</div>'
        + '</button>';
    }).join('');
  }

  function renderStars(box, total, done, big) {
    if (!box) return;
    var n = Math.max(0, Math.min(total || 0, 12));
    var html = '';
    for (var i = 0; i < n; i++) {
      html += '<span class="kg-star' + (i < done ? ' on' : '') + '"></span>';
    }
    box.innerHTML = html;
    box.style.display = n ? 'flex' : 'none';
  }

  function celebrateAll() {
    if (state._celebratedDate === P.todayStr()) return;
    state._celebratedDate = P.todayStr();
    save();
    confetti(38);
    Sfx.fanfare();
    Speech.say('今天的任务全部完成，你真厉害！', true);
  }

  function confetti(n) {
    var box = $('#confetti');
    if (!box) return;
    var colors = ['#FFC93C', '#FF7EB3', '#6EC6FF', '#4FD08A', '#B388FF', '#FF9F43'];
    var html = '';
    for (var i = 0; i < (n || 32); i++) {
      var c = colors[i % colors.length];
      var left = Math.random() * 100;
      var delay = Math.random() * 0.45;
      var dur = 1.9 + Math.random() * 1.5;
      var size = 7 + Math.random() * 9;
      var h = size * (Math.random() > 0.5 ? 1 : 1.8);
      var radius = Math.random() > 0.6 ? '50%' : '3px';
      html += '<i style="left:' + left.toFixed(1) + '%;width:' + size.toFixed(0) + 'px;height:' + h.toFixed(0) + 'px;'
        + 'background:' + c + ';border-radius:' + radius + ';'
        + 'animation-duration:' + dur.toFixed(2) + 's;animation-delay:' + delay.toFixed(2) + 's"></i>';
    }
    box.innerHTML = html;
    clearTimeout(confettiTimer);
    confettiTimer = setTimeout(function () { box.innerHTML = ''; }, 3800);
  }

  function renderParent() {
    if (activeTab === 'today') renderTodayTab();
    else if (activeTab === 'courses') renderCoursesTab();
    else if (activeTab === 'stats') renderStatsTab();
    else if (activeTab === 'settings') renderSettingsForm();
  }

  function itemById(id) {
    var found = null;
    (state.plan ? state.plan.items : []).forEach(function (it) { if (it.id === id) found = it; });
    return found;
  }

  function renderTodayTab() {
    var items = state.plan ? state.plan.items : [];
    var html = '<div class="panel"><h3>今日队列（' + items.length + ' 节）</h3>'
      + '<p class="tiny">顺序即播放顺序。上移/下移只影响今天；跳过之后不再自动排这节。</p>';
    if (!items.length) html += '<p class="tiny">今天没有安排。</p>';
    items.forEach(function (it) {
      var l = lessonMap[it.id] || { title: it.id, unitName: '' };
      var typeName = it.type === 'carry' ? '顺延' : it.type === 'review' ? '复习' : '新课';
      var kindName = l.type === 'pdf' ? 'PDF' : '视频';
      html += '<div class="row"><div class="r-main">'
        + '<div class="r-title">' + esc(l.title) + ' ' + (it.done ? '<span class="mini green">已完成</span>' : '')
        + '</div><div class="r-sub">' + esc(l.unitName) + ' · ' + kindName + ' · ' + typeName
        + ' · ' + it.minutes + ' 分钟</div></div>'
        + '<button class="mini" data-action="up" data-id="' + esc(it.id) + '">↑</button>'
        + '<button class="mini" data-action="down" data-id="' + esc(it.id) + '">↓</button>'
        + (it.done ? '' : '<button class="mini blue" data-action="done" data-id="' + esc(it.id) + '">标完成</button>'
          + '<button class="mini blue" data-action="again" data-id="' + esc(it.id) + '">没学会</button>'
          + '<button class="mini red" data-action="skip" data-id="' + esc(it.id) + '">跳过</button>')
        + '</div>';
    });
    html += '</div>';
    $('#tab-today').innerHTML = html;
  }

  function renderCoursesTab() {
    var st = P.stats(state, catalog);
    var html = '<div class="panel"><h3>全部课程（' + st.done + ' / ' + st.total + ' 已学）</h3>';
    (catalog.units || []).forEach(function (u) {
      html += '<div class="unit-block"><h4>' + esc(u.name) + '（' + (u.lessons || []).length + ' 节）</h4>';
      (u.lessons || []).forEach(function (l) {
        var ls = state.lessons[l.id];
        var skipped = state.skipped.indexOf(l.id) >= 0;
        var status = skipped ? '已跳过'
          : (ls && ls.status === 'done') ? (ls.nextReviewAt ? '复习中' : '已学完') : '未学';
        var inPlan = !!itemById(l.id);
        var action = skipped
          ? '<button class="mini blue" data-action="restore" data-id="' + esc(l.id) + '">恢复</button>'
          : inPlan ? '<span class="mini">已在今天</span>'
            : '<button class="mini blue" data-action="add" data-id="' + esc(l.id) + '">加到今天</button>';
        action += '<button class="mini" data-action="quiz" data-id="' + esc(l.id) + '">看题目</button>';
        html += '<div class="row"><div class="r-main"><div class="r-title">' + esc(l.title)
          + '</div><div class="r-sub">' + (l.type === 'pdf' ? 'PDF · ' : '视频 · ')
          + status + ' · ' + P.minutesOf(l) + ' 分钟</div></div>'
          + action + '</div>';
      });
      html += '</div>';
    });
    if (!(catalog.units || []).length) html += '<p class="tiny">还没有课程，请先运行转码脚本导入课件。</p>';
    html += '</div>';
    $('#tab-courses').innerHTML = html;
  }

  function renderStatsTab() {
    var st = P.stats(state, catalog);
    var cells = P.heatmap(state, 12);
    var heat = cells.map(function (c) {
      var lv = c.future ? 'future' : c.count === 0 ? '' : c.count === 1 ? 'l1' : c.count === 2 ? 'l2' : 'l3';
      return '<div class="heat-cell ' + lv + '" title="' + c.date + '：' + c.count + ' 节"></div>';
    }).join('');
    $('#tab-stats').innerHTML =
      '<div class="panel"><h3>学习概览</h3><div class="stat-grid">'
      + '<div class="stat-box"><strong>' + st.done + '</strong><span>已学课程</span></div>'
      + '<div class="stat-box"><strong>' + st.percent + '%</strong><span>总进度</span></div>'
      + '<div class="stat-box"><strong>' + st.streak + '</strong><span>连续天数</span></div>'
      + '<div class="stat-box"><strong>' + Math.round(st.totalMinutes / 60 * 10) / 10 + '</strong><span>累计小时</span></div>'
      + '<div class="stat-box"><strong>' + st.remaining + '</strong><span>还剩课程</span></div>'
      + '</div></div>'
      + quizStatsHtml()
      + '<div class="panel"><h3>最近 12 周</h3><div class="heatmap">' + heat + '</div>'
      + '<p class="legend">每格一天，颜色越深完成得越多 · 共 ' + st.studyDays + ' 天有学习记录</p></div>'
      + '<div class="panel"><h3>今日' + (state.plan && state.plan.theme ? '（' + state.plan.theme + '）' : '') + '</h3><p class="tiny">预算 '
      + (state.plan ? state.plan.budgetMin : 0) + ' 分钟，安排 '
      + (state.plan ? state.plan.plannedMin : 0) + ' 分钟，完成 '
      + P.planProgress(state.plan).done + ' / ' + P.planProgress(state.plan).total + ' 节。</p></div>';
  }

  // 做题情况：家长最想知道「是真懂了还是蒙的」——看正确率和重复次数最快的题
  function quizStatsHtml() {
    var q = state.quiz || {};
    var ids = Object.keys(q);
    if (!ids.length) {
      return '<div class="panel"><h3>做题情况</h3><p class="tiny">还没有做题记录。'
        + '孩子看完一节课会自动进入「闯关」小游戏，做完这里就会有数据。</p></div>';
    }
    var n = 0, ok = 0, skipped = 0;
    ids.forEach(function (id) {
      n += q[id].total || 0;
      ok += q[id].ok || 0;
      if (q[id].skip) skipped++;
    });
    var rate = n ? Math.round(ok / n * 100) : 0;
    var day = state.quizDay || {};
    var dayKeys = Object.keys(day).sort().slice(-7);
    var rows = dayKeys.map(function (d) {
      var r = day[d];
      var p = r.n ? Math.round(r.ok / r.n * 100) : 0;
      return '<li><b>' + d.slice(5) + '</b> 做 ' + r.n + ' 题，答对 ' + r.ok + ' 题（' + p + '%）</li>';
    }).join('');
    // 错得最多的几节：同一题错 2 次以上就值得再看一遍
    var hard = ids.filter(function (id) { return (q[id].tries || 0) > (q[id].ok || 0) + 1 || (q[id].ok || 0) < (q[id].total || 0) - 1; })
      .slice(-5).map(function (id) {
        var l = lessonMap[id];
        var r = q[id];
        return '<li>' + esc(l ? shortTitle(l.title) : id)
          + ' <span class="muted">对了 ' + r.ok + '/' + r.total + '</span></li>';
      }).join('');

    return '<div class="panel"><h3>做题情况</h3><div class="stat-grid">'
      + '<div class="stat-box"><strong>' + n + '</strong><span>累计做题</span></div>'
      + '<div class="stat-box"><strong>' + rate + '%</strong><span>答对率</span></div>'
      + '<div class="stat-box"><strong>' + ids.length + '</strong><span>做过的课</span></div>'
      + '<div class="stat-box"><strong>' + skipped + '</strong><span>跳过没做</span></div>'
      + '</div>'
      + (rows ? '<ul class="quiz-days">' + rows + '</ul>' : '')
      + (hard ? '<h4 class="quiz-hard-title">这几节可以再看一遍</h4><ul class="quiz-days">' + hard + '</ul>' : '')
      + '<p class="tiny">答对率只做参考：孩子第一次接触新内容，3 题里对 2 题是正常的。'
      + '错题不用重做，第二天复习时还会再看一次这节课。</p></div>';
  }

  function renderSettingsForm() {
    var s = state.settings;
    $('#setWeekday').value = s.weekdayMinutes;
    $('#setWeekend').value = s.weekendMinutes;
    $('#setRatio').value = Math.round(s.completionRatio * 100);
    $('#setReview').checked = !!s.reviewEnabled;
    $('#setMaxReview').value = s.maxReviewsPerDay;
    $('#setFriday').value = ['auto', 'math', 'pinyin'].indexOf(s.fridayFocus) >= 0 ? s.fridayFocus : 'auto';
    $('#setPin').value = s.pin;
    $('#setSpeech').checked = s.speechEnabled !== false;
    $('#setSound').checked = s.soundEnabled !== false;
    $('#setQuiz').checked = s.quizEnabled !== false;
    loadCacheInfo();
    loadLocalInfo();
  }

  // ---------------- 本地课件（有文件夹时可完全离线） ----------------
  function loadLocalInfo() {
    var el = $('#localInfo');
    if (!el) return;
    if (!location.origin || location.origin.indexOf('http') !== 0) {
      el.textContent = '网页版直接读本地文件夹，不需要设置。';
      return;
    }
    fetch('api/local', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var btn = $('#localEnable');
        if (d && d.ready) {
          el.textContent = '已开启：' + (d.dir || '本机文件夹')
            + '　视频和练习册都从本机读取，完全断网也能用。';
          if (btn) btn.classList.add('hidden');
        } else {
          el.textContent = '还没选文件夹（现在走云端点播，第一次看新的一集要联网）。'
            + '如果本机已经存好视频，点下面的按钮选一次，之后完全不用网。';
          if (btn) {
            btn.classList.remove('hidden');
            btn.textContent = '选择离线课件文件夹';
          }
          if (d && d.picked) el.textContent = '上次选的文件夹里没找到视频，请重新选一次。';
        }
      })
      .catch(function () {
        el.textContent = '读不到本地课件状态（用网页版打开时不支持）。';
      });
  }

  function enableLocal() {
    fetch('api/local', { method: 'POST' })
      .then(function () {
        toast('请在弹出的窗口里选中存视频的那个文件夹（能看到 u01、u02 那一层）');
      })
      .catch(function () { toast('操作失败，稍后再试'); });
  }

  // 安卓壳选完文件夹后会调用它来刷新这一块
  window.sdRefreshLocal = loadLocalInfo;

  // ---------------- 缓存（云端点播留下的视频） ----------------
  function loadCacheInfo() {
    var el = $('#cacheInfo');
    if (!el) return;
    if (!location.origin || location.origin.indexOf('http') !== 0) {
      el.textContent = '网页版直接读本地文件夹，不涉及缓存。';
      return;
    }
    fetch('api/cache', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || typeof d.count !== 'number') throw new Error('no-data');
        var mb = Math.round((d.bytes || 0) / 1048576);
        var lim = Math.round((d.limit || 0) / 1073741824);
        el.textContent = '已缓存 ' + d.count + ' 集，占用 ' + (mb >= 1024 ? (mb / 1024).toFixed(1) + ' GB' : mb + ' MB')
          + '（上限 ' + lim + ' GB）' + (d.downloading ? '，正在下载 ' + d.downloading + ' 集' : '');
      })
      .catch(function () {
        el.textContent = '读不到缓存信息（用网页版打开时不支持）。';
      });
  }

  function clearCache() {
    if (!confirm('清空后，看过的课需要重新联网加载，确定要清吗？')) return;
    fetch('api/cache', { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function () { toast('缓存已清空'); loadCacheInfo(); })
      .catch(function () { toast('清理失败，稍后再试'); });
  }

  // ---------------- 播放 ----------------
  function bindVideo() {
    var v = $('#video');
    v.removeAttribute('controls');
    v.addEventListener('loadedmetadata', function () {
      if (pendingPosition > 0 && isFinite(v.duration) && pendingPosition < v.duration - 1) {
        try { v.currentTime = pendingPosition; } catch (e) {}
      }
    });
    v.addEventListener('play', function () { setPlayingUI(true); });
    v.addEventListener('pause', function () { if (!completedGuard) setPlayingUI(false); });
    v.addEventListener('timeupdate', function () {
      if (currentType !== 'video' || !isFinite(v.duration) || v.duration <= 0) return;
      $('#vProgressFill').style.width = Math.min(100, (v.currentTime / v.duration) * 100) + '%';
      if (!currentId || completedGuard) return;
      var now = Date.now();
      if (now - lastPosSave > 4000) {
        state = P.setPosition(state, catalog, currentId, v.currentTime);
        save(); lastPosSave = now;
      }
      if (v.currentTime / v.duration >= state.settings.completionRatio) completeCurrent('good');
    });
    v.addEventListener('ended', function () {
      if (currentType === 'video') completeCurrent('good');
    });
    v.addEventListener('error', function () {
      if (!currentId || currentType !== 'video') return;
      var online = navigator.onLine !== false;
      $('#videoError').innerHTML = (online
        ? '这一集还没存到本机，刚才联网加载没成功<br>看看 WiFi 再点「重试」，或者点「学完了」跳过'
        : '这一集还没存到本机，现在没有网络<br>连上 WiFi 就能看；看过的课以后离线也能看')
        + '<br><button id="videoRetry" class="btn-ghost" style="margin-top:10px">重试</button>';
      $('#videoError').classList.remove('hidden');
      var rb = $('#videoRetry');
      if (rb) {
        rb.onclick = function () {
          $('#videoError').classList.add('hidden');
          try { v.load(); v.play(); } catch (e) {}
        };
      }
    });
  }

  function setPlayingUI(playing) {
    var layer = $('#videoTapLayer');
    if (!layer) return;
    layer.classList.toggle('playing', !!playing);
  }

  // 全屏播放：纯 CSS 铺满整屏（不调 requestFullscreen，避免与 WebView 原生全屏冲突）
  function setFullscreen(on) {
    var wrap = $('#videoWrap');
    if (!wrap) return;
    var yes = typeof on === 'boolean' ? on : !wrap.classList.contains('fs');
    wrap.classList.toggle('fs', yes);
    document.body.classList.toggle('fs-lock', yes);
  }

  function isFullscreen() {
    var wrap = $('#videoWrap');
    return !!wrap && wrap.classList.contains('fs');
  }

  function togglePlay() {
    var v = $('#video');
    if (!currentId || currentType !== 'video') return;
    try {
      if (v.paused) { v.play().catch(function () {}); } else { v.pause(); }
    } catch (e) {}
  }

  function setDoneLabel(label, disabled) {
    var btn = $('#markDoneBtn');
    btn.disabled = !!disabled;
    var bl = btn.querySelector('.bl');
    if (bl) bl.textContent = label;
  }

  // ---------------- 确认弹窗（学完/重学，防误点） ----------------
  var askCb = null;

  function askConfirm(opt) {
    var face = $('#askFace');
    if (face) face.textContent = opt.face || '🤔';
    $('#askTitle').textContent = opt.title;
    $('#askSub').textContent = opt.sub || '';
    $('#askOk').textContent = opt.ok || '好';
    $('#askCancel').textContent = opt.cancel || '再想想';
    askCb = opt.onOk || null;
    $('#askModal').classList.remove('hidden');
    if (opt.say !== false) Speech.say(opt.title);
  }

  function closeAsk() {
    $('#askModal').classList.add('hidden');
    askCb = null;
  }

  /** 这一节今天是否已经点过「学完了」 */
  function isDoneToday(id) {
    var it = itemById(id);
    return !!(it && it.done);
  }

  /** 重学：退回未完成，然后进入播放页 */
  function redoLesson(id) {
    state = P.undoComplete(state, catalog, id);
    save(true);
    renderAll();
    openPlayer(id);
    toast('已退回「还没学完」，可以重新学啦');
  }

  function lessonSpeech(l) {
    if (!l) return '';
    var look = lookOf(l.unitName);
    var unit = String(l.unitName || '').replace(/^\d+\s*/, '');
    return '这一节是' + unit + '，' + String(l.title || '').replace(/[，,]/g, '，');
  }

  function openPlayer(id) {
    var it = itemById(id);
    if (!it || it.done) return;
    var l = lessonMap[id];
    if (!l) { toast('找不到这节课的文件'); return; }
    cancelNext();
    Sfx.unlock();
    currentId = id;
    currentType = l.type === 'pdf' ? 'pdf' : 'video';
    completedGuard = false;
    pendingPosition = (state.lessons[id] && state.lessons[id].positionSec) || 0;
    lastPosSave = 0;
    $('#kidView').classList.add('hidden');
    $('#parentView').classList.add('hidden');
    $('#playerView').classList.remove('hidden');
    $('#playerTitle').textContent = l.unitName + ' · ' + l.title;
    $('#videoError').classList.add('hidden');
    $('#vProgressFill').style.width = '0%';
    setDoneLabel('学完了', false);
    window.scrollTo(0, 0);
    Speech.sayLesson(id, lessonSpeech(l));
    if (currentType === 'pdf') openPdf(l); else openVideo(l);
  }

  function openVideo(l) {
    $('#videoWrap').classList.remove('hidden');
    $('#pdfViewer').classList.add('hidden');
    $('#playerHint').textContent = '不想看了可以点左上角返回';
    var v = $('#video');
    v.muted = false;
    $('#muteBtn').textContent = '🔊';
    v.src = encodePath(l.file);
    v.load();
    setPlayingUI(false);
    var pr = v.play();
    if (pr && pr.catch) pr.catch(function () { setPlayingUI(false); });
  }

  function openPdf(l) {
    $('#videoWrap').classList.add('hidden');
    $('#pdfViewer').classList.remove('hidden');
    $('#playerHint').textContent = '左右滑动也能翻页';
    pdfPages = Array.isArray(l.pages) ? l.pages.slice() : [];
    pdfIndex = 0;
    var nativeOnly = pdfPages.length === 0;
    $('#pdfPageWrap').classList.toggle('hidden', nativeOnly);
    $('#pdfNative').classList.toggle('hidden', !nativeOnly);
    document.querySelector('.pdf-nav').classList.toggle('hidden', nativeOnly);
    if (nativeOnly) {
      $('#pdfExternal').setAttribute('href', encodePath(l.file || '#'));
      setDoneLabel('学完了', false);
    } else {
      showPdfPage();
    }
  }

  function showPdfPage() {
    var total = pdfPages.length;
    $('#pdfPageImg').src = encodePath(pdfPages[pdfIndex]);
    $('#pdfPageLabel').textContent = (pdfIndex + 1) + ' / ' + total;
    var first = pdfIndex === 0, last = pdfIndex >= total - 1;
    $('#pdfPrev').disabled = first;
    $('#pdfNext').disabled = last;
    setDoneLabel(last ? '学完了' : '往后翻', !last);
  }

  function turnPdf(delta) {
    if (currentType !== 'pdf' || !pdfPages.length) return;
    var next = pdfIndex + delta;
    if (next < 0 || next >= pdfPages.length) return;
    var wasLast = pdfIndex >= pdfPages.length - 1;
    pdfIndex = next;
    showPdfPage();
    if (!wasLast && pdfIndex >= pdfPages.length - 1) Speech.say('看完了，点绿色的按钮');
  }

  function closePlayer() {
    var v = $('#video');
    try { v.pause(); } catch (e) {}
    setFullscreen(false);
    currentId = null;
    completedGuard = true;
    $('#playerView').classList.add('hidden');
    $('#kidView').classList.remove('hidden');
    $('#pdfPageImg').removeAttribute('src');
    setPlayingUI(false);
    setDoneLabel('学完了', false);
  }

  function completeCurrent(rating) {
    if (!currentId || completedGuard) return;
    completedGuard = true;
    var id = currentId;
    // 先停住视频：闯关界面会盖在上面，声音继续放会打架
    try { $('#video').pause(); } catch (e) {}
    var s = state.settings || {};
    if (s.quizEnabled !== false && Quiz.has(id)) {
      Quiz.start(id, function () { finishLesson(id, rating); });
      return;
    }
    finishLesson(id, rating);
  }

  function finishLesson(id, rating) {
    state = P.completeItem(state, catalog, id, rating);
    save(true);
    closePlayer();
    renderAll();
    if (P.planProgress(state.plan).allDone) return;
    var next = null;
    state.plan.items.forEach(function (it) { if (!next && !it.done) next = it; });
    if (next) showNextOverlay(next);
  }

  function nextPraise() {
    var list = praiseList();
    praiseIdx = (praiseIdx + 1) % list.length;
    return list[praiseIdx];
  }

  function showNextOverlay(item) {
    var l = lessonMap[item.id];
    nextId = item.id;
    var look = lookOf(l ? l.unitName : '');
    var poster = l && l.poster ? encodePath(l.poster) : '';
    $('#nextThumb').innerHTML = poster
      ? '<img src="' + poster + '" alt="">'
      : unitIcon(look, 'next-fallback');
    var praise = nextPraise();
    $('#nextTitle').textContent = praise;
    var doneToday = (state.history[P.todayStr()] || {}).doneCount || 0;
    var rest = doneToday >= 2;
    $('#restTip').classList.toggle('hidden', !rest);
    $('#nextOverlay').classList.remove('hidden');
    confetti(20);
    Sfx.ding();
    Speech.say(praise.replace(/[！!]\s*$/, '') + (rest ? '，休息一下吧' : '，我们继续'), true);

    var left = rest ? 12 : 4;
    $('#nextCountdown').textContent = left + ' 秒后自动继续';
    clearInterval(nextTimer);
    nextTimer = setInterval(function () {
      left--;
      if (left <= 0) {
        cancelNext();
        openPlayer(nextId);
      } else {
        $('#nextCountdown').textContent = left + ' 秒后自动继续';
      }
    }, 1000);
  }
  function cancelNext() {
    clearInterval(nextTimer);
    nextTimer = null;
    $('#nextOverlay').classList.add('hidden');
  }

  // ---------------- 家长端 ----------------
  function askPin() {
    if (!$('#pinModal').classList.contains('hidden')) return;
    $('#pinInput').value = '';
    $('#pinError').textContent = '';
    $('#pinModal').classList.remove('hidden');
    setTimeout(function () { try { $('#pinInput').focus(); } catch (e) {} }, 80);
  }
  function enterParent() {
    cancelNext();
    closePlayer();
    $('#kidView').classList.add('hidden');
    $('#playerView').classList.add('hidden');
    $('#parentView').classList.remove('hidden');
    renderParent();
    window.scrollTo(0, 0);
  }
  function exitParent() {
    $('#parentView').classList.add('hidden');
    $('#kidView').classList.remove('hidden');
    renderAll();
    window.scrollTo(0, 0);
  }

  function exportProgress() {
    var blob = new Blob([JSON.stringify(state, null, 2)], { type: 'application/json' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'studydaily-progress-' + P.todayStr() + '.json';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1500);
    toast('备份已导出，请妥善保存');
  }

  function rebuildToday() {
    var doneItems = (state.plan ? state.plan.items : []).filter(function (i) { return i.done; });
    state.plan = null;
    state = P.buildPlan(state, catalog, P.todayStr()).state;
    var doneIds = {};
    doneItems.forEach(function (d) { doneIds[d.id] = true; });
    state.plan.items = state.plan.items.filter(function (i) { return !doneIds[i.id]; });
    state.plan.items = doneItems.concat(state.plan.items);
    state.plan.plannedMin = state.plan.items.reduce(function (a, b) { return a + b.minutes; }, 0);
    save(true);
    renderAll();
    toast('已按新设置重排今天');
  }

  // ---------------- 事件 ----------------
  function bindEvents() {
    // 第一次触摸时解锁音频（移动端禁止无交互自动播放）
    var unlockOnce = function () {
      Speech.unlock();
      Sfx.unlock();
      document.removeEventListener('pointerdown', unlockOnce, true);
      document.removeEventListener('touchstart', unlockOnce, true);
    };
    document.addEventListener('pointerdown', unlockOnce, true);
    document.addEventListener('touchstart', unlockOnce, true);

    // 首次点击解锁音频（安卓要求用户手势后才能出声）
    document.addEventListener('touchstart', function once() {
      Sfx.unlock();
      document.removeEventListener('touchstart', once);
    }, { passive: true });
    document.addEventListener('mousedown', function once2() {
      Sfx.unlock();
      document.removeEventListener('mousedown', once2);
    });

    // 大主按钮 -> 进第一节
    $('#startBtn').addEventListener('click', function (e) {
      var id = e.currentTarget.getAttribute('data-id');
      Sfx.tap();
      if (id) openPlayer(id);
    });

    // ---- 练习册 ----
    $('#wbEntry').addEventListener('click', function () { Sfx.tap(); WB.showShelf(); });
    $('#wbBack').addEventListener('click', function () { Sfx.tap(); WB.closeShelf(); });
    $('#wbShelfBack').addEventListener('click', function () { Sfx.tap(); WB.backToShelf(); });
    $('#wbPrev').addEventListener('click', function () { WB.turn(-1); });
    $('#wbNext').addEventListener('click', function () { WB.turn(1); });
    $('#wbGrid').addEventListener('click', function (e) {
      var b = e.target.closest('[data-wb]');
      if (!b) return;
      Sfx.tap();
      WB.openBook(b.getAttribute('data-wb'));
    });

    // ---- 闯关小游戏 ----
    $('#qzClose').addEventListener('click', function () { Sfx.tap(); Quiz.close(true); });
    $('#qzGo').addEventListener('click', function () { Sfx.tap(); Quiz.close(false); });
    $('#qzAgain').addEventListener('click', function () {
      Sfx.tap();
      $('#qzFinish').classList.add('hidden');
      Quiz.restart();
    });
    $('#qzReplay').addEventListener('click', function (e) { e.stopPropagation(); Sfx.tap(); Quiz.replay(); });
    $('#qzSayText').addEventListener('click', function () { Quiz.replay(); });

    // 今日卡片
    $('#todayList').addEventListener('click', function (e) {
      var card = e.target.closest('[data-id]');
      if (!card) return;
      var it = itemById(card.getAttribute('data-id'));
      if (!it) return;
      Sfx.tap();
      if (it.done) {
        askConfirm({
          face: '🤔',
          title: '这一节已经学完啦',
          sub: '要重新学一遍吗？重学会清掉这节课今天的记录',
          ok: '重学一遍',
          cancel: '不用了',
          onOk: function () { redoLesson(it.id); }
        });
        return;
      }
      openPlayer(it.id);
    });

    // 引导
    $('#guideOk').addEventListener('click', function () { closeGuide(); Sfx.tap(); });
    $('#guideLayer').addEventListener('click', function (e) {
      if (e.target.id === 'guideLayer') closeGuide();
    });

    // 声音开关
    $('#soundBtn').addEventListener('click', function () {
      var on = !Speech.isEnabled();
      state.settings.speechEnabled = on;
      Speech.setEnabled(on);
      Sfx.unlock();
      $('#soundBtn').textContent = on ? '🔊' : '🔇';
      $('#soundBtn').classList.toggle('off', !on);
      if (on) Speech.say('声音打开啦', true);
      save();
      toast(on ? '语音已打开' : '语音已关闭');
    });

    // 播放页
    $('#backBtn').addEventListener('click', function () {
      cancelNext(); Speech.stop(); closePlayer(); renderAll();
    });
    $('#videoTapLayer').addEventListener('click', function () { togglePlay(); });
    $('#fsBtn').addEventListener('click', function (e) {
      e.stopPropagation();
      Sfx.tap();
      setFullscreen(true);
    });
    $('#fsExit').addEventListener('click', function (e) {
      e.stopPropagation();
      Sfx.tap();
      setFullscreen(false);
    });
    $('#muteBtn').addEventListener('click', function (e) {
      e.stopPropagation();
      var v = $('#video');
      v.muted = !v.muted;
      $('#muteBtn').textContent = v.muted ? '🔇' : '🔊';
    });
    $('#markDoneBtn').addEventListener('click', function () {
      Sfx.tap();
      // 先问一句，防止小孩手快误点（确认后才真正记账）
      askConfirm({
        face: '🎉',
        title: '这一节学完了吗？',
        sub: '点「学完啦」才会记下来哦',
        ok: '学完啦 ✓',
        cancel: '还想再看看',
        onOk: function () { completeCurrent('good'); }
      });
    });
    $('#askOk').addEventListener('click', function () {
      Sfx.tap();
      var cb = askCb;
      closeAsk();
      if (cb) cb();
    });
    $('#askCancel').addEventListener('click', function () { Sfx.tap(); closeAsk(); });
    $('#markAgainBtn').addEventListener('click', function () {
      if (currentType !== 'video') { toast('这一节可以左右滑动翻页'); return; }
      var v = $('#video');
      try { v.currentTime = 0; v.play().catch(function () {}); } catch (e) {}
    });
    $('#seeStatsBtn').addEventListener('click', function () { askPinThen('stats'); });

    // PDF 翻页：按钮 + 左右滑动 + 键盘方向键
    $('#pdfPrev').addEventListener('click', function () { turnPdf(-1); });
    $('#pdfNext').addEventListener('click', function () { turnPdf(1); });
    var touchX = 0, touchY = 0;
    $('#pdfPageWrap').addEventListener('touchstart', function (e) {
      touchX = e.touches[0].clientX;
      touchY = e.touches[0].clientY;
    }, { passive: true });
    $('#pdfPageWrap').addEventListener('touchend', function (e) {
      var dx = e.changedTouches[0].clientX - touchX;
      var dy = e.changedTouches[0].clientY - touchY;
      if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) turnPdf(dx < 0 ? 1 : -1);
    }, { passive: true });
    document.addEventListener('keydown', function (e) {
      if (currentType !== 'pdf' || $('#playerView').classList.contains('hidden')) return;
      if (e.key === 'ArrowRight') turnPdf(1);
      else if (e.key === 'ArrowLeft') turnPdf(-1);
    });

    // 自动下一节
    $('#nextNow').addEventListener('click', function () {
      var id = nextId; cancelNext(); Sfx.tap(); if (id) openPlayer(id);
    });
    $('#nextLater').addEventListener('click', function () {
      cancelNext(); Speech.stop(); renderAll(); toast('好的，休息一下再继续');
    });

    // 家长入口：连点 5 次 或 长按 1.5 秒
    var pb = $('#parentBtn');
    pb.addEventListener('click', function () {
      tapCount++;
      clearTimeout(tapTimer);
      tapTimer = setTimeout(function () { tapCount = 0; }, 2000);
      if (tapCount >= 5) { tapCount = 0; askPin(); }
    });
    pb.addEventListener('touchstart', function () {
      pressTimer = setTimeout(function () { askPin(); }, 1500);
    }, { passive: true });
    ['touchend', 'touchcancel'].forEach(function (ev) {
      pb.addEventListener(ev, function () { clearTimeout(pressTimer); }, { passive: true });
    });

    // PIN
    $('#pinOk').addEventListener('click', function () {
      if ($('#pinInput').value.trim() === String(state.settings.pin)) {
        $('#pinModal').classList.add('hidden');
        if (pendingTab) { switchTab(pendingTab); pendingTab = null; }
        enterParent();
      } else {
        $('#pinError').textContent = 'PIN 不对，再试一次（默认 1234）';
      }
    });
    $('#pinCancel').addEventListener('click', function () {
      pendingTab = null;
      $('#pinModal').classList.add('hidden');
    });
    $('#pinInput').addEventListener('keydown', function (e) {
      if (e.key === 'Enter') $('#pinOk').click();
    });

    $('#exitParent').addEventListener('click', exitParent);
    $('#tabs').addEventListener('click', function (e) {
      var b = e.target.closest('[data-tab]');
      if (b) switchTab(b.getAttribute('data-tab'));
    });

    // 家长端列表操作（事件委托）
    ['#tab-today', '#tab-courses'].forEach(function (sel) {
      $(sel).addEventListener('click', function (e) {
        var b = e.target.closest('[data-action]');
        if (!b) return;
        var id = b.getAttribute('data-id');
        var action = b.getAttribute('data-action');
        if (action === 'up') state = P.moveItem(state, catalog, id, -1);
        else if (action === 'down') state = P.moveItem(state, catalog, id, 1);
        else if (action === 'skip') { state = P.skipItem(state, catalog, id); toast('已跳过这节'); }
        else if (action === 'done') state = P.completeItem(state, catalog, id, 'good');
        else if (action === 'again') { state = P.completeItem(state, catalog, id, 'again'); toast('已标记没学会，明天会重看'); }
        else if (action === 'add') { state = P.addToPlan(state, catalog, id, 'new'); toast('已加到今天'); }
        else if (action === 'restore') {
          state.skipped = state.skipped.filter(function (x) { return x !== id; });
          toast('已恢复，之后会重新安排');
        }
        else if (action === 'quiz') {
          // 家长预览这节课的 3 道小题（不计入孩子成绩）
          Sfx.tap();
          Quiz.start(id, function () {}, true);
          return;
        }
        save(true);
        renderAll();
      });
    });

    // 设置
    $('#saveSettings').addEventListener('click', function () {
      var s = state.settings;
      s.weekdayMinutes = Number($('#setWeekday').value) || 30;
      s.weekendMinutes = Number($('#setWeekend').value) || 15;
      s.completionRatio = Math.max(0.5, Math.min(1, (Number($('#setRatio').value) || 90) / 100));
      s.reviewEnabled = $('#setReview').checked;
      s.maxReviewsPerDay = Math.max(0, Math.min(6, Number($('#setMaxReview').value) || 2));
      var fr = $('#setFriday').value;
      s.fridayFocus = ['auto', 'math', 'pinyin'].indexOf(fr) >= 0 ? fr : 'auto';
      s.speechEnabled = $('#setSpeech').checked;
      s.soundEnabled = $('#setSound').checked;
      s.quizEnabled = $('#setQuiz').checked;
      var pin = ($('#setPin').value || '').replace(/\D/g, '').slice(0, 4);
      s.pin = pin.length === 4 ? pin : '1234';
      state = P.normalize(state, catalog);
      syncAudioSettings();
      save(true);
      // 今天正好是周五且改了「周五搭配」时，立即按新侧重重排今天
      if (new Date().getDay() === 5 && s.fridayFocus !== 'auto') {
        rebuildToday();
        return;
      }
      renderAll();
      toast('设置已保存，明天起按新时长排课');
    });
    $('#rebuildToday').addEventListener('click', rebuildToday);
    var cc = $('#cacheClear');
    if (cc) cc.addEventListener('click', clearCache);
    var le = $('#localEnable');
    if (le) le.addEventListener('click', enableLocal);

    // 数据
    $('#exportBtn').addEventListener('click', exportProgress);
    $('#importBtn').addEventListener('click', function () { $('#importFile').click(); });
    $('#importFile').addEventListener('change', function (e) {
      var f = e.target.files && e.target.files[0];
      if (!f) return;
      var reader = new FileReader();
      reader.onload = function () {
        try {
          var data = JSON.parse(reader.result);
          if (!data || typeof data !== 'object') throw new Error('bad');
          state = P.normalize(data, catalog);
          state = P.buildPlan(state, catalog, P.todayStr()).state;
          syncAudioSettings();
          save(true); renderAll();
          toast('备份已恢复');
        } catch (err) { toast('这个文件不是有效的进度备份'); }
      };
      reader.readAsText(f);
      e.target.value = '';
    });
    $('#resetBtn').addEventListener('click', function () {
      if (!confirm('确定清空所有学习记录？此操作不可撤销，建议先导出备份。')) return;
      state = {};
      state = P.buildPlan(state, catalog, P.todayStr()).state;
      save(true); renderAll();
      toast('已清空，重新开始');
    });
  }

  var pendingTab = null;
  function askPinThen(tab) {
    pendingTab = tab;
    askPin();
  }
  function switchTab(tab) {
    activeTab = tab;
    $$('#tabs button').forEach(function (b) {
      b.classList.toggle('active', b.getAttribute('data-tab') === tab);
    });
    $$('.tabpane').forEach(function (p) {
      p.classList.toggle('active', p.id === 'tab-' + tab);
    });
    renderParent();
    window.scrollTo(0, 0);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
