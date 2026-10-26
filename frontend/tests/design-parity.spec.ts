import {test, expect, type Page} from '@playwright/test';
import type {AttachmentInfo, ConversationInfo, Evidence, MessageInfo, ResultInfo, RuntimeBundle} from '../src/contracts';

const at = '2026-10-23T02:42:00Z';
const user = {id:'design-user', display_name:'林然', role:'admin', csrf_token:'design-only'};
const conversation: ConversationInfo = {
  id:'design-conversation', conversation_id:'design-conversation', title:'客户转化率为何下降', title_source:'auto', status:'active',
  data_source_id:'customer', data_source_name:'客户行为分析', data_source_status:'enabled', data_source_locked:true,
  current_task_id:null, latest_task_id:'design-task', can_retry:false, cleanup_status:'none', cleanup_error:null,
  cleanup_updated_at:null, last_message_at:at, created_at:at, updated_at:at,
};
const messages: MessageInfo[] = ['为什么第二周访问到下单的转化率下降？','整体转化率从 20% 降到 14%。访问用户保持 1,000 人，下单用户从 200 减少至 140。\n\n按相同期内的严格三步用户漏斗计算。当前对比能解释变化位置，尚不能证明具体原因。','按设备拆解，哪个环节变化最大？','Windows是主要变化来源。\n\nWindows下单用户减少 60 人，macOS保持 80 人不变。Windows访问到加购的转化率从 50% 降至 30%。'].map((content,i)=>({
  id:`message-${i}`, message_id:`message-${i}`, task_id:i<2 ? 'design-first-task' : 'design-task', role:i%2 ? 'assistant' : 'user',
  message_type:i%2 ? 'assistant_text' : 'user_input', content, seq_no:i+1, message_status:'complete', tool_name:null,
  tool_status:null, tool_call_id:null, tool_input:null, tool_outputs:[], attachments:[], created_at:i<2 ? '2026-10-23T02:31:00Z' : at, updated_at:at,
}));
const exports = {markdown:{status:'not_generated' as const,error_message:null,generated_at:null},html:{status:'not_generated' as const,error_message:null,generated_at:null}};
const result: ResultInfo = {
  result_id:'design-result', task_id:'design-task', conversation_id:conversation.id, task_kind:'analysis', final_report_scope:null,
  problem_definition:'比较 9 月 1–7 日与 9 月 8–14 日的访问下单转化，按当期首次访问设备拆解。',
  key_metrics:[{metric_name:'访问下单转化率',metric_value:14,metric_unit:'%',metric_period:'基期 20% · 下降 6 个百分点'},
    {metric_name:'下单用户变化',metric_value:-60,metric_unit:'人',metric_period:'Windows贡献本次净下降 100%'}],
  evidence_list:[{evidence_id:'E02',source_type:'database',source_name:'客户行为分析',evidence_text:'Windows下单用户 120 → 60 人',related_metric:'设备漏斗',confidence:'high',
    source_locator:{data_source_id:'customer',database:'business_demo',schema:'customer_behavior',query_text:'SELECT device, count(*) FROM customer_behavior.events GROUP BY device',filter_summary:'9 月 1–14 日',extracted_at:at,available:true,available_at_result:true}}],
  conclusion_text:'事实：总体少 60 人，全部来自Windows。\n推断：变化集中在访问 → 加购环节。\n假设：需进一步验证页面体验或流量质量。',
  missing_data_text:'Windows错误时点、版本变更及失败链路。',next_action_text:'1. 核对版本与错误发生时点。\n2. 补充链路数据，开展对照验证。',
  result_markdown:'# 分析结果',export_status:exports,created_at:at,
};
const config: RuntimeBundle = {
  auth:{issuer_url:'https://auth.example.test',issuer_internal_url:'https://auth.internal.test',client_id:'business-attribution',callback_url:'https://app.example.test/auth/callback',credential_ref:'auth:masked',role_claim:'roles',role_mapping:{analyst:'analyst',admin:'admin'}},
  model:{api_url:'https://model.example.test/v1',model_id:'example-model',credential_ref:'model:masked',timeout_seconds:120},
  tools:{sql_enabled:true,command_enabled:true,allowed_commands:['python_stats','text_search'],sql_timeout_seconds:30,command_timeout_seconds:60,task_timeout_seconds:600,cleanup_step_timeout_seconds:60},
  context:{context_window_tokens:32768,response_reserved_tokens:4096,summary_trigger_ratio:0.8,summary_max_tokens:2048,recent_message_count:10},
  parse_limits:{parse_timeout_seconds:60,parse_max_memory_bytes:268435456,xlsx_max_uncompressed_bytes:200000000,parse_max_rows:100000,text_max_chars:2000000},
  data_sources:{customer:{name:'客户行为分析',credential_ref:'source:masked',status:'enabled'}},
};
const sourceRows = [
  ['catalog','商品目录优化','catalog_optimization'],['customer','客户行为分析','customer_behavior'],['inventory','库存异常分析','inventory_anomaly'],
  ['reviews','评论反馈分析','review_feedback'],['market','市场表现分析','market_performance'],['refund','退款模式分析','refund_patterns'],
];
config.data_sources = Object.fromEntries(sourceRows.map(([id,name])=>[id,{name,credential_ref:'source:masked',status:'enabled'}]));
const pending = structuredClone(config);pending.model!.timeout_seconds=150;pending.data_sources!.market.status='disabled';
const attachmentRows: AttachmentInfo[] = ['client_error_summary.csv','device_events.xlsx','channel_metrics.xlsx'].map((name,i)=>({
  id:`att-${i}`,attachment_id:`att-${i}`,conversation_id:conversation.id,message_id:null,file_name:name,file_path:'',file_type:i ? 'xlsx' : 'csv',file_size:i ? 2400000 : 2100,
  created_at:'2026-10-23T02:45:00Z',updated_at:at,parse_status:i===0 ? 'success' : i===1 ? 'parsing' : 'failed',parse_error:i===2 ? 'Sheet1!D7 包含公式。请转为静态值后重传。' : null,
  parse_options:{has_header:true},parse_summary:i===0 ? [{columns:['device','period','error_count'],row_count:4,skipped_empty_rows:0}] : null,
  delete_status:'none',delete_error:null,can_retry:false,download_url:i===0 ? '/fixture/download' : null,
}));

