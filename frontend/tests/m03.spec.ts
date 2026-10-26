import {test, expect, type Page} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const accounts = JSON.parse(fs.readFileSync(process.env.BA_UI_ACCOUNTS || path.resolve('..','.secrets','demo-accounts.json'),'utf8')) as Array<{username:string;password:string;role:string}>;
const output = process.env.BA_UI_OUTPUT || 'test-results';
fs.mkdirSync(output,{recursive:true});

async function login(page:Page) {
  const user=accounts.find(row=>row.username==='analyst_demo')!;
  await page.goto('/auth/login');
  await page.getByLabel('登录名').fill(user.username);
  await page.getByLabel('密码',{exact:true}).fill(user.password);
  await page.getByRole('button',{name:'登录并授权'}).click();
  await expect(page.getByRole('heading',{name:'授权成功',exact:true})).toBeVisible();
  await page.getByRole('link',{name:'进入工作台',exact:true}).click();
  await expect(page.getByLabel('当前身份：分析用户', {exact:true})).toHaveText(/分析用户/);
}

test.afterEach(async ({page})=>{
  if(!page.isClosed())await page.locator('input[type="password"]').evaluateAll(inputs=>inputs.forEach(input=>(input as HTMLInputElement).value='')).catch(()=>{});
});

test('工作台会话新建选源、改名、刷新、归档恢复',async ({page})=>{
  const errors:string[]=[];
  page.on('pageerror',error=>errors.push(error.message));
  await login(page);
  await page.getByRole('button',{name:'新建会话',exact:true}).first().click();
  const source=page.getByRole('button',{name:'数据源',exact:true});
  await expect(source).toBeEnabled();
  const option=page.getByRole('option').first();
  const chosen=(await option.innerText()).trim();
  await option.click();
  await expect(source).toContainText(chosen);
  await page.getByRole('button',{name:'更多',exact:true}).last().click();
  await page.getByRole('menuitem',{name:'重命名…',exact:true}).click();
  await page.getByLabel('会话标题',{exact:true}).fill('会话阶段验证 😀');
  await page.getByRole('button',{name:'保存名称',exact:true}).click();
  await expect(page.getByRole('heading',{name:'会话阶段验证 😀',exact:true})).toBeVisible();
  await expect(page.getByRole('dialog')).toBeHidden();
  await page.reload();
  await expect(page.getByRole('heading',{name:'会话阶段验证 😀',exact:true})).toBeVisible();
  await expect(source).toContainText(chosen);
  await page.screenshot({path:path.join(output,'m03-workbench.png'),fullPage:true});
  await page.getByRole('button',{name:'更多',exact:true}).last().click();
  await page.getByRole('menuitem',{name:'归档会话',exact:true}).click();
  await expect(page.getByText('会话已归档',{exact:true})).toBeVisible();
  await expect(source).toBeHidden();
  await page.getByRole('button',{name:'恢复会话',exact:true}).click();
  await expect(source).toBeVisible();
  await expect(source).toBeEnabled();
  await expect(source).toContainText(chosen);
  await expect(page.getByRole('button',{name:'发送分析',exact:true})).toBeDisabled();
  expect(errors).toEqual([]);
});

