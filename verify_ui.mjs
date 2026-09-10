// 书到了 UI 视图开关验证脚本
// 覆盖：63/54 单选 + 特教开关 + 学生/教师用书二态 + 互斥防呆置灰 +
//       localStorage 持久化/旧存档迁移 + 清单徽标与脱节回收
// 运行：node verify_ui.mjs（用系统 Edge，无需下载浏览器；服务需已启动）
// 基线数字为 2026-09 目录缓存实测：默认视图 2162 / 54常规学生 288 /
// 特教 300（特教无五四、无教师用书）；教师用书 63常规 498 / 54常规 52。
import { createRequire } from 'module';
const require = createRequire('C:/Users/SAKE/.workbuddy/binaries/node/workspace/');
const { chromium } = require('playwright-core');

const BASE = 'http://127.0.0.1:8765';
const OUT = 'D:/01_Projects/smartedu-教材下载器/.playwright-cli';
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 950 } });

const total = async () => (await page.textContent('#cat-count')).trim();
const states = async () => ({
  s63: await page.getAttribute('#switch-63', 'aria-pressed'),
  s54: await page.getAttribute('#switch-54', 'aria-pressed'),
  spec: await page.getAttribute('#switch-spec', 'aria-pressed'),
  stu: await page.getAttribute('#switch-stu', 'aria-pressed'),
  tea: await page.getAttribute('#switch-tea', 'aria-pressed'),
});
const blocked = async sel => (await page.getAttribute(sel, 'aria-disabled')) === 'true';
const toastText = async () => {
  try { return (await page.textContent('#toast', { timeout: 2000 })).trim(); }
  catch { return ''; }
};
const waitIdle = () => page.waitForTimeout(1500);
let failed = 0;
const expect = (cond, label, detail = '') => {
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${label}${detail ? '  [' + detail + ']' : ''}`);
  if (!cond) failed++;
};

// 清掉旧存档，从干净默认态开始
await page.goto(BASE + '/#', { waitUntil: 'domcontentloaded' });
await page.evaluate(() => { localStorage.removeItem('shudaole.views'); localStorage.removeItem('shudaole.added'); });

await page.goto(BASE, { waitUntil: 'networkidle' });
await page.waitForFunction(() => document.querySelector('#cat-count')?.textContent.includes('命中'), null, { timeout: 60000 });

console.log('1) 默认态');
let s = await states();
expect(s.s63 === 'true' && s.s54 === 'false' && s.spec === 'false', '默认 63 亮、54/特教灭', JSON.stringify(s));
expect(s.stu === 'true' && s.tea === 'false', '默认 学生用书 亮', JSON.stringify(s));
expect((await total()).includes('2162'), '默认命中 2162（已排除教师用书）', await total());
expect(await page.isHidden('#btn-cart'), '清单徽标初始隐藏');
await page.screenshot({ path: OUT + '/view-normal.png' });

console.log('2) 清单徽标 + 脱节回收');
await page.click('#btn-add-all');
await waitIdle();
expect(await page.isVisible('#btn-cart'), '全部加入后徽标出现');
expect((await page.textContent('#cart-n')).trim() === '2162', '徽标计数 2162', await page.textContent('#cart-n'));
expect((await page.textContent('#cart-summary')).includes('清单构成'), '卡片③出现构成摘要');
await page.click('#btn-clear-links');
await waitIdle();
expect(await page.isHidden('#btn-cart'), '清空链接后徽标回收（H4）');
expect(await page.isHidden('#cart-summary'), '清空链接后摘要回收');

console.log('3) 切 54');
await page.click('#switch-54');
await waitIdle();
s = await states();
expect(s.s54 === 'true' && s.s63 === 'false', '54 亮、63 灭', JSON.stringify(s));
expect((await total()).includes('288'), '54+常规+学生命中 288', await total());
console.log('   年级候选:', await page.$$eval('#f-grade option', os => os.map(o => o.textContent.trim()).join(' | ')));
await page.screenshot({ path: OUT + '/view-54.png' });

console.log('4) 重复点击已选中 54（不应动作）');
await page.click('#switch-54');
await waitIdle();
expect((await total()).includes('288'), '命中不变', await total());

console.log('5) 63 + 特教：互斥防呆');
await page.click('#switch-63');
await waitIdle();
await page.click('#switch-spec');
await waitIdle();
s = await states();
expect(s.s63 === 'true' && s.spec === 'true', '63+特教点亮', JSON.stringify(s));
expect((await total()).includes('300'), '特教命中 300', await total());
expect(await blocked('#switch-54'), '特教视图下五四按钮置灰（结构性死集防呆）');
// Playwright 对 aria-disabled 元素拒绝常规点击；force 模拟真实用户强行点击，
// 验证 guarded handler 会拦截并给出 toast 解释
await page.click('#switch-54', { force: true });
await waitIdle();
s = await states();
expect(s.s63 === 'true' && s.s54 === 'false', '点置灰五四不切换', JSON.stringify(s));
expect((await toastText()).includes('五四'), '置灰点击有 toast 解释', await toastText());
expect(await blocked('#switch-tea'), '特教视图下教师用书置灰（特教无教师用书）');
await page.click('#switch-tea', { force: true });
await waitIdle();
s = await states();
expect(s.stu === 'true' && s.tea === 'false', '点置灰教师用书不切换', JSON.stringify(s));
await page.screenshot({ path: OUT + '/view-spec.png' });

console.log('6) 刷新持久化（含 tea 字段）');
await page.reload({ waitUntil: 'networkidle' });
await page.waitForFunction(() => document.querySelector('#cat-count')?.textContent.includes('命中'), null, { timeout: 60000 });
s = await states();
expect(s.s63 === 'true' && s.spec === 'true' && s.stu === 'true', '刷新后仍 63+特教+学生', JSON.stringify(s));
const saved = JSON.parse(await page.evaluate(() => localStorage.getItem('shudaole.views')));
expect(saved.spec === true && saved.tea === false, '存档含 spec/tea 字段', JSON.stringify(saved));

console.log('7) 旧版存档迁移 + 54 视图下特教置灰');
await page.evaluate(() => localStorage.setItem('shudaole.views', JSON.stringify({ sys54: true, spec: false })));
await page.reload({ waitUntil: 'networkidle' });
await page.waitForFunction(() => document.querySelector('#cat-count')?.textContent.includes('命中'), null, { timeout: 60000 });
s = await states();
expect(s.s54 === 'true', '旧存档 {sys54:true} 迁移为 54 亮', JSON.stringify(s));
expect(await blocked('#switch-spec'), '54 视图下特教按钮置灰');
await page.click('#switch-spec', { force: true });
await waitIdle();
s = await states();
expect(s.spec === 'false', '点置灰特教不切换', JSON.stringify(s));

console.log('8) 教师用书视图');
await page.click('#switch-63');
await waitIdle();
await page.click('#switch-tea');
await waitIdle();
s = await states();
expect(s.tea === 'true' && s.stu === 'false' && s.spec === 'false', '教师用书亮', JSON.stringify(s));
expect((await total()).includes('498'), '63+常规+教师命中 498', await total());
expect(await page.isVisible('#btn-export-csv'), '结果非空时导出 CSV 可见');
await page.screenshot({ path: OUT + '/view-tea.png' });

// 9) 高中视图：年级覆盖率过低时年级下拉隐藏
await page.evaluate(() => localStorage.removeItem('shudaole.views'));
await page.reload({ waitUntil: 'networkidle' });
await page.waitForFunction(() => document.querySelector('#cat-count')?.textContent.includes('命中'), null, { timeout: 60000 });
// 默认小学视图：年级下拉应可见
expect(await page.isVisible('label[data-dim="grade"]'), '小学视图年级下拉可见');
await page.selectOption('#f-phase', '高中');
await page.waitForTimeout(2000);
expect(await page.isHidden('label[data-dim="grade"]'), '高中视图年级下拉隐藏（覆盖率不足 50%）');
expect(await page.isVisible('label[data-dim="subject"]'), '高中视图科目下拉仍可见');
// 切回小学，年级下拉恢复
await page.selectOption('#f-phase', '');
await page.waitForTimeout(2000);
expect(await page.isVisible('label[data-dim="grade"]'), '切回全部后年级下拉恢复');
await page.screenshot({ path: OUT + '/view-senior-hidden-grade.png' });

// 10) 版本下拉按命中数降序（前 3 项计数不递增）
const pubOpts = await page.$$eval('#f-publisher option:not([value=""])', os =>
  os.map(o => {
    const m = o.textContent.match(/（(\d+)）$/);
    return { v: o.value, c: m ? +m[1] : 0 };
  }));
const first3 = pubOpts.slice(0, 3);
if (first3.length >= 3) {
  expect(first3[0].c >= first3[1].c && first3[1].c >= first3[2].c,
    '版本下拉前 3 项按计数降序',
    first3.map(o => o.v + '(' + o.c + ')').join(' > '));
}

// 11) 常用组合：替换式应用（先选年级，再点芯片后全部被替换）
// page.fill 不触发 app 的 oninput handler，用 page.evaluate 确保状态一致
await page.evaluate(() => localStorage.removeItem('shudaole.views'));
await page.reload({ waitUntil: 'networkidle' });
await page.waitForFunction(() => document.querySelector('#cat-count')?.textContent.includes('命中'), null, { timeout: 60000 });
await page.selectOption('#f-grade', '一年级');
// selectOption 触发 onchange → reload，等计数从默认值变化
const prevTotal = await total();
await page.waitForFunction(
  (prev) => (document.querySelector('#cat-count')?.textContent || '').trim() !== prev,
  prevTotal, { timeout: 10000 });
await page.waitForTimeout(300);
// 用 evaluate 读取，避免 JSHandle 解析的编码问题
const oldTotal = await page.evaluate(() => (document.querySelector('#cat-count')?.textContent || '').trim());
const hasHit = oldTotal.indexOf('命中') >= 0;
const notZero = oldTotal.indexOf('命中 0 条') < 0;
expect(hasHit && notZero, '预选条件有命中', oldTotal);
const comboBtn = await page.$('button.js-combo');
if (comboBtn) {
  const comboText = await comboBtn.textContent();
  console.log('   combo:', comboText);
  await comboBtn.click();
  // 等 reload 完成 + 竞态响应全部到达
  await page.waitForFunction(() => document.querySelector('#cat-count')?.textContent.includes('命中'), null, { timeout: 10000 });
  await page.waitForTimeout(1500);
  s = await states();
  expect(s.stu === 'true', '组合不重置用书视图', JSON.stringify(s));
  const newTotal = await total();
  expect(newTotal.includes('命中') && !newTotal.includes('0 条'), '组合替换后有命中', newTotal);
  const gradeVal = await page.$eval('#f-grade', el => el.value);
  expect(gradeVal === '', '组合清空了年级', JSON.stringify(gradeVal));
  const pubVal = await page.$eval('#f-publisher', el => el.value);
  expect(pubVal !== '', '组合设置了版本', JSON.stringify(pubVal));
  const chipsText = await page.textContent('#filter-chips').catch(() => '');
  expect(chipsText.includes('版本'), 'chips 显示版本条件', chipsText);
  await page.screenshot({ path: OUT + '/view-combo-after.png' });
}

// 收尾：清存档回默认
await page.evaluate(() => { localStorage.removeItem('shudaole.views'); localStorage.removeItem('shudaole.added'); });
await browser.close();
console.log(failed ? `DONE（${failed} 项失败）` : 'DONE 全部通过');
process.exit(failed ? 1 : 0);