async function setup(page: Page, mode:'none'|'new'|'success'|'running'|'failed'|'archived'|'deleted'|'final'|'queued'|'cancelled' = 'success', admin=false) {
  await page.clock.install({time:new Date('2026-10-23T11:10:00+08:00')});
  const current = {...conversation, ...(mode==='new' ? {id:'design-new',title:'新会话',data_source_id:null,data_source_name:null,data_source_locked:false,latest_task_id:null} : {}),
    ...(mode==='archived' ? {status:'archived' as const} : {}), ...(mode==='deleted' ? {status:'deleted' as const,cleanup_status:'failed' as const,cleanup_error:'工作目录清理超时。',can_retry:true} : {}), ...(['running','queued'].includes(mode) ? {current_task_id:'design-task'} : {})};
  const modeMessages=mode==='final' ? [
    ...messages.map(message=>message.task_id==='design-task' ? {...message,task_id:'design-second-task'} : message),
    {...messages[2],id:'final-question',message_id:'final-question',seq_no:5,content:'汇总本会话并生成最终报告'},
    {...messages[3],id:'final-answer',message_id:'final-answer',seq_no:6,content:'整体转化下降集中在Windows，访问到加购环节变化最大。补充错误计数提供了体验异常线索，当前尚不能确认为因果。'},
  ] : messages.map((message,index)=>index===3 && (mode==='failed' || mode==='cancelled') ? {...message,message_status:mode} : message);
  await page.addInitScript(({userId,id})=>sessionStorage.setItem(`ba_conversation_${userId}`,id),{userId:user.id,id:mode==='none' ? '' : current.id});
  await page.route('**/auth/me',route=>route.fulfill({json:{...user,role:admin ? 'admin' : 'analyst'}}));
  await page.routeWebSocket('**/api/chat/ws/chat?*',ws=>ws.send(JSON.stringify({type:'subscription_ready',conversation_id:current.id,created_at:at})));
  await page.route('**/api/**',async route=>{
    const url = new URL(route.request().url());
    let json: unknown;
    if(url.pathname==='/api/chat/ls') json={items:mode==='none' ? [] : mode==='deleted' ? [current] : [conversation,{...conversation,id:'market-conversation',title:'本期收入与投放效率',data_source_name:'市场表现分析',created_at:'2026-10-22T02:42:00Z',last_message_at:'2026-10-22T02:42:00Z'},{...conversation,id:'inventory-conversation',title:'库存变化复核',data_source_name:'库存异常分析',created_at:'2026-10-20T02:42:00Z',last_message_at:'2026-10-20T02:42:00Z'}],next_cursor:'design-more'};
    else if(url.pathname==='/api/chat/ls/'+current.id) json={conversation:current,items:mode==='new' ? [] : modeMessages,next_cursor:null,snapshot_max_seq_no:4};
    else if(url.pathname==='/api/chat/ws-token') json={websocket_token:'design-only'};
    else if(url.pathname==='/api/chat/update') {Object.assign(current,route.request().postDataJSON());current.data_source_name=sourceRows.find(([id])=>id===current.data_source_id)?.[1] || null;json=current;}
    else if(url.pathname.startsWith('/api/tasks/')) {const id=url.pathname.split('/').at(-1);json={task_id:id,task_status:id==='design-first-task' ? 'success' : ['running','queued','failed','cancelled'].includes(mode) ? mode : 'success',task_kind:mode==='final' && id==='design-task' ? 'final_report' : 'analysis',messages:mode==='new' ? [] : modeMessages.filter(message=>message.task_id===id),export_status:exports,last_event_id:0,current_step:'正在核对补充资料',started_at:at,error_message:mode==='failed' ? '模型调用超时，已保存过程可查看。' : null};}
    else if(url.pathname.startsWith('/api/results/')) json={...result,task_id:url.pathname.split('/').at(-1),...(mode==='final' ? {task_kind:'final_report',final_report_scope:{up_to_seq_no:4,source_task_ids:['design-first-task','design-second-task'],incomplete_task_ids:[]},problem_definition:'汇总整体转化、设备拆解及补充错误线索，保留三轮口径、证据和未验证事项。',evidence_list:[...result.evidence_list,{evidence_id:'E03',source_type:'file',source_name:'client_error_summary.csv',evidence_text:'Windows错误计数 10 → 70',related_metric:'第 3 轮',confidence:'medium',source_locator:{attachment_id:'att-0',file_name:'client_error_summary.csv',position:'逻辑记录 2–5，物理行 2–5',available_at_result:true,available:false,unavailable_reason:'deleted'}}],export_status:{markdown:{status:'success',error_message:null,generated_at:at},html:{status:'failed',error_message:'写入未完成',generated_at:null}}} : {})};
    else if(url.pathname==='/api/admin/configs') json={active_version:12,pending_version:13,edit_version:13,effective_config:config,pending_config:pending};
    else if(url.pathname==='/api/admin/logs') json={items:['conversation_cleanup','attachment_cleanup','data_source_tested','config_saved','export_status','task_finished'].map((log_type,i)=>({id:`log-${i}`,created_at:at,log_level:i===0 ? 'ERROR' : 'INFO',log_type,task_id:i>3 ? 'design-task' : null,operator_id:'林然',target_type:i>3 ? '任务' : '配置',target_id:'runtime_bundle',details:{message:'已记录脱敏运行状态'}})),next_cursor:'design-next'};
    else if(url.pathname==='/api/data-sources') json={items:url.searchParams.get('view')==='admin'
      ? sourceRows.map(([id,,schema])=>({id,connection_target:{host:'postgres',port:5432},database_name:'business_demo',schema_name:schema,effective:config.data_sources![id],pending:id==='market' ? pending.data_sources!.market : null,effective_status:'enabled'}))
      : sourceRows.map(([id,name,schema])=>({id,name,database:'business_demo',schema}))};
    else if(url.pathname==='/api/attachment/ls') json={items:attachmentRows,next_cursor:null,total_bytes:2402100};
    else throw new Error('Unexpected design fixture request: '+url.pathname);
    await route.fulfill({json});
  });
}

async function screenshot(page: Page, name: string) {
  await page.evaluate(()=>document.fonts.ready);
  await expect(page.locator('html')).toHaveCSS('color-scheme','light');
  expect(await page.evaluate(()=>getComputedStyle(document.documentElement).getPropertyValue('--canvas').trim())).toBe('#f2f5f9');
  await page.screenshot({path:test.info().outputPath(name+'.png'),fullPage:true});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBe(1600);
}

test.use({viewport:{width:1600,height:1000},colorScheme:'dark'});

for (const [width,height] of [[1280,800],[1600,1000],[1920,1080],[2559,1347]]) {
  test(`长结果保持一屏，消息与结果独立滚动 ${width}×${height}`,async ({page})=>{
    await page.setViewportSize({width,height});
    await setup(page);
    const longMessages=messages.map(message=>message.role==='assistant' ? {...message,content:message.content.repeat(35)} : message);
    const longResult={...result,conclusion_text:result.conclusion_text.repeat(40),missing_data_text:result.missing_data_text.repeat(40),next_action_text:result.next_action_text.repeat(40)};
    await page.route('**/api/**',async route=>{
      const pathname=new URL(route.request().url()).pathname;
      if(pathname.startsWith('/api/results/')) await route.fulfill({json:{...longResult,task_id:pathname.split('/').at(-1)}});
      else if(pathname==='/api/chat/ls/'+conversation.id) await route.fulfill({json:{conversation,items:longMessages,next_cursor:null,snapshot_max_seq_no:4}});
      else if(pathname.startsWith('/api/tasks/')) {
        const id=pathname.split('/').at(-1);
        await route.fulfill({json:{task_id:id,task_status:'success',task_kind:'analysis',messages:longMessages.filter(message=>message.task_id===id),export_status:exports,last_event_id:0,current_step:'已完成',started_at:at,error_message:null}});
      } else await route.fallback();
    });
    await page.goto('/workbench');
    await expect(page.locator('.result-panel .sec-title')).toHaveCount(6);
    const history=page.getByLabel('消息历史',{exact:true}), panel=page.getByLabel('分析结果',{exact:true});
    await expect(page.locator('.result-note .sr-only')).toContainText('来源状态截至结果生成时');
    expect(await page.evaluate(()=>document.documentElement.scrollHeight)).toBeLessThanOrEqual(height);
    for(const area of [history,panel]) {
      expect(await area.evaluate(element=>element.scrollHeight>element.clientHeight)).toBe(true);
      await area.evaluate(element=>{element.scrollTop=0;});
    }
    await history.evaluate(element=>{element.scrollTop=300;});
    expect(await history.evaluate(element=>element.scrollTop)).toBeGreaterThan(0);
    expect(await panel.evaluate(element=>element.scrollTop)).toBe(0);
    const historyTop=await history.evaluate(element=>element.scrollTop);
    await panel.evaluate(element=>{element.scrollTop=element.scrollHeight;});
    expect(await panel.evaluate(element=>element.scrollTop)).toBeGreaterThan(0);
    expect(await history.evaluate(element=>element.scrollTop)).toBe(historyTop);
    await page.evaluate(()=>window.scrollTo(0,10000));
    expect(await page.evaluate(()=>window.scrollY)).toBe(0);
    expect((await page.locator('.toolbar').boundingBox())!.y).toBe(0);
    expect((await page.locator('.composer').boundingBox())!.y+(await page.locator('.composer').boundingBox())!.height).toBeLessThanOrEqual(height);
    await page.screenshot({path:test.info().outputPath('scroll-boundary.png'),fullPage:true});
  });
}

