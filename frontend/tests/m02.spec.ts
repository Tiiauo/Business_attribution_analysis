import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const accounts: Array<{username:string; password:string; role:string}> = JSON.parse(fs.readFileSync(
  process.env.BA_UI_ACCOUNTS || path.resolve('..', '.secrets', 'demo-accounts.json'), 'utf8'));
const output = process.env.BA_UI_OUTPUT || 'test-results';
fs.mkdirSync(output, {recursive:true});

async function login(page: Page, username: string) {
  const account = accounts.find(row => row.username === username)!;
  await page.goto('/auth/login');
  await page.getByLabel('测试身份').selectOption(account.role);
  await page.getByLabel('登录名').fill(account.username);
  await page.getByLabel('密码', {exact:true}).fill(account.password);
  await page.getByRole('button', {name:'登录并授权'}).click();
  await expect(page.getByRole('heading', {name:'授权成功', exact:true})).toBeVisible();
  await page.getByRole('link', {name:'进入工作台'}).click();
}

test.afterEach(async ({page}) => {
  if (!page.isClosed()) await page.locator('input[type="password"]').evaluateAll(
    inputs => inputs.forEach(input => (input as HTMLInputElement).value = '')).catch(() => {});
});

test('管理员完成配置重载、数据源测试和日志查询', async ({page}) => {
  const browserErrors: string[] = [];
  page.on('pageerror', error => browserErrors.push(error.message));
  await login(page, 'admin_demo');
  await page.getByRole('link', {name:'系统管理'}).click();
  await expect(page.getByRole('heading', {name:'配置总览与重载'})).toBeVisible();
  await expect(page.getByRole('tablist', {name:'管理页签'})).toBeVisible();
  const publish = page.getByRole('button', {name:'重载配置并生效'});
  await expect(publish).toBeEnabled();
  await publish.click();
  await expect(page.getByText(/整份配置已生效|当前无待发布变更/)).toBeVisible({timeout:30000});
  await page.screenshot({path:path.join(output, 'm02-overview.png'), fullPage:true});

  await page.getByRole('tab', {name:'数据源', exact:true}).click();
  await expect(page.getByRole('heading', {name:'示例数据源', exact:true})).toBeVisible();
  await expect(page.locator('.table-card')).toContainText('商品目录优化');
  await expect(page.locator('.table-row')).toHaveCount(6);
  await page.screenshot({path:path.join(output, 'm02-sources.png'), fullPage:true});
  await page.getByRole('button', {name:'测试连接'}).first().click();
  const testDialog = page.getByRole('dialog', {name:'连接测试结果'});
  await expect(testDialog).toBeVisible();
  await expect(testDialog).toContainText('通过');
  await expect(testDialog).toContainText('拒绝访问其他业务 schema');
  await testDialog.getByRole('button', {name:'关闭'}).last().click();
  await expect(testDialog).toBeHidden();

  await page.getByRole('tab', {name:'认证与模型', exact:true}).click();
  await expect(page.getByRole('heading', {name:'认证与模型配置'})).toBeVisible();
  await expect(page.getByLabel('base_url')).toBeVisible();
  await page.locator('.auth-config>.card').last().getByText('替换凭证',{exact:true}).click();
  await expect(page.getByLabel('api_key', {exact:true})).toHaveValue(/^(?:••••••••)?$/);
  await page.keyboard.press('Tab');
  expect(await page.evaluate(() => document.activeElement?.closest('.admin-shell') !== null)).toBe(true);

  await page.getByRole('tab', {name:'运行日志', exact:true}).click();
  await expect(page.getByRole('heading', {name:'运行日志'})).toBeVisible();
  await expect(page.locator('.table-row').first()).toBeVisible();
  await page.getByRole('button', {name:'查看详情'}).first().click();
  await expect(page.getByRole('dialog', {name:/日志详情/})).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', {name:/日志详情/})).toBeHidden();
  expect(browserErrors).toEqual([]);
});

