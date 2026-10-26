import {test, expect} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const accounts = JSON.parse(fs.readFileSync(process.env.BA_UI_ACCOUNTS || path.resolve('..','.secrets','demo-accounts.json'),'utf8')) as Array<{username:string;password:string}>;

test('M07 丢失实时增量和终态后周期快照收敛',async ({page})=>{
  test.setTimeout(80000);
  let dropped=0;
  await page.routeWebSocket('**/api/chat/ws/chat?*',ws=>{
    const server=ws.connectToServer();
    server.onMessage(message=>{
      const event=JSON.parse(message.toString());
      if(event.type==='subscription_ready')ws.send(message);
      else dropped++;
    });
  });
  const user=accounts.find(row=>row.username==='analyst_demo')!;
  await page.goto('/auth/login');
  await page.getByLabel('登录名').fill(user.username);
  await page.getByLabel('密码',{exact:true}).fill(user.password);
  await page.getByRole('button',{name:'登录并授权'}).click();
  await page.getByRole('link',{name:'进入工作台',exact:true}).click();
  await page.getByRole('button',{name:'新建会话',exact:true}).first().click();
  await page.getByRole('option').first().click();
  await page.getByLabel('问题输入',{exact:true}).fill('M07 lost events fixture');
  const acceptedResponse=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/chat/send');
  await page.getByRole('button',{name:'发送分析',exact:true}).click();
  const accepted=await (await acceptedResponse).json();
  await expect(page.getByLabel('分析结果').getByRole('heading',{name:'分析结果',exact:true})).toBeVisible({timeout:30000});
  const history=page.getByLabel('消息历史');
  await expect(history.getByText('已保存的第一段。已保存的第二段。',{exact:true})).toBeVisible();
  expect(dropped).toBeGreaterThan(0);
  await expect(history.getByText('M07 lost events fixture',{exact:true})).toHaveCount(1);
  await page.reload();
  await expect(history.getByText('已保存的第一段。已保存的第二段。',{exact:true})).toBeVisible();

  const taskPath='/api/tasks/'+accepted.task_id;
  const snapshot=await page.evaluate(async path=>await (await fetch(path)).json(),taskPath);
  const source=await page.evaluate(async cid=>(await (await fetch('/api/chat/ls/'+cid)).json()).conversation.data_source_id,snapshot.conversation_id);
  const cursor=Math.max(12,snapshot.last_event_id);
  let calls=0,inFlight=0,maximum=0;
  let release!:()=>void;
  const gate=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**'+taskPath,async route=>{
    const call=++calls;inFlight++;maximum=Math.max(maximum,inFlight);
    try {
      if(call===2)await gate;
      if(call===3){await route.abort();return;}
      const value=structuredClone(snapshot);value.last_event_id=call===2?cursor-2:cursor;
      if(call===2)for(const message of value.messages)if(message.role==='assistant')message.content='不应显示的旧快照';
      await route.fulfill({json:value});
    }finally{inFlight--;}
  });
  const visible=()=>page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  const high=page.waitForResponse(response=>new URL(response.url()).pathname===taskPath);
  await visible();await (await high).finished();
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  await visible();await expect.poll(()=>calls).toBe(2);
  for(let i=0;i<5;i++)await visible();
  expect(calls).toBe(2);release();
  await expect.poll(()=>calls).toBeGreaterThanOrEqual(3);
  await expect(page.getByText('不应显示的旧快照',{exact:true})).toBeHidden();
  await expect(history.getByText('已保存的第一段。已保存的第二段。',{exact:true})).toBeVisible();
  await visible();await expect.poll(()=>calls).toBeGreaterThanOrEqual(4);
  expect(maximum).toBe(1);
  await page.unroute('**'+taskPath);
  await page.reload();
  await expect(history.getByText('已保存的第一段。已保存的第二段。',{exact:true})).toBeVisible();

  await page.context().setOffline(true);
  const failed=page.waitForEvent('requestfailed',request=>new URL(request.url()).pathname.startsWith('/api/chat/ls/'));
  await visible();await failed;
  await expect(history.getByText('已保存的第一段。已保存的第二段。',{exact:true})).toBeVisible();
  await page.context().setOffline(false);
  const recovered=page.waitForResponse(response=>new URL(response.url()).pathname===taskPath&&response.status()===200);
  await visible();await recovered;
  await expect(history.getByText('M07 lost events fixture',{exact:true})).toHaveCount(1);

  const second=await page.context().newPage();
  await second.goto('/workbench');
  const next=await second.evaluate(async ({cid,source})=>{
    const user=await (await fetch('/auth/me')).json();
    const response=await fetch('/api/chat/send',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':user.csrf_token},body:JSON.stringify({conversation_id:cid,data_source_id:source,client_request_id:'m07_other_tab_'+Date.now(),content:'来自另一标签页的任务'})});
    if(!response.ok)throw new Error('Second-tab task was not accepted');
    return await response.json();
  },{cid:snapshot.conversation_id,source});
  await second.close();
  await expect(history.getByText('来自另一标签页的任务',{exact:true})).toHaveCount(1,{timeout:15000});
  await expect.poll(async()=>await page.evaluate(async id=>(await (await fetch('/api/tasks/'+id)).json()).task_status,next.task_id)).toBe('success');
  await expect(history.getByText('已保存的第一段。已保存的第二段。',{exact:true})).toHaveCount(2,{timeout:15000});
  await page.evaluate(async id=>{
    const user=await (await fetch('/auth/me')).json();
    const response=await fetch('/api/results/'+id+'/export',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':user.csrf_token},body:JSON.stringify({format:'html'})});
    if(!response.ok)throw new Error('HTML export was not accepted');
  },next.task_id);
  await expect(page.getByRole('link',{name:'下载 HTML',exact:true})).toBeVisible({timeout:15000});
});