for(const mode of ['none','new','success','running','queued','cancelled','failed','archived','final'] as const) {
  test(`桌面三栏与状态：${mode}`,async ({page})=>{
    await setup(page,mode); await page.goto('/workbench');
    await expect(page.locator('.wb-side')).toBeVisible();
    if(mode!=='none') await expect(page.locator('.wb-main')).toHaveAttribute('aria-busy','false');

    const toolbar=await page.locator('.toolbar').boundingBox();
    const side=await page.locator('.wb-side').boundingBox();
    const panel=await page.getByLabel('分析结果',{exact:true}).boundingBox();
    expect(toolbar!.height).toBe(64);expect(side!.y).toBe(64);expect(side!.width).toBe(268);
    expect(panel!.x).toBe(1140);expect(panel!.y).toBe(64);expect(panel!.width).toBe(460);
    if(mode==='none') expect((await page.locator('.conv-scroll.empty>.primary').boundingBox())!.width).toBeGreaterThan(96);
    if(mode==='new') {
      expect((await page.locator('.composer').boundingBox())!.height).toBe(188);
      expect((await page.locator('.source-options').boundingBox())!.height).toBe(252);
      const emptyIcon=await page.locator('.result-empty>svg').boundingBox();
      expect(Math.abs(emptyIcon!.x+emptyIcon!.width/2-1370)).toBeLessThan(1);
    }
    if(mode==='success') {
      await expect(page.locator('.result-panel .sec-title')).toHaveCount(6);
      await expect(page.getByRole('button',{name:'导出 Markdown',exact:true})).toBeVisible();
      await expect(page.locator('.export-block')).toHaveCount(0);
      await page.locator('.ev-row').click();await expect(page.getByLabel('证据详情')).toBeVisible();
      expect((await page.getByLabel('证据详情').boundingBox())!.width).toBe(540);
      await screenshot(page,'evidence');
      await page.getByText('查看只读 SQL',{exact:true}).click();
      await expect(page.locator('.evidence-query pre')).toHaveText(result.evidence_list[0].source_locator.query_text as string);
      await page.getByLabel('证据详情').getByRole('button',{name:'关闭',exact:true}).click();
    }
    if(mode==='final') {
      await expect(page.locator('.export-row')).toHaveCount(2);
      await expect(page.locator('.report-summary')).toHaveText('整体转化下降集中在Windows，访问到加购环节变化最大。补充错误计数提供了体验异常线索，当前尚不能确认为因果。');
      await expect(page.getByLabel('固定汇总范围')).toContainText('2 轮成功分析');
      const summary=await page.locator('.report-summary').boundingBox();
      expect((await page.locator('.export-block').boundingBox())!.y).toBeGreaterThan(summary!.y+summary!.height);
      expect((await page.locator('.result-actions').boundingBox())!.y).toBe(911);
    }
    if(mode==='running' || mode==='queued') await expect(page.locator('.result-head')).toContainText('分析结果');
    await screenshot(page,mode);
  });
}

test('删除进度使用完整桌面内容宽度',async ({page})=>{
  await setup(page,'deleted');await page.goto('/workbench');
  await expect(page.getByRole('heading',{name:'删除进度',exact:true})).toBeVisible();
  await expect(page.getByLabel('分析结果',{exact:true})).toBeHidden();
  expect((await page.locator('.wb-main').boundingBox())!.width).toBe(1332);
  await screenshot(page,'cleanup');
});

test('数据源列表支持键盘选择，选源后保持未锁定',async ({page})=>{
  await setup(page,'new');await page.goto('/workbench');
  const source=page.getByLabel('数据源',{exact:true});await expect(page.getByRole('option')).toHaveCount(6);
  await source.focus();await page.keyboard.press('ArrowUp');await expect(page.getByRole('option',{name:'退款模式分析'})).toBeFocused();
  await page.keyboard.press('Enter');await expect(source).toContainText('退款模式分析');
  await expect(page.locator('.conv-sub')).toHaveText('退款模式分析 · 首次问题接受后锁定');
  await expect(page.locator('.conv-sub .lucide-lock-keyhole')).toHaveCount(0);
  await expect(page.getByRole('listbox')).toBeHidden();
  await source.click();await page.getByRole('option',{name:'客户行为分析'}).click();await expect(source).toContainText('客户行为分析');
  await screenshot(page,'source-selected-unlocked');
});

test('最终报告准备清空附件并显示固定范围，与报告文件分格式状态',async ({page})=>{
  await setup(page);await page.goto('/workbench');await expect(page.locator('.msg-user').first()).toBeVisible();
  await page.getByRole('button',{name:'附件',exact:true}).click();await page.getByLabel('本轮使用 client_error_summary.csv').check();
  await page.getByLabel('会话附件',{exact:true}).getByRole('button',{name:'关闭',exact:true}).click();
  await page.getByRole('button',{name:'生成最终报告',exact:true}).click();
  await expect(page.getByLabel('报告范围',{exact:true})).toBeVisible();
  await expect(page.locator('.composer-foot')).toContainText('不使用新附件');
  await expect(page.getByRole('button',{name:'附件',exact:true})).toBeDisabled();
  await expect(page.locator('.analysis-scope .row')).toHaveCount(2);
  await screenshot(page,'report-prepare');
  await page.getByRole('button',{name:'返回普通分析',exact:true}).click();
  await expect(page.locator('.composer-foot')).toContainText('附件 0 个');
});

test('附件三种状态、删除确认与会话菜单键盘',async ({page})=>{
  await setup(page);await page.goto('/workbench');await expect(page.locator('.msg-user').first()).toBeVisible();
  await page.getByLabel('问题输入',{exact:true}).fill('结合新增资料，更新判断。');
  await page.getByRole('button',{name:'附件',exact:true}).click();
  await expect(page.locator('.att-item')).toHaveCount(3);await expect(page.locator('.att-item input[type=checkbox]')).toHaveCount(1);
  await screenshot(page,'attachments-with-draft');
  await page.locator('.att-item').first().getByRole('button',{name:'删除',exact:true}).click();
  const dialog=page.getByRole('dialog');await expect(dialog.getByRole('button',{name:'保留附件'})).toBeFocused();
  await screenshot(page,'attachment-delete');await page.keyboard.press('Escape');
  await page.getByLabel('会话附件',{exact:true}).getByRole('button',{name:'关闭',exact:true}).click();
  const more=page.getByRole('button',{name:'更多',exact:true});await more.focus();await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('menuitem',{name:'重命名…'})).toBeFocused();await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('menuitem',{name:'归档会话',exact:true})).toBeFocused();
  await screenshot(page,'conversation-menu');await page.keyboard.press('Escape');await expect(more).toBeFocused();
  await more.click();await page.getByRole('menuitem',{name:'重命名…'}).click();
  await expect(page.getByLabel('会话标题')).toBeFocused();await screenshot(page,'rename');
});

