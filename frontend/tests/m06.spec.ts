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

test('双格式导出、下载与复制入口',async ({page})=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await login(page);
  const conversation=await page.evaluate(async(title)=>{
    const response=await fetch('/api/chat/ls?page_size=100',{cache:'no-store'});
    const pageValue=await response.json();
    return pageValue.items.find((row:{title:string;status:string})=>row.title===title&&row.status==='active') as {title:string}|undefined;
  },demoTitle);
  if(!conversation)throw new Error('先运行 scripts/smoke-analysis.py 生成 M06 导出结果');
  await page.reload();
  await page.getByRole('navigation',{name:'会话列表',exact:true}).getByRole('button',{name:new RegExp('^'+demoTitle+' ')}).first().click();
  const result=page.getByLabel('分析结果');
  await expect(result.getByRole('heading',{name:'最终报告',exact:true})).toBeVisible();
  await expect(result.getByRole('button',{name:'复制',exact:true})).toBeVisible();
  for(const [format,label] of [['markdown','Markdown'],['html','HTML']] as const){
    const row=page.locator('.wb-main .export-row').filter({hasText:label});
    if(!await row.getByRole('link',{name:`下载 ${label}`,exact:true}).count())await row.getByRole('button').click();
    const download=result.getByRole('link',{name:`下载 ${label}`,exact:true});
    await expect(download).toBeVisible();
    const response=page.waitForResponse(item=>new URL(item.url()).pathname.endsWith(`/download`)&&new URL(item.url()).searchParams.get('format')===format);
    await download.click();
    expect((await response).status()).toBe(200);
  }

  expect(errors).toEqual([]);
});

test('会话删除确认、墓碑轮询与完成提示',async ({page})=>{
  await login(page);
  const created=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/chat/create');
  await page.getByRole('button',{name:'新建会话',exact:true}).first().click();
  const cid=(await (await created).json()).id as string;
  await page.getByRole('button',{name:'更多',exact:true}).last().click();
  await page.getByRole('menuitem',{name:'删除会话…',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:/^删除“/});
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole('button',{name:'保留会话',exact:true})).toBeFocused();
  const deleted=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/chat/delete');
  await dialog.getByRole('button',{name:'确认删除',exact:true}).click();
  expect((await deleted).status()).toBe(202);
  await expect(page.getByRole('heading',{name:'删除进度',exact:true})).toBeVisible();
  await expect(page.getByText(new RegExp(`会话 #${cid} 的数据和文件已清理。`))).toBeVisible({timeout:30000});
  await expect(dialog).toBeHidden();
});