test('工作台会话切换丢弃迟到历史响应',async ({page})=>{
  await login(page);
  const suffix=Date.now().toString(36);
  const ids = await page.evaluate(async suffix=>{
    const user=await (await fetch('/auth/me')).json();
    const result:string[]=[];
    for(const title of ['迟到会话 A '+suffix,'当前会话 B '+suffix]){
      const response=await fetch('/api/chat/create',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':user.csrf_token},body:JSON.stringify({title})});
      if(!response.ok)throw new Error('隔离会话夹具创建失败');
      result.push((await response.json()).conversation_id);
    }
    return result;
  },suffix);
  await page.reload();
  let release!:()=>void;
  const gate=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/api/chat/ls/'+ids[0],async route=>{await gate;await route.continue();});
  const requested=page.waitForRequest(request=>new URL(request.url()).pathname==='/api/chat/ls/'+ids[0]);
  await page.getByRole('navigation',{name:'会话列表',exact:true}).getByRole('button',{name:new RegExp('^迟到会话 A '+suffix)}).click();
  await requested;
  try {
    await page.getByRole('navigation',{name:'会话列表',exact:true}).getByRole('button',{name:new RegExp('^当前会话 B '+suffix)}).click();
    const currentHeading=page.getByRole('heading',{name:new RegExp('^当前会话 B '+suffix+'$')});
    await expect(currentHeading).toBeVisible();
    const late=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/chat/ls/'+ids[0]);
    release();
    await (await late).finished();
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    await expect(currentHeading).toBeVisible();
    await expect(page.getByRole('heading',{name:new RegExp('^迟到会话 A '+suffix+'$')})).toBeHidden();
  } finally {
    release();
    await page.evaluate(async ids=>{
      const user=await (await fetch('/auth/me')).json();
      await fetch('/api/chat/delete',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':user.csrf_token},body:JSON.stringify({conversation_ids:ids})});
    },ids);
  }
});

test('附件上传、解析选择、下载与删除',async ({page})=>{
  test.setTimeout(60000);
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await login(page);
  const created=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/chat/create');
  await page.getByRole('button',{name:'新建会话',exact:true}).first().click();
  const cid=(await (await created).json()).id;
  try {
    let failQuery=false,queries=0;
    await page.route('**/api/attachment/ls?*',async route=>{
      queries++;
      if(failQuery){failQuery=false;await route.fulfill({status:503,json:{code:'TEMPORARY_QUERY_FAILURE',message:'测试查询暂不可用',request_id:'m07-fixture'}});}
      else await route.continue();
    });
    await page.getByRole('button',{name:'附件',exact:true}).click();
    const file=page.getByLabel('选择文件上传',{exact:true});
    await file.setInputFiles({name:'m03.csv',mimeType:'text/csv',buffer:Buffer.from('id,count\n00123,10\n00002,20')});
    failQuery=true;
    await page.getByRole('button',{name:'上传文件',exact:true}).click();
    await expect(page.getByRole('alert')).toContainText('测试查询暂不可用');
    await expect(page.getByText('解析失败 · 暂不可选',{exact:true})).toBeHidden();
    await page.evaluate(()=>{
      Object.defineProperty(document,'visibilityState',{configurable:true,get:()=> 'hidden'});
      document.dispatchEvent(new Event('visibilitychange'));
      Object.defineProperty(document,'visibilityState',{configurable:true,get:()=> 'visible'});
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await expect(page.getByText('解析成功',{exact:true})).toBeVisible({timeout:30000});
    const terminalQueries=queries;
    await page.waitForTimeout(5500); // Verify that terminal attachments stop polling.
    expect(queries).toBe(terminalQueries);
    const pick=page.getByLabel('本轮使用 m03.csv',{exact:true});
    await expect(pick).toBeEnabled();
    await pick.check();
    await expect(page.getByText('本轮已选择 1 个附件',{exact:true})).toBeVisible();
    await expect(page.getByText(/附件 1 个/)).toBeVisible();
    const download=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/attachment/get');
    await page.getByRole('link',{name:'下载',exact:true}).click();
    expect((await download).status()).toBe(200);
    await page.screenshot({path:path.join(output,'m03-attachments.png'),fullPage:true});
    await page.getByRole('button',{name:'删除',exact:true}).click();
    const dialog=page.getByRole('dialog',{name:'删除附件',exact:true});
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('button',{name:'保留附件',exact:true})).toBeFocused();
    await dialog.getByRole('button',{name:'确认删除附件',exact:true}).click();
    await expect(page.getByLabel('本轮使用 m03.csv',{exact:true})).toBeHidden({timeout:30000});
    await expect(page.getByText(/附件 0 个/)).toBeVisible();
    expect(errors).toEqual([]);
  } finally {
    await page.evaluate(async id=>{
      const user=await (await fetch('/auth/me')).json();
      await fetch('/api/chat/delete',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':user.csrf_token},body:JSON.stringify({conversation_ids:[id]})});
    },cid).catch(()=>{});
  }
});

test('任务发送、刷新恢复、单次取消与草稿保留',async ({page})=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await login(page);
  await page.getByRole('button',{name:'新建会话',exact:true}).first().click();
  await page.getByRole('option').first().click();
  const input=page.getByLabel('问题输入',{exact:true});
  await input.fill('队列刷新恢复验证');
  await page.reload();
  await expect(input).toHaveValue('队列刷新恢复验证');
  await page.getByRole('button',{name:'发送分析',exact:true}).click();
  await expect(input).toBeDisabled();
  await expect(page.getByRole('button',{name:'取消任务',exact:true})).toBeVisible();
  await page.reload();
  await page.getByRole('button',{name:'取消任务',exact:true}).click();
  await expect(page.getByRole('button',{name:'取消任务',exact:true})).toBeHidden();
  await expect(input).toBeEnabled();
  await expect(page.getByLabel('消息历史')).toContainText('队列刷新恢复验证');
  await expect(page.getByLabel('消息历史').getByText('队列刷新恢复验证',{exact:true})).toHaveCount(1);
  await page.screenshot({path:path.join(output,'m04-queued-cancelled.png'),fullPage:true});
  expect(errors).toEqual([]);
});