test('管理差异比较有效版本，校验与冲突保留编辑',async ({page})=>{
  await setup(page,'success',true);await page.goto('/admin');await expect(page.locator('.diff-row')).toHaveCount(2);
  await expect(page.locator('.diff-row').first()).toContainText('120 秒');await expect(page.locator('.diff-row').first()).toContainText('150 秒');
  await page.getByRole('tab',{name:'工具与资源',exact:true}).click();const field=page.getByLabel('SQL 查询时限',{exact:true});
  await field.fill('29');
  await page.route('**/api/admin/configs',async route=>{
    if(route.request().method()==='POST') await route.fulfill({status:409,json:{code:'CONFIG_VERSION_CONFLICT',message:'配置版本已更新',request_id:'design-conflict'}});
    else {const latest=structuredClone(pending);latest.model!.timeout_seconds=180;await route.fulfill({json:{active_version:12,pending_version:14,edit_version:14,effective_config:config,pending_config:latest}});}
  });
  await page.getByRole('button',{name:'保存为草稿',exact:true}).click();await expect(page.getByText('配置已被其他管理员更新',{exact:true})).toBeVisible();
  await expect(field).toHaveValue('29');await page.getByRole('button',{name:'读取最新版本',exact:true}).click();
  await expect(page.locator('.conflict-col').last()).toContainText('180 秒');await expect(field).toHaveValue('29');
  await screenshot(page,'admin-conflict');
  await page.getByRole('button',{name:'保留本地编辑',exact:true}).click();
  await page.route('**/api/admin/configs',route=>route.fulfill({status:400,json:{code:'CONFIG_INVALID',message:'上下文窗口不满足预算约束，请修正草稿后重载；有效版本继续运行。',request_id:'design-validation'}}));
  await page.getByRole('button',{name:'保存为草稿',exact:true}).click();await expect(page.getByRole('alert')).toContainText('CONFIG_INVALID');
  await expect(field).toHaveValue('29');await screenshot(page,'admin-validation');
});

test('日志详情、数据源测试、移除确认与配置展开',async ({page})=>{
  await setup(page,'success',true);await page.goto('/admin');await expect(page.locator('.diff-row')).toHaveCount(2);
  await page.getByText('查看各分组已生效值',{exact:true}).click();await screenshot(page,'effective-config');
  await page.getByRole('tab',{name:'运行日志',exact:true}).click();await page.getByRole('button',{name:'查看详情'}).first().click();
  await expect(page.getByRole('dialog')).toContainText('日志详情');await screenshot(page,'log-detail');await page.keyboard.press('Escape');
  await page.getByRole('tab',{name:'数据源',exact:true}).click();
  await page.route('**/api/admin/data-sources/*/test',route=>route.fulfill({json:{status:'success',message:'连通与权限检查通过',tested_version:13,checks:{connectivity:true,target_scope:true,read_only:true,cross_schema_denied:true,application_db_denied:true}}}));
  await page.locator('.table-row').first().getByRole('button',{name:'测试连接',exact:true}).click();await expect(page.getByRole('dialog')).toContainText('拒绝访问其他业务 schema');
  await screenshot(page,'source-test');await page.keyboard.press('Escape');
  await page.locator('.table-row').first().getByRole('button',{name:'移除',exact:true}).click();await screenshot(page,'source-remove');await page.keyboard.press('Escape');
  await page.locator('.table-row').filter({hasText:'客户行为分析'}).getByRole('button',{name:'编辑',exact:true}).click();
  await page.getByRole('button',{name:'重新测试',exact:true}).click();await expect(page.locator('.source-editor')).toContainText('拒绝访问其他业务 schema');
  await screenshot(page,'source-edit-tested');
});

test('登录入口和回调三态复用原型卡片',async ({page})=>{
  await page.route('**/auth/me',route=>route.fulfill({status:401,json:{message:'请重新登录'}}));
  await page.goto('/login');await expect(page.locator('.login-card')).toBeVisible();await screenshot(page,'login');
  expect((await page.locator('.login-card').boundingBox())!.width).toBe(480);
  await page.goto('/auth/complete?error=AUTH_CALLBACK_INVALID');await expect(page.getByRole('heading',{name:'授权已失效'})).toBeVisible();await screenshot(page,'callback-failure');
  await page.route('**/auth/me',async route=>{await new Promise(resolve=>setTimeout(resolve,1500));await route.fulfill({json:user});});
  await page.goto('/auth/complete');await expect(page.getByRole('heading',{name:'正在验证授权'})).toBeVisible();await screenshot(page,'callback-pending');
  await expect(page.getByRole('heading',{name:'授权成功'})).toBeVisible();await screenshot(page,'callback-success');
});

test('附件宽度、删除弹窗、键盘焦点及固定浅色外观',async ({page})=>{
  await setup(page);await page.goto('/workbench');await expect(page.locator('.msg-user').first()).toBeVisible();
  await page.getByRole('button',{name:'附件',exact:true}).click();
  await expect(page.getByLabel('会话附件',{exact:true})).toBeVisible();
  expect((await page.locator('.att-panel').boundingBox())!.width).toBe(500);
  await screenshot(page,'attachments');
  await page.getByLabel('会话附件',{exact:true}).getByRole('button',{name:'关闭',exact:true}).click();
  await page.getByRole('button',{name:'更多',exact:true}).click();await page.getByRole('menuitem',{name:'删除会话…'}).click();
  const dialog=page.getByRole('dialog');await expect(dialog).toBeVisible();
  await expect(dialog.getByRole('button',{name:'保留会话'})).toBeFocused();
  const bounds=await dialog.boundingBox();expect(bounds!.width).toBe(540);expect(bounds!.x).toBe(530);
  expect((await dialog.getByRole('button',{name:'确认删除',exact:true}).boundingBox())!.height).toBe(38);
  await screenshot(page,'delete-confirm');await page.keyboard.press('Escape');await expect(dialog).toBeHidden();
  await page.emulateMedia({colorScheme:'dark'});await screenshot(page,'light-system-dark');
});

