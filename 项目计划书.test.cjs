const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {execFileSync} = require('node:child_process');

const source = fs.readFileSync(path.join(__dirname, '项目计划书.html'), 'utf8');
const projectPlan = fs.readFileSync(path.join(__dirname, '项目规划.md'), 'utf8');
const progressBackup = fs.readFileSync(path.join(__dirname, '项目进度备份.json'), 'utf8');
const script = source.match(/<script>([\s\S]*?)<\/script>/)[1];
new (require('node:vm').Script)(script);
assert.equal((projectPlan.match(/^### M0[1-7] /gm) || []).length, 7);
assert(source.includes('前端原型图.pen'), '计划书须展示前端原型基线');
assert(fs.existsSync(path.join(__dirname, '前端原型图.pen')), '前端原型文件须存在');
assert(!/<script[^>]+src=|<link[^>]+stylesheet/i.test(source), '页面应可独立离线打开');

const browser = [
  process.env.PROJECT_PLAN_TEST_BROWSER,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe'
].filter(Boolean).find(file => fs.existsSync(file));
assert(browser, '未找到本地 Chrome/Edge；可用 PROJECT_PLAN_TEST_BROWSER 指定路径');

const tempRoot = path.resolve(os.tmpdir());
const temp = fs.mkdtempSync(path.join(tempRoot, 'project-plan-test-'));
const page = path.join(temp, 'plan.html');

function runBrowser(test, extra = []) {
  fs.writeFileSync(page, source.replace('</body>', '<script>' + test + '</script></body>'), 'utf8');
  const output = execFileSync(browser, [
    '--headless', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--disable-background-networking', '--disable-component-update',
    '--user-data-dir=' + path.join(temp, 'profile'), '--window-size=1440,1500',
    '--virtual-time-budget=4000', '--dump-dom', ...extra, pathToFileURL(page).href
  ], {encoding:'utf8', windowsHide:true, timeout:45000, maxBuffer:8 * 1024 * 1024, stdio:['ignore','pipe','pipe']});
  const match = output.match(/data-self-test="([^"]+)"/);
  assert(match, '浏览器未返回自检结果');
  const result = JSON.parse(decodeURIComponent(match[1]));
  assert.equal(result.error, undefined, result.error);
  console.log(result.passed.join('\n'));
}

const firstRun = `(async () => {
  const passed = [];
  function check(condition, name) { if (!condition) throw new Error(name); passed.push('PASS ' + name); }
  function input(id, value, type = 'input') { $(id).value = value; $(id).dispatchEvent(new Event(type,{bubbles:true})); }
  function rejects(value) { try { validateState(value); return false; } catch { return true; } }
  try {
    check(document.querySelectorAll('.module').length === 7 && $('progress').value === 7 && state.modules[6].status === '模块验收通过', '默认反映 M01—M07 电脑端功能验收通过');
    check(validateState(JSON.parse(${JSON.stringify(progressBackup)})).modules.filter(row => row.status === '模块验收通过').length === 7, '仓库进度备份可直接导入');
    check(state.modules.slice(0,2).every(row => row.report.includes('验收报告')), '已通过模块附实际报告位置');
    state.modules.forEach(row => {row.status='未开始';row.note='';row.report='';row.updatedAt=null;});
    renderModules(); updateOverview();
    check([...document.querySelectorAll('select,textarea,input[type="text"]')].every(field => field.labels.length > 0), '所有可编辑字段有可关联标签');
    input('M01-status','模块验收通过','change');
    check(state.modules[0].status === '未开始' && $('M01-status').value === '未开始' && $('M01-report') === document.activeElement, '无报告不能登记验收通过并定位到字段');
    input('M01-report','docs/验收报告/M01/批次/验收报告.md');
    input('M01-status','开发中','change');
    check($('working-count').textContent.includes('1'), '开发中统计实时更新');
    input('M01-status','模块验收通过','change');
    check($('progress').value === 1 && $('percentage').textContent === '14%' && !$('notice').classList.contains('error'), '验收通过进度实时更新且旧校验错误清除');
    input('M01-report','');
    check(state.modules[0].status === '待验收' && $('progress').value === 0, '清空报告撤回完成状态');
    input('M01-report','docs/验收报告/M01/真实报告.md');
    input('M01-status','模块验收通过','change');
    input('M01-note','<img src=x onerror="window.injected=true">');
    check(!document.querySelector('#M01 img') && !window.injected, '导入及输入文字不作为 HTML 执行');
    check(JSON.parse(localStorage.getItem(STORAGE_KEY)).modules[0].status === '模块验收通过', '修改实际保存到浏览器');
    const good = JSON.parse(JSON.stringify(state));
    const invalid = [];
    let bad = structuredClone(good); bad.modules[1].id = 'M01'; invalid.push(bad);
    bad = structuredClone(good); bad.modules[0].status = '伪造状态'; invalid.push(bad);
    bad = structuredClone(good); bad.modules[0].note = 'a'.repeat(4001); invalid.push(bad);
    bad = structuredClone(good); bad.modules[0].report = ''; invalid.push(bad);
    bad = structuredClone(good); bad.projectId = 'other-project'; invalid.push(bad);
    bad = structuredClone(good); bad.prdVersion = 'V9'; invalid.push(bad);
    bad = structuredClone(good); bad.updatedAt = 'invalid-date'; invalid.push(bad);
    bad = structuredClone(good); bad.updatedAt = '2026-02-30T00:00:00.000Z'; invalid.push(bad);
    check(invalid.every(rejects), '拒绝重复 ID、非法状态、超长文字、缺报告、错误项目/版本/日期');
    const before = JSON.stringify(state);
    try { importBackup(JSON.stringify(invalid[0])); } catch {}
    check(JSON.stringify(state) === before && localStorage.getItem(STORAGE_KEY) === before, '坏备份不破坏现有进度');
    window.confirm = () => false;
    check(importBackup(JSON.stringify(initialState())) === false && JSON.stringify(state) === before, '取消导入保留当前数据');
    window.confirm = () => true;
    const legacy = structuredClone(good);
    legacy.prdVersion = 'V1.4'; legacy.planVersion = 'V1.0';
    const normalized = validateState(legacy);
    check(normalized.prdVersion === 'V1.5' && normalized.planVersion === 'V1.1' && JSON.stringify(normalized.modules) === JSON.stringify(good.modules), '旧备份升级基线并保留七模块原始记录');
    check(importBackup(JSON.stringify(legacy)) && $('notice').textContent.includes('补充原型') && state.modules[0].status === '模块验收通过', '旧备份导入保留登记并提示新增检查复核');
    good.modules.reverse();
    check(importBackup(JSON.stringify(good)) && state.modules[0].id === 'M01' && $('progress').value === 1, '备份导入并恢复正确模块顺序');
    const originalSet = Storage.prototype.setItem;
    Storage.prototype.setItem = () => { throw new Error('quota'); };
    input('M02-note','浏览器保存失败时仍保留页面修改');
    check(state.modules[1].note.includes('页面修改') && $('save-state').textContent.includes('未能保存'), '存储失败明确提示并保留内存进度');
    Storage.prototype.setItem = originalSet;
    saveState();
    check($('save-state').textContent.includes('已保存') && !$('notice').classList.contains('error'), '保存恢复后清除过期失败提示');
    const originalCreate = URL.createObjectURL;
    const originalClick = HTMLAnchorElement.prototype.click;
    let exported, filename;
    URL.createObjectURL = blob => { exported = blob; return 'blob:test'; };
    HTMLAnchorElement.prototype.click = function () { filename = this.download; };
    downloadBackup();
    check(validateState(JSON.parse(await exported.text())).modules[0].status === '模块验收通过' && filename.endsWith('.json'), '导出得到可重新导入的实际 JSON 备份');
    localStorage.setItem(STORAGE_KEY,'{broken');
    state = loadState(); renderModules(); updateOverview();
    check(damagedRecord === '{broken' && $('M01-status').disabled && localStorage.getItem(STORAGE_KEY) === '{broken', '损坏旧记录保留且暂停编辑');
    downloadBackup();
    check(await exported.text() === '{broken', '损坏记录仍可导出原文');
    URL.createObjectURL = originalCreate;
    HTMLAnchorElement.prototype.click = originalClick;
    check(importBackup(JSON.stringify(initialState())) && !$('M01-status').disabled, '有效备份可以恢复编辑');
    input('M03-note','跨浏览器进程持久化复测');
    document.body.dataset.selfTest = encodeURIComponent(JSON.stringify({passed}));
  } catch(error) { document.body.dataset.selfTest = encodeURIComponent(JSON.stringify({passed,error:error.stack})); }
})();`;

try {
  runBrowser(firstRun);
  runBrowser(`(() => {
    try {
      if (state.modules[2].note !== '跨浏览器进程持久化复测' || $('M03-note').value !== state.modules[2].note) throw new Error('重开页面未恢复记录');
      document.body.dataset.selfTest = encodeURIComponent(JSON.stringify({passed:['PASS 重开浏览器进程后自动恢复记录']}));
    } catch(error) { document.body.dataset.selfTest = encodeURIComponent(JSON.stringify({passed:[],error:error.stack})); }
  })();`);
  console.log('PASS JavaScript 语法、离线资源、规划模块数量与原型约束');
} finally {
  assert.equal(path.dirname(path.resolve(temp)), tempRoot);
  assert(path.basename(temp).startsWith('project-plan-test-'));
  fs.rmSync(temp,{recursive:true,force:true});
}
