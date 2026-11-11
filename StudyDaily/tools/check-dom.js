/*
 * 静态检查：app.js 里所有 $('#id') 引用必须在 index.html 中存在，
 * 以及 tab 名称与 tabpane id 一一对应。避免上线后才发现拼写错误。
 */
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const js = fs.readFileSync(path.join(root, 'app.js'), 'utf8');

const ids = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]));
const refs = [...new Set([...js.matchAll(/\$\('#([A-Za-z0-9_-]+)'\)/g)].map(m => m[1]))];
const missing = refs.filter(id => !ids.has(id));

const tabs = [...html.matchAll(/data-tab="([^"]+)"/g)].map(m => m[1]);
const missingPanes = tabs.filter(t => !ids.has('tab-' + t));

console.log(`HTML 中 id 数量: ${ids.size}，app.js 静态引用: ${refs.length}`);
if (missing.length) {
  console.error('[错误] app.js 引用了不存在的 id: ' + missing.join(', '));
  process.exit(1);
}
if (missingPanes.length) {
  console.error('[错误] 缺少 tabpane: ' + missingPanes.map(t => 'tab-' + t).join(', '));
  process.exit(1);
}
console.log('OK：DOM 引用全部匹配，' + tabs.length + ' 个家长端标签页齐全');