test('管理页间距、五页签、输入框与双栏数据源编辑',async ({page})=>{
  await setup(page,'success',true);await page.goto('/admin');await expect(page.locator('.version-cards')).toBeVisible();
  await expect(page.locator('.admin-content')).toHaveAttribute('aria-busy','false');
  const box=await page.locator('.version-cards').boundingBox();expect(box!.x).toBe(292);expect(Math.abs(box!.y-277)).toBeLessThanOrEqual(2);
  expect(Math.abs(box!.height-137)).toBeLessThan(1);
  await expect(page.getByRole('tab')).toHaveCount(5);await screenshot(page,'admin-overview');
  for(const name of ['认证与模型','工具与资源','运行日志','数据源']) {
    await page.getByRole('tab',{name,exact:true}).click();await expect(page.locator('.admin-content')).toHaveAttribute('aria-busy','false');
    if(name==='认证与模型') {
      await expect(page.getByLabel('服务端认证地址',{exact:true})).toBeHidden();
      await screenshot(page,'admin-auth-collapsed');
      await page.getByLabel('高级认证配置',{exact:true}).click();
      await expect(page.getByLabel('服务端认证地址',{exact:true})).toHaveValue('https://auth.internal.test');
      await page.locator('.auth-provider').getByText('替换凭证',{exact:true}).click();
      await expect(page.getByLabel(/^替换认证凭证/)).toBeVisible();
    }
    if(name==='工具与资源') {
      const cards=await page.locator('.admin-grid--three>.card').all();
      expect(Math.abs((await cards[0].boundingBox())!.height-528)).toBeLessThan(1);
      expect(Math.abs((await cards[1].boundingBox())!.height-528)).toBeLessThan(1);
      expect(Math.abs((await cards[2].boundingBox())!.height-528)).toBeLessThan(1);
      await page.getByLabel('SQL 查询',{exact:true}).uncheck();
      await expect(page.getByText('SQL 已停用',{exact:true})).toBeVisible();
      await page.getByLabel('SQL 查询',{exact:true}).check();
      page.once('dialog',dialog=>dialog.accept());
      await page.getByRole('button',{name:'放弃编辑',exact:true}).click();
    }
    if(name==='运行日志') {
      expect((await page.getByLabel('任务 ID',{exact:true}).boundingBox())!.height).toBe(40);
      await screenshot(page,'admin-logs-collapsed');
      await page.locator('.time-range>summary').click();
      expect((await page.getByLabel('开始时间',{exact:true}).boundingBox())!.height).toBe(40);
      await page.locator('.time-range>summary').click();
    }
    await screenshot(page,'admin-'+name);
  }
  await page.locator('.table-row').filter({hasText:'客户行为分析'}).getByRole('button',{name:'编辑',exact:true}).click();
  await expect(page.getByRole('heading',{name:'编辑客户行为分析数据源'})).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect((await page.getByLabel('显示名称').boundingBox())!.height).toBe(40);
  expect((await page.locator('.source-editor').boundingBox())!.width).toBe(1268);
  await screenshot(page,'source-edit');await page.getByRole('button',{name:'返回列表'}).click();
  await page.getByRole('button',{name:'新建数据源'}).click();await expect(page.getByLabel('连接目标',{exact:true})).toBeVisible();
  await expect(page.getByLabel('数据库账号',{exact:true})).toBeVisible();
  await screenshot(page,'source-new');
});

test('市场指标表格保留原文，非法表格与 HTML 按文字展示',async ({page})=>{
  await setup(page);
  const body='总收入由 20,000 元降至 16,000 元，下降 20%。B 渠道投放费增加，而有效订单数下降，效率同步变差。';
  const table='| 指标 | 基期 | 分析期 |\n| --- | --- | --- |\n| B 渠道收入 | 10,000 元 | 6,000 元 |\n| B 渠道 ROAS | 10 | 4 |\n| B 渠道获单成本 | 10 元 | 25 元 |\n| 整体 ROAS | 10 | 6.4 |';
  const history=messages.map((message,index)=>index===3 ? {...message,content:`B 渠道解释了全部净收入下降。\n\n${body}\n\n${table}\n\n<img src=x onerror=alert(1)>\n\n| 不完整 | 表格 |\n| --- | --- |\n| 只有一格 |`} : message);
  await page.route('**/api/chat/ls/design-conversation',route=>route.fulfill({json:{conversation,items:history,next_cursor:null,snapshot_max_seq_no:4}}));
  await page.route('**/api/tasks/design-task',route=>route.fulfill({json:{task_id:'design-task',task_status:'success',task_kind:'analysis',messages:history.filter(message=>message.task_id==='design-task'),export_status:exports,last_event_id:0,current_step:null,started_at:at,error_message:null}}));
  await page.route('**/api/results/design-task',route=>route.fulfill({json:{...result,key_metrics:[{metric_name:'支付成交收入',metric_value:16000,metric_unit:'元',metric_period:'基期 20,000 元 · 下降 20%'},{metric_name:'整体 ROAS',metric_value:6.4,metric_unit:'',metric_period:'基期 10 · 收入 / 投放费'}]}}));
  await page.goto('/workbench');await expect(page.locator('.answer-table')).toBeVisible();
  await expect(page.locator('.answer-table th')).toHaveCount(3);await expect(page.locator('.answer-table tbody tr')).toHaveCount(4);
  expect((await page.locator('.answer-table').boundingBox())!.height).toBeGreaterThan(150);
  await expect(page.locator('.answer-table')).toContainText('6,000 元');
  await expect(page.locator('.metric dd').first()).toHaveText('16,000 元');
  await expect(page.locator('.msg-answer img')).toHaveCount(0);
  await expect(page.locator('.msg-answer').last()).toContainText('<img src=x onerror=alert(1)>');
  await expect(page.locator('.msg-answer').last()).toContainText('| 只有一格 |');
  await screenshot(page,'market-table');
});

test('原型 26 市场整页的当前轮次、标题和表格对齐',async ({page})=>{
  await setup(page);
  const market={...conversation,title:'本期收入与投放效率',data_source_id:'market',data_source_name:'市场表现分析'};
  await page.route('**/api/chat/ls?*',route=>route.fulfill({json:{items:[{...conversation,id:'customer-history'},market,{...conversation,id:'inventory-history',title:'库存变化复核',data_source_name:'库存异常分析',last_message_at:'2026-10-20T02:42:00Z'}],next_cursor:'design-more'}}));
  const content='B 渠道解释了全部净收入下降。\n\n总收入由 20,000 元降至 16,000 元，下降 20%。B 渠道投放费增加，而有效订单数下降，效率同步变差。\n\n| 指标 | 基期 | 分析期 |\n| --- | --- | --- |\n| B 渠道收入 | 10,000 元 | 6,000 元 |\n| B 渠道 ROAS | 10 | 4 |\n| B 渠道获单成本 | 10 元 | 25 元 |\n| 整体 ROAS | 10 | 6.4 |';
  const items=messages.map((message,index)=>({...message,content:index===2 ? '分渠道拆解收入变化，再比较 ROAS 和获单成本。' : index===3 ? content : message.content}));
  await page.route('**/api/chat/ls/design-conversation',route=>route.fulfill({json:{conversation:market,items,next_cursor:null,snapshot_max_seq_no:4}}));
  await page.route('**/api/tasks/design-task',route=>route.fulfill({json:{task_id:'design-task',task_status:'success',task_kind:'analysis',messages:items.slice(2),last_event_id:0,export_status:exports}}));
  await page.route('**/api/results/design-task',route=>route.fulfill({json:{...result,
    problem_definition:'比较 9 月 1–7 日与 9 月 8–14 日的有效支付成交收入及投放效率，按 paid_at 归期。',
    key_metrics:[{metric_name:'支付成交收入',metric_value:16000,metric_unit:'元',metric_period:'基期 20,000 元 · 下降 20%'},{metric_name:'整体 ROAS',metric_value:6.4,metric_unit:'',metric_period:'基期 10 · 收入 / 投放费'}],
    evidence_list:[{...result.evidence_list[0],evidence_text:'B 渠道收入 10,000 → 6,000 元',related_metric:'支付成交口径'}],
    conclusion_text:'事实：B 渠道解释全部净收入下降。\n推断：投放效率同步恶化。\n假设：定向调整与变化的关系仍需验证。',missing_data_text:'受众、曝光、流量质量及对照数据。',next_action_text:'1. 复核 B 渠道流量质量。\n2. 补充对照数据，验证定向调整影响。'}}));
  await page.goto('/workbench');await expect(page.locator('.market-round')).toBeVisible();
  await page.evaluate(()=>document.fonts.ready);
  const question=await page.locator('.market-round .msg-user').boundingBox(), table=await page.locator('.market-round .answer-table').boundingBox();
  expect(question!.y).toBeGreaterThan(150);expect(question!.height).toBeGreaterThan(60);
  expect(table!.x).toBeGreaterThan(268);expect(table!.y).toBeGreaterThan(question!.y);expect(table!.height).toBeGreaterThan(150);
  await expect(page.locator('.market-round .info')).toContainText('ROAS 不等于利润率或 ROI');
  await screenshot(page,'market-full');
  await page.locator('.conv-scroll').evaluate(element=>element.scrollTop=0);await expect(page.locator('.market-history')).toBeInViewport();
});

