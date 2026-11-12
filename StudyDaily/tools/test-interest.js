/* 兴趣课堂端到端验证：入口 -> 书架 -> 列表 -> 播放 -> 打勾 -> 记录进度 */
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({
    args: ['--no-proxy-server', '--autoplay-policy=no-user-gesture-required'],
  });
  const page = await browser.newPage({ viewport: { width: 1024, height: 768 } });
  const base = 'http://127.0.0.1:8720/';
  const fails = [];
  const ok = (name, cond) => {
    console.log((cond ? 'PASS' : 'FAIL') + ' ' + name);
    if (!cond) fails.push(name);
  };

  await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 20000 });
  await page.waitForTimeout(1500);

  ok('兴趣入口显示', await page.locator('#itEntry').isVisible());
  await page.locator('#itEntry').click();
  await page.waitForTimeout(600);
  ok('书架打开', await page.locator('#itView').isVisible());
  const unitCount = await page.locator('#itGrid [data-it-unit]').count();
  ok('书架栏目>0 (' + unitCount + ')', unitCount > 0);

  // 点第一个栏目
  const firstUnit = page.locator('#itGrid [data-it-unit]').first();
  const unitName = (await firstUnit.textContent() || '').trim();
  await firstUnit.click();
  await page.waitForTimeout(600);
  ok('课单打开', await page.locator('#itListView').isVisible());
  const lessonCount = await page.locator('#itLessonGrid .kcard').count();
  ok('课单>0 (' + lessonCount + ')', lessonCount > 0);

  // 点第一课播放
  await page.locator('#itLessonGrid .kcard').first().click();
  await page.waitForTimeout(2500);
  ok('播放页打开', await page.locator('#playerView').isVisible());
  const vstate = await page.evaluate(() => {
    const v = document.getElementById('video');
    return { src: v.src, time: v.currentTime, paused: v.paused, err: v.error && v.error.code };
  });
  ok('视频源是兴趣课 ' + vstate.src, /videos\/i\d\d\//.test(vstate.src));
  ok('视频在播', !vstate.paused);
  ok('无加载错误', !vstate.err);

  // 等 3 秒确认进度条在走 + 快进到结尾触发自动打勾
  await page.waitForTimeout(3000);
  const t2 = await page.evaluate(() => document.getElementById('video').currentTime);
  ok('进度推进 (' + t2.toFixed(1) + 's)', t2 > vstate.time);

  await page.evaluate(() => {
    const v = document.getElementById('video');
    if (isFinite(v.duration)) v.currentTime = Math.max(0, v.duration - 2);
  });
  await page.waitForTimeout(4000);
  const marked = await page.evaluate(() => {
    const s = JSON.parse(localStorage.getItem('studydaily.state.v1') || '{}');
    return s.interest && Object.keys(s.interest).length;
  });
  ok('看完自动打勾 (' + marked + ')', marked > 0);
  ok('回到兴趣列表', await page.locator('#itListView').isVisible());
  ok('列表出现勾', await page.locator('#itLessonGrid .kcard.done').count() > 0);

  // 返回书架 -> 返回首页
  await page.locator('#itListBack').click();
  await page.waitForTimeout(400);
  await page.locator('#itBack').click();
  await page.waitForTimeout(400);
  ok('返回首页', await page.locator('#kidView').isVisible());

  // 主课排课不受影响：今日任务里没有 i 开头的课
  const planIds = await page.evaluate(() => {
    const s = JSON.parse(localStorage.getItem('studydaily.state.v1') || '{}');
    return (s.plan && s.plan.items || []).map(i => i.id);
  });
  ok('今日任务无兴趣课', planIds.every(id => !/^i\d/.test(id)));

  console.log(fails.length ? '\nFAILED: ' + fails.length : '\nALL PASS');
  await page.screenshot({ path: 'interest-test.png', fullPage: false });
  await browser.close();
  process.exit(fails.length ? 1 : 0);
})().catch(e => { console.error('ERROR', e.message); process.exit(2); });