test('普通用户不能进入管理页或调用管理员接口', async ({page}) => {
  await login(page, 'analyst_demo');
  await page.goto('/admin');
  await expect(page.getByRole('heading', {name:'需要管理员权限'})).toBeVisible();
  const status = await page.evaluate(async () => (await fetch('/api/admin/configs')).status);
  expect(status).toBe(403);
});

test('模型服务遮掩密钥和测试连接不保存配置',async ({page})=>{
  await login(page,'admin_demo');
  await page.goto('/admin');
  await page.getByRole('tab',{name:'认证与模型',exact:true}).click();
  await page.locator('.auth-config>.card').last().getByText('替换凭证',{exact:true}).click();
  const key=page.getByLabel('api_key',{exact:true});
  await expect(key).toHaveAttribute('type','password');
  await expect(key).toHaveValue('••••••••');
  const before=await page.evaluate(async()=>await (await fetch('/api/admin/configs')).json());
  await page.route('**/api/admin/model/test',async route=>{
    const payload=route.request().postDataJSON();
    expect(payload.api_key).toBeUndefined();
    expect(payload.model.model_id).toBeTruthy();
    await route.fulfill({json:{status:'success',message:'连接测试成功（浏览器交互夹具）',elapsed_ms:12}});
  });
  await page.getByRole('button',{name:'测试连接 · 最小真实请求，不保存配置',exact:true}).click();
  await expect(page.getByText(/连接测试成功/)).toBeVisible();
  const after=await page.evaluate(async()=>await (await fetch('/api/admin/configs')).json());
  expect(after).toEqual(before);
});

test('表单保存、非法值与版本冲突保留编辑', async ({page, context}) => {
  test.setTimeout(120000);
  await login(page, 'admin_demo');
  await page.goto('/admin');
  await page.getByRole('tab', {name:'工具与资源', exact:true}).click();
  const rows = page.getByLabel('非空数据行数（最多 100000）');
  const original = await rows.inputValue();
  await rows.fill('100001');
  await page.getByRole('button', {name:'保存为草稿', exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('CONFIG_INVALID');
  await expect(rows).toHaveValue('100001');
  await rows.fill(original);
  await page.getByRole('button', {name:'保存为草稿', exact:true}).click();
  await expect(page.getByText('已保存草稿，尚未生效。请重载整份配置。')).toBeVisible();

  const second = await context.newPage();
  await second.goto('/admin');
  await second.getByRole('tab', {name:'工具与资源', exact:true}).click();
  const timeout = page.getByLabel('SQL 查询时限', {exact:true});
  const oldTimeout = await timeout.inputValue();
  const changedTimeout = oldTimeout === '30' ? '29' : '30';
  await timeout.fill(changedTimeout);
  await page.getByRole('button', {name:'保存为草稿', exact:true}).click();
  await expect(page.getByRole('button', {name:'保存为草稿', exact:true})).toBeDisabled();
  const secondTimeout = second.getByLabel('SQL 查询时限', {exact:true});
  await secondTimeout.fill(changedTimeout);
  await second.getByRole('button', {name:'保存为草稿', exact:true}).click();
  await expect(second.getByRole('alert')).toContainText('CONFIG_VERSION_CONFLICT');
  await expect(secondTimeout).toHaveValue(changedTimeout);
  await expect(second.getByText('配置已被其他管理员更新', {exact:true})).toBeVisible();
  await second.getByRole('button', {name:'读取最新版本', exact:true}).click();
  await expect(secondTimeout).toHaveValue(changedTimeout);
  await second.screenshot({path:path.join(output, 'm02-conflict.png'), fullPage:true});
  second.on('dialog', dialog => dialog.accept());
  await second.close();
  await timeout.fill(oldTimeout);
  await page.getByRole('button', {name:'保存为草稿', exact:true}).click();
  await expect(page.getByRole('button', {name:'保存为草稿', exact:true})).toBeDisabled();
  await page.getByRole('tab', {name:'配置总览与重载',exact:true}).click();
  await page.getByRole('button', {name:'重载配置并生效', exact:true}).click();
  await expect(page.getByText('整份配置已生效。', {exact:true})).toBeVisible({timeout:30000});
});