for(const kind of ['file','tool_output'] as const) test(`证据来源定位及生成时与当前状态：${kind}`,async ({page})=>{
  await setup(page);
  const source: Evidence={evidence_id:'E03',source_type:kind,source_name:kind==='file' ? 'client_error_summary.csv' : '设备错误汇总',evidence_text:'Windows错误计数由 10 增至 70；macOS两期均为 5。',related_metric:'Windows访问到加购转化率',confidence:'medium',source_locator:kind==='file'
    ? {attachment_id:'att_client_errors',file_name:'client_error_summary.csv',position:{record:'2–5',line_start:2,line_end:5},available_at_result:true,available:false,unavailable_reason:'deleted',availability_checked_at:'2026-10-23T03:05:00Z'}
    : {task_id:'design-task',tool_call_id:'call-3',output_id:'out-3',output_kind:'file',relative_path:'summary.json',position:'完整汇总',generated_at:at,available_at_result:true,available:true,availability_checked_at:'2026-10-23T03:05:00Z',input_refs:[{type:'attachment',attachment_id:'att_client_errors',available:false,unavailable_reason:'attachment_deleted'}]} as Evidence['source_locator']};
  await page.route('**/api/results/design-task',route=>route.fulfill({json:{...result,evidence_list:[source],conclusion_text:'Windows错误计数增加与转化下降同时出现。仅有计数不能计算故障率，也不足以证明技术故障导致转化下降。'}}));
  await page.goto('/workbench');await page.locator('.ev-row').click();
  const aside=page.getByLabel('证据详情',{exact:true});await expect(aside).toContainText('生成时 · 原件可用');
  if(kind==='file') {await expect(aside).toContainText('当前 · 原件已删除');await expect(aside).toContainText('物理起始行：2');}
  else {await expect(aside).toContainText('当前 · 原件可用');await expect(aside.locator('.input-sources')).toContainText('输入原件已删除');await expect(aside).toContainText('summary.json');}
  await screenshot(page,'evidence-'+kind);
});

for (const status of ['success','failed','cancelled'] as const) test(`工具过程按轮次默认折叠，保留记录与终态 ${status}`,async ({page})=>{
  await setup(page,status);
  const items: MessageInfo[] = [];
  for (let round=0; round<2; round++) {
    const question={...messages[round*2],seq_no:round*4+1};
    items.push(question,...['inspect_database','query_database'].map((name,index): MessageInfo=>({...question,
      id:`process-${round}-${index}`,message_id:`process-${round}-${index}`,role:'tool',message_type:'tool_call',
      tool_name:name,tool_status:round===1 && index===1 && status!=='success' ? status : 'success',
      seq_no:round*4+index+2,content:`工具记录 ${round+1}-${index+1}`,
    })),{...messages[round*2+1],seq_no:round*4+4,message_status:round===1 && status!=='success' ? status : 'complete'});
  }
  await page.route('**/api/chat/ls/design-conversation',route=>route.fulfill({json:{conversation,items,next_cursor:null,snapshot_max_seq_no:8}}));
  await page.route('**/api/tasks/*',route=>{
    const id=new URL(route.request().url()).pathname.split('/').at(-1);
    return route.fulfill({json:{task_id:id,task_kind:'analysis',task_status:id==='design-task' ? status : 'success',
      messages:items.filter(message=>message.task_id===id),last_event_id:0,export_status:exports,
      error_message:status==='failed' ? '模型调用超时，已保存过程可查看。' : null}});
  });
  await page.goto('/workbench');
  const groups=page.locator('.conv-scroll .tool-process');
  await expect(groups).toHaveCount(2);
  for(let i=0;i<2;i++) {
    await expect(groups.nth(i)).not.toHaveAttribute('open','');
    await expect(groups.nth(i).locator(':scope > summary')).toContainText(`第 ${i+1} 轮 · 2 次工具调用`);
    await expect(groups.nth(i).locator('.msg-tool').first()).toBeHidden();
  }
  await groups.last().locator(':scope > summary').focus();await page.keyboard.press('Enter');
  await expect(groups.last().locator('.msg-tool')).toHaveCount(2);
  await expect(groups.last().locator('.msg-tool').last()).toBeVisible();
  await groups.last().locator('.msg-tool').last().locator('summary').click();
  await expect(groups.last().locator('pre').last()).toHaveText('工具记录 2-2');
  if(status==='failed') {
    await expect(page.getByLabel('分析结果',{exact:true})).toContainText('模型调用超时');
    await expect(page.getByText('失败，保留部分内容',{exact:true}).first()).toBeVisible();
    await page.getByPlaceholder('继续追问，或补充新的分析问题…').fill('补充资料后重新分析');
    await expect(page.getByRole('button',{name:'发送分析',exact:true})).toBeEnabled();
  } else if(status==='success') {
    await page.locator('.ev-row').click();await expect(page.getByLabel('证据详情',{exact:true})).toContainText('Windows下单用户');
    await page.getByLabel('证据详情',{exact:true}).getByRole('button',{name:'关闭',exact:true}).click();
  } else await expect(page.getByText('已取消',{exact:true}).first()).toBeVisible();
  await page.reload();await expect(page.locator('.conv-scroll .tool-process[open]')).toHaveCount(0);
  await screenshot(page,`tool-process-${status}`);
});

test('加载更早消息后合并同轮工具，保留单个折叠入口',async ({page})=>{
  await setup(page);
  const tool=(id:string,seq:number):MessageInfo=>({...messages[0],id,message_id:id,seq_no:seq,role:'tool',message_type:'tool_call',tool_name:'query_database',tool_status:'success',content:id});
  const first=[messages[0],tool('older-tool',2),tool('loaded-tool',3),{...messages[1],seq_no:4}];
  const latest=[{...messages[2],seq_no:5,content:messages[2].content.repeat(80)},{...messages[3],seq_no:6}];
  await page.route('**/api/chat/ls/design-conversation?*',route=>route.fulfill({json:{conversation,items:first.slice(0,2),next_cursor:null,snapshot_max_seq_no:6}}));
  await page.route('**/api/chat/ls/design-conversation',route=>route.fulfill({json:{conversation,items:[...first.slice(2),...latest],next_cursor:'older',snapshot_max_seq_no:6}}));
  await page.route('**/api/tasks/design-task',route=>route.fulfill({json:{task_id:'design-task',task_status:'success',task_kind:'analysis',messages:latest,last_event_id:0,export_status:exports}}));
  await page.goto('/workbench');
  const group=page.locator('.conv-scroll .tool-process');
  await expect(group.locator(':scope > summary')).toContainText('较早轮次 · 1 次工具调用');
  await page.getByLabel('消息历史',{exact:true}).evaluate(element=>{element.scrollTop=element.scrollHeight;});
  await page.getByRole('button',{name:'加载更早消息',exact:true}).click();
  await expect(page.getByRole('button',{name:'加载更早消息',exact:true})).toHaveCount(0);
  await expect(group).toHaveCount(1);await expect(group).not.toHaveAttribute('open','');
  await expect(group.locator(':scope > summary')).toContainText('第 1 轮 · 2 次工具调用');
  await group.locator(':scope > summary').click();await expect(group.locator('.msg-tool')).toHaveCount(2);
});

