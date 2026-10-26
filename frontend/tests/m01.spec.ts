import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const accounts: Array<{username:string; password:string; role:string}> = JSON.parse(fs.readFileSync(
  process.env.BA_UI_ACCOUNTS || path.resolve('..', '.secrets', 'demo-accounts.json'), 'utf8'));

test.afterEach(async ({page}) => {
  // Playwright error snapshots can otherwise include password input values.
  if (!page.isClosed()) await page.locator('input[type="password"]').evaluateAll(inputs => inputs.forEach(input => (input as HTMLInputElement).value = '')).catch(() => {});
});

test('登录、独立授权、回调成功、退出，键盘可完成', async ({page}) => {
  await page.goto('/login');
  await expect(page.getByRole('heading', {name:'从问题出发，以证据归因。'})).toBeVisible();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', {name:'通过认证中心登录'})).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', {name:'授权登录', exact:true})).toBeVisible();
  await expect(page.locator('.toolbar')).toHaveText('经营归因分析系统');
  await expect(page.locator('html')).toHaveCSS('color-scheme','light');
  const account = accounts.find(row => row.username === 'analyst_demo')!;
  await page.getByLabel('登录名').fill(account.username);
  await page.getByLabel('密码', {exact:true}).fill(account.password);
  await page.getByRole('button', {name:'登录并授权'}).click();
  await expect(page.getByRole('heading', {name:'授权成功', exact:true})).toBeVisible();
  await page.getByRole('link', {name:'进入工作台'}).click();
  await expect(page.getByLabel('当前身份：分析用户')).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('当前身份：分析用户')).toBeVisible();
  await page.getByRole('button', {name:'退出'}).click();
  await expect(page.getByRole('link', {name:'通过认证中心登录'})).toBeVisible();
});

test('管理员身份通过真实认证，错误密码有可见反馈', async ({page}) => {
  await page.goto('/auth/login');
  await page.getByLabel('测试身份').selectOption('admin');
  await page.getByLabel('登录名').fill('admin_demo');
  await page.getByLabel('密码', {exact:true}).fill('wrong-password');
  await page.getByRole('button', {name:'登录并授权'}).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByLabel('密码', {exact:true})).toHaveValue('');
  const account = accounts.find(row => row.username === 'admin_demo')!;
  await page.getByLabel('登录名').fill(account.username);
  await page.getByLabel('密码', {exact:true}).fill(account.password);
  await page.getByRole('button', {name:'登录并授权'}).click();
  await expect(page.getByRole('heading', {name:'授权成功', exact:true})).toBeVisible();
  await page.getByRole('link', {name:'进入工作台'}).click();
  await expect(page.getByLabel('当前身份：系统管理员')).toBeVisible();
});

test('回调失效、等待状态与重试入口', async ({page}) => {
  await page.goto('/auth/callback?state=invalid&code=invalid');
  await expect(page.getByRole('heading', {name:'授权已失效'})).toBeVisible();
  await expect(page.getByRole('link', {name:'重新登录'})).toBeVisible();
  await expect(page).toHaveURL(/\/auth\/complete$/);
  await page.route('**/auth/me', async route => { await new Promise(resolve => setTimeout(resolve, 1200)); await route.continue(); });
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.goto('/auth/complete');
  await expect(page.getByRole('heading', {name:'正在验证授权'})).toBeVisible();
  if (!process.env.BA_FUNCTIONAL_ONLY) await expect(page.locator('.animate-spin')).toHaveCSS('animation-name','none');
  await expect(page.getByRole('heading', {name:'授权已失效'})).toBeVisible();
});

