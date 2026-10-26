import {test, expect, type Page} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const accounts = JSON.parse(fs.readFileSync(process.env.BA_UI_ACCOUNTS || path.resolve('..','.secrets','demo-accounts.json'),'utf8')) as Array<{username:string;password:string;role:string}>;
const output = process.env.BA_UI_OUTPUT || 'test-results';
const demoTitle = process.env.BA_UI_DEMO_TITLE || 'M05 真实模型联调';
fs.mkdirSync(output,{recursive:true});

async function login(page:Page) {
  const user=accounts.find(row=>row.username==='analyst_demo')!;
  await page.goto('/auth/login');
  await page.getByLabel('登录名').fill(user.username);
  await page.getByLabel('密码',{exact:true}).fill(user.password);
  await page.getByRole('button',{name:'登录并授权'}).click();
  await expect(page.getByRole('heading',{name:'授权成功',exact:true})).toBeVisible();
  await page.getByRole('link',{name:'进入工作台',exact:true}).click();
  await expect(page.getByLabel('当前身份：分析用户',{exact:true})).toBeVisible();
}

test('真实最终报告六部分、证据与固定范围',async ({page})=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await login(page);
  const conversation=await page.evaluate(async(title)=>{
    const response=await fetch('/api/chat/ls?page_size=100',{cache:'no-store'});
    const pageValue=await response.json();
    return pageValue.items.find((row:{title:string;status:string})=>row.title===title&&row.status==='active') as {title:string}|undefined;
  },demoTitle);
  if(!conversation)throw new Error('先运行 scripts/smoke-analysis.py 生成 M05 真实报告');
  await page.reload();
  await page.getByRole('navigation',{name:'会话列表',exact:true}).getByRole('button',{name:new RegExp('^'+demoTitle+' ')}).first().click();
  const result=page.getByLabel('分析结果');
  await expect(result.getByRole('heading',{name:'最终报告',exact:true})).toBeVisible();
  for(const heading of ['问题定义','关键指标','证据列表','归因结论','待补充数据','下一步建议'])await expect(result.getByRole('heading',{name:new RegExp(heading+'$')})).toBeVisible();
  await expect(page.locator('.wb-main').getByLabel('固定汇总范围',{exact:true})).toBeVisible();
  await expect(result.locator('.ev-row').first()).toBeVisible();
  await result.locator('.ev-row').first().click();
  await expect(page.getByLabel('证据详情')).toBeVisible();
  await page.getByLabel('证据详情').getByRole('button',{name:'关闭',exact:true}).click();
  await page.screenshot({path:path.join(output,'m05-real-final-report.png'),fullPage:true});

  expect(errors).toEqual([]);
});