test('完整运行过程对齐原型，取消受理期间继续占位',async ({page})=>{
  await setup(page,'running');
  const past=messages.map(message=>message.task_id==='design-task' ? {...message,task_id:'previous-task'} : message);
  const question={...messages[2],id:'current-question',message_id:'current-question',seq_no:5,content:'结合新增资料，更新对Windows转化下降的判断。'};
  const steps=['查询客户行为数据','读取已选 CSV','核对设备错误计数'].map((name,index)=>({...messages[3],id:'tool-'+index,message_id:'tool-'+index,role:'tool',message_type:'tool_call',tool_name:name,tool_status:index===2 ? 'running' : 'success',seq_no:6+index,content:index===0 ? '4 张业务表，已沿用相同统计期间' : index===1 ? '4 条记录，首个非空行作为表头' : ''}));
  const partial={...messages[3],id:'partial-answer',message_id:'partial-answer',seq_no:9,message_status:'streaming',content:'Windows错误计数由 10 增至 70，macOS保持为 5。该变化与转化下降同时出现，但缺少请求量和错误发生时点，尚不能判断故障率或确认因果。'};
  let cancelling=false;
  await page.route('**/api/chat/ls/design-conversation',route=>route.fulfill({json:{conversation:{...conversation,current_task_id:'design-task'},items:[...past,question],next_cursor:null,snapshot_max_seq_no:9}}));
  await page.route('**/api/tasks/previous-task',route=>route.fulfill({json:{task_id:'previous-task',task_status:'success',task_kind:'analysis',messages:past.slice(2),last_event_id:0,export_status:exports}}));
  await page.route('**/api/tasks/design-task',route=>route.fulfill({json:{task_id:'design-task',task_status:'running',task_kind:'analysis',messages:[question,...steps,partial],last_event_id:cancelling ? 1 : 0,current_step:'正在核对补充资料',started_at:'2026-10-23T02:46:00Z',is_cancelling:cancelling,cancel_requested_at:cancelling ? at : null,export_status:exports}}));
  await page.route('**/api/tasks/design-task/cancel',route=>{cancelling=true;return route.fulfill({json:{task_id:'design-task',task_status:'running',is_cancelling:true}});});
  await page.goto('/workbench');await expect(page.locator('.live-round .task-step')).toHaveCount(1);
  await expect(page.locator('.live-round .task-step')).toContainText('核对设备错误计数');
  const process=page.locator('.live-round .tool-process');
  await expect(process.locator('.msg-tool').first()).toBeHidden();
  await expect(process.locator(':scope > summary')).toContainText('已结束的工具步骤 · 2 次工具调用');
  await process.locator(':scope > summary').click();await expect(process.locator('.msg-tool').last()).toBeVisible();
  await page.clock.runFor(5100);await expect(process).toHaveAttribute('open','');
  await process.locator(':scope > summary').click();
  const card=await page.locator('.task-card').boundingBox();expect(card!.y).toBeGreaterThan(150);expect(card!.height).toBeGreaterThan(200);
  await screenshot(page,'running-full');
  await page.route('**/api/chat/archive',route=>route.fulfill({status:409,json:{code:'TASK_BUSY',message:'分析任务尚未结束，暂不能归档；请等待任务结束。',request_id:'design-archive-busy'}}));
  await page.getByRole('button',{name:'更多',exact:true}).click();await page.getByRole('menuitem',{name:'归档会话',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('暂不能归档');await screenshot(page,'archive-blocked');
  await page.getByRole('button',{name:'取消任务',exact:true}).click();await page.clock.runFor(150);
  await expect(page.locator('.task-card')).toContainText('正在取消');await expect(page.getByRole('button',{name:'发送分析'})).toBeDisabled();
  await expect(page.getByRole('button',{name:'取消任务',exact:true})).toBeDisabled();await screenshot(page,'cancelling');
  await process.locator(':scope > summary').click();
  const stopped=[question,...steps.map(step=>({...step,tool_status:step.tool_status==='running' ? 'cancelled' : step.tool_status})),{...partial,message_status:'cancelled'}];
  await page.route('**/api/chat/ls/design-conversation',route=>route.fulfill({json:{conversation,items:[...past,...stopped],next_cursor:null,snapshot_max_seq_no:9}}));
  await page.route('**/api/tasks/design-task',route=>route.fulfill({json:{task_id:'design-task',task_status:'cancelled',task_kind:'analysis',messages:stopped,last_event_id:2,export_status:exports}}));
  await page.clock.runFor(5100);await expect(page.locator('.live-round')).toHaveCount(0);
  await expect(page.locator('.conv-scroll .tool-process')).toHaveCount(1);
  await expect(page.locator('.conv-scroll .tool-process[open]')).toHaveCount(0);
  await expect(page.locator('.conv-scroll .tool-process>summary')).toContainText('3 次工具调用');
  await expect(page.getByText('已取消',{exact:true}).first()).toBeVisible();
});

test('市场分析与更早轮次均通过折叠入口查看工具记录',async ({page})=>{
  await setup(page);
  const items=messages.flatMap(message=>message.role==='user' ? [message,{...message,id:`tool-${message.id}`,message_id:`tool-${message.id}`,role:'tool',message_type:'tool_call',tool_name:'query_database',tool_status:'success',content:'市场分析查询记录'}] : [message]).map((message,index)=>({...message,seq_no:index+1}));
  await page.route('**/api/chat/ls/design-conversation',route=>route.fulfill({json:{conversation:{...conversation,data_source_id:'market'},items,next_cursor:null,snapshot_max_seq_no:6}}));
  await page.route('**/api/tasks/*',route=>{
    const id=new URL(route.request().url()).pathname.split('/').at(-1);
    return route.fulfill({json:{task_id:id,task_status:'success',task_kind:'analysis',messages:items.filter(message=>message.task_id===id),last_event_id:0,export_status:exports}});
  });
  await page.goto('/workbench');
  for(const selector of ['.market-history','.market-round']) {
    const group=page.locator(`${selector} .tool-process`);
    await expect(group).toHaveCount(1);await expect(group).not.toHaveAttribute('open','');
    await group.locator(':scope > summary').click();await expect(group.locator('.msg-tool')).toBeVisible();
  }
});

test('高级配置、批准命令、无草稿和时间范围均可完整展开',async ({page})=>{
  await setup(page,'success',true);await page.goto('/admin');
  await page.getByRole('tab',{name:'认证与模型',exact:true}).click();await page.getByLabel('高级认证配置').click();
  await page.locator('.auth-provider').getByText('替换凭证',{exact:true}).click();await page.locator('.role-editor>summary').click();
  await page.locator('.auth-config>.card').last().getByText('替换凭证',{exact:true}).click();await screenshot(page,'admin-auth-full');
  expect((await page.getByLabel('角色映射（JSON）').boundingBox())!.height).toBe(82);
  await page.getByRole('tab',{name:'工具与资源',exact:true}).click();await page.getByLabel('允许的批准命令',{exact:true}).click();
  await expect(page.getByLabel('python_stats',{exact:true})).toBeChecked();
  expect((await page.locator('.admin-grid--three>.card').first().boundingBox())!.height).toBeGreaterThan(400);await screenshot(page,'approved-commands');
  await page.getByRole('tab',{name:'运行日志',exact:true}).click();await page.locator('.time-range>summary').click();await screenshot(page,'log-time-range');
  await page.route('**/api/admin/configs',route=>route.fulfill({json:{active_version:12,pending_version:null,edit_version:12,effective_config:config,pending_config:null}}));
  await page.reload();await expect(page.getByText('无待生效草稿',{exact:true}).first()).toBeVisible();
  await page.getByText('查看各分组已生效值',{exact:true}).click();
  expect((await page.locator('.effective-details').boundingBox())!.height).toBeGreaterThan(300);await screenshot(page,'no-pending-config');
  await page.getByLabel('完整脱敏配置',{exact:true}).click();await expect(page.locator('.effective-details pre').first()).toContainText('auth:masked');await screenshot(page,'effective-config-full');
});

test('连接失败检查不标记成功，切换数据源不沿用旧测试',async ({page})=>{
  await setup(page,'success',true);await page.goto('/admin');await page.getByRole('tab',{name:'数据源',exact:true}).click();
  await page.route('**/api/admin/data-sources/*/test',route=>route.fulfill({json:{status:'failed',message:'数据库连接被拒绝，请核对草稿连接与只读账号。',tested_version:13,checks:{connectivity:false,read_only:false}}}));
  await page.locator('.table-row').first().getByRole('button',{name:'测试连接',exact:true}).click();
  const dialog=page.getByRole('dialog');await expect(dialog).toContainText('连接失败');await expect(dialog).not.toContainText('连接成功');await screenshot(page,'source-test-failed');
  await page.keyboard.press('Escape');await page.locator('.table-row').nth(1).getByRole('button',{name:'编辑',exact:true}).click();
  await expect(page.locator('.source-editor')).toContainText('选择“重新测试”后显示');
});

test('归档历史可查看且仍然只读',async ({page})=>{
  await setup(page,'archived');await page.goto('/workbench');await page.getByRole('button',{name:'查看历史过程',exact:true}).click();
  await expect(page.getByLabel('归档历史过程')).toContainText(messages[0].content);
  await expect(page.locator('.archived-composer input')).toHaveAttribute('readonly','');await screenshot(page,'archived-history');
});

test('附件删除失败保持占额，提供重试且禁止上传和选择',async ({page})=>{
  await setup(page);await page.route('**/api/attachment/ls?*',route=>route.fulfill({json:{items:[{...attachmentRows[0],delete_status:'failed',delete_error:'文件清理未完成',can_retry:true,download_url:null}],next_cursor:null,total_bytes:2100}}));
  await page.goto('/workbench');await page.getByRole('button',{name:'附件',exact:true}).click();
  await expect(page.getByText('删除失败 · 暂不可用',{exact:true})).toBeVisible();await expect(page.locator('#attachment-file')).toBeDisabled();
  await expect(page.getByLabel('本轮使用 client_error_summary.csv')).toBeDisabled();await expect(page.getByRole('button',{name:'重试删除',exact:true})).toBeEnabled();
  await screenshot(page,'attachment-cleanup-failed');await page.getByRole('button',{name:'重试删除',exact:true}).click();await expect(page.getByRole('dialog')).toContainText('删除附件');
});

test('源停用保留绑定和历史，证据不足不制造指标',async ({page})=>{
  await setup(page);await page.route('**/api/chat/ls/design-conversation',route=>route.fulfill({json:{conversation:{...conversation,data_source_status:'disabled'},items:messages,next_cursor:null,snapshot_max_seq_no:4}}));
  await page.route('**/api/results/design-task',route=>route.fulfill({json:{...result,key_metrics:[],evidence_list:[],conclusion_text:'证据不足，当前不能确认原因。'}}));
  await page.goto('/workbench');await expect(page.getByText('绑定数据源已停用',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'发送分析'})).toBeDisabled();await expect(page.getByText('证据不足 · 已保存',{exact:true})).toBeVisible();
  await expect(page.locator('.metric')).toHaveCount(0);await expect(page.locator('.conv-sub')).toContainText('客户行为分析');await screenshot(page,'source-disabled-insufficient');
});

test('断线提示可重连，不重新提交分析',async ({page})=>{
  await setup(page);let socket: {close:()=>void} | undefined, sends=0;
  await page.routeWebSocket('**/api/chat/ws/chat?*',connection=>{socket=connection;connection.send(JSON.stringify({type:'subscription_ready',conversation_id:conversation.id,created_at:at}));});
  page.on('request',request=>{if(new URL(request.url()).pathname==='/api/chat/send') sends++;});
  await page.goto('/workbench');await expect(page.locator('.result-head')).toBeVisible();socket!.close();
  await expect(page.getByRole('button',{name:'重新连接',exact:true})).toBeVisible();await screenshot(page,'connection-recovery');
  await page.getByRole('button',{name:'重新连接',exact:true}).click();await expect(page.locator('.result-head')).toBeVisible();expect(sends).toBe(0);
});

test('登录失效保留问题草稿，提供真实认证入口且不自动重发',async ({page})=>{
  await setup(page);let sends=0;await page.route('**/api/chat/send',route=>{sends++;return route.fulfill({status:401,json:{code:'SESSION_EXPIRED',message:'登录已失效，请重新登录。',request_id:'design-expired'}});});
  await page.goto('/workbench');await expect(page.locator('.result-head')).toBeVisible();await page.getByLabel('问题输入',{exact:true}).fill('保留这条尚未提交的问题');
  await page.getByRole('button',{name:'发送分析',exact:true}).click();await expect(page.getByRole('link',{name:'重新登录'})).toHaveAttribute('href','/auth/login');
  await expect(page.getByLabel('问题输入',{exact:true})).toHaveValue('保留这条尚未提交的问题');await screenshot(page,'session-expired');
  await page.reload();await expect(page.getByLabel('问题输入',{exact:true})).toHaveValue('保留这条尚未提交的问题');expect(sends).toBe(1);
});

test('电脑端 1280 与 1920 宽度保持三栏，源列表不挤压或横向溢出',async ({page})=>{
  await setup(page,'new');await page.goto('/workbench');await expect(page.locator('.source-options')).toBeVisible();
  for(const [width,height] of [[1280,800],[1920,1080]]) {
    await page.setViewportSize({width,height});await page.evaluate(()=>document.fonts.ready);
    await expect(page.getByRole('group',{name:'对话与结果切换',exact:true})).toHaveCount(0);
    await expect(page.getByRole('button',{name:'会话',exact:true})).toHaveCount(0);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBe(width);
    expect((await page.locator('.wb-side').boundingBox())!.width).toBe(268);
    expect((await page.locator('.result-panel').boundingBox())!.width).toBe(460);
    expect((await page.locator('.wb-main').boundingBox())!.width).toBe(width-728);
    const list=await page.locator('.source-options').boundingBox(), last=await page.getByRole('option').last().boundingBox();
    expect(last!.y+last!.height).toBeLessThanOrEqual(list!.y+list!.height);

    await page.screenshot({path:test.info().outputPath('desktop-'+width+'.png'),fullPage:true});
  }
});
