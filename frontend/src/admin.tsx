import {useEffect, useRef, useState, type ReactNode} from 'react';
import {
  ArrowLeft, ArrowRight, ChartNoAxesCombined, ChevronDown, CircleAlert, CircleCheck, Database,
  Info, KeyRound, LoaderCircle, LockKeyhole, LogOut, Plus, RefreshCw, RotateCcw, Save, Search, Settings2,
  ShieldCheck, SlidersHorizontal, Trash2, TriangleAlert,
} from 'lucide-react';
import type {UserInfo, AuthConfig, ModelConfig, ToolsConfig, ContextConfig, ParseLimits, SourceConfig, ConfigState} from './contracts';

type Bundle = {auth: Required<AuthConfig>; model: Required<ModelConfig>; tools: Required<ToolsConfig>; context: Required<ContextConfig>; parse_limits: Required<ParseLimits>; data_sources: Record<string, SourceConfig>};
type Source = {id: string; connection_target: {host: string; port: number}; database_name: string; schema_name: string; effective: SourceConfig | null; pending: SourceConfig | null; effective_status: string};
type Log = {id: string; created_at: string; log_level: string; log_type: string; task_id: string | null; operator_id: string | null; target_type: string; target_id: string; details: Record<string, unknown>};
type TestResult = {status: string; message: string; tested_version: number; checks: Record<string, boolean>};
type TabKey = 'overview' | 'auth' | 'sources' | 'tools' | 'logs';

const tabs: Array<{key: TabKey; name: string; icon: typeof Settings2}> = [
  {key: 'overview', name: '配置总览与重载', icon: Settings2},
  {key: 'auth', name: '认证与模型', icon: ShieldCheck},
  {key: 'sources', name: '数据源', icon: Database},
  {key: 'tools', name: '工具与资源', icon: SlidersHorizontal},
  {key: 'logs', name: '运行日志', icon: Search},
];
const navHints: Record<TabKey, string> = {overview: '版本与发布', auth: '身份与密钥', sources: '连接与范围', tools: '额度与限制', logs: '审计与排障'};
const MODEL_KEY_MASK = '••••••••';
const groupNames: Record<string, string> = {auth: '认证', model: '模型', tools: '工具', context: '上下文', parse_limits: '解析限制', data_sources: '数据源'};
const logTime = (value: string) => new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', timeStyle: 'medium', hour12: false}).format(new Date(value));

export function Modal({title, onClose, children, icon, className = ''}: {title: string; onClose: () => void; children: ReactNode; icon?: ReactNode; className?: string}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    const target = dialog?.querySelector<HTMLElement>('[data-autofocus]')
      || dialog?.querySelector<HTMLElement>('input:not([type="hidden"]),textarea,select')
      || dialog?.querySelector<HTMLElement>('button.primary')
      || dialog?.querySelector<HTMLElement>('button');
    target?.focus();
  }, []);
  return <dialog ref={ref} className={`admin-dialog ${className}`} onCancel={onClose} onClose={onClose} aria-labelledby="dialog-title">
    <div className="dialog-body">
      <div className="dialog-head">
        {icon ?? null}
        <h2 id="dialog-title">{title}</h2>
      </div>
      {children}
    </div>
  </dialog>;
}

function Chip({tone, children}: {tone: 'neutral' | 'accent' | 'good' | 'warn' | 'danger'; children: ReactNode}) {
  return <span className={`chip chip--${tone}`}>{children}</span>;
}

export function Admin({user}: {user: UserInfo}) {
  const [tab, setTab] = useState<TabKey>('overview'), [state, setState] = useState<ConfigState | null>(null), [draft, setDraft] = useState<Bundle | null>(null);
  const [version, setVersion] = useState(0), [dirty, setDirty] = useState(false), [conflict, setConflict] = useState(false);
  const [message, setMessage] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [sources, setSources] = useState<Source[]>([]), [editing, setEditing] = useState<Source | 'new' | null>(null);
  const [sourceForm, setSourceForm] = useState({name: '', host: '', port: 5432, database_name: '', schema_name: '', username: '', password: '', status: 'enabled'});
  const [secrets, setSecrets] = useState({auth: '', model: ''}), [roles, setRoles] = useState('');
  const [test, setTest] = useState<TestResult | null>(null), [remove, setRemove] = useState<Source | null>(null);
  const [logItems, setLogItems] = useState<Log[]>([]), [cursor, setCursor] = useState<string | null>(null), [detail, setDetail] = useState<Log | null>(null);
  const [filters, setFilters] = useState<Record<string, string>>({scope: 'all', level: '', task_id: '', operator_id: '', target_type: '', target_id: '', start_time: '', end_time: ''});

  async function api<T>(path: string, body?: unknown, method = 'POST'): Promise<T> {
    const response = await fetch('/api' + path, {
      method: body === undefined ? 'GET' : method, cache: 'no-store',
      headers: body === undefined ? {} : {'Content-Type': 'application/json', 'X-CSRF-Token': user.csrf_token},
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) {if (data.code === 'CONFIG_VERSION_CONFLICT') setConflict(true); throw new Error(`${data.message}（${data.code}；请求 ${data.request_id}）`);}
    return data;
  }
  async function sourceList() {setSources((await api<{items: Source[]}>('/data-sources?view=admin')).items);}
  function adopt(next: ConfigState) {
    setState(next);
    const value = (next.pending_config || next.effective_config) as Bundle;
    setDraft(structuredClone(value)); setRoles(JSON.stringify(value.auth.role_mapping, null, 2));
    setVersion(next.edit_version); setDirty(false); setConflict(false);
    setSecrets({auth: '', model: value.model.credential_ref ? MODEL_KEY_MASK : ''});
  }
  async function load(preserve = false) {
    const next = await api<ConfigState>('/admin/configs');
    if (preserve) {setState(next); setConflict(true);} else adopt(next);
    await sourceList();
  }
  async function run(action: () => Promise<void>) {
    setBusy(true); setError(''); setMessage('');
    try {await action();} catch (e) {setError((e as Error).message);} finally {setBusy(false);}
  }
  useEffect(() => {void run(() => load());}, []);
  useEffect(() => {
    const prevent = (event: BeforeUnloadEvent) => {if (dirty) event.preventDefault();};
    window.addEventListener('beforeunload', prevent);
    return () => window.removeEventListener('beforeunload', prevent);
  }, [dirty]);
  function change(group: Exclude<keyof Bundle, 'data_sources'>, field: string, value: unknown) {
    setDraft(current => current ? {...current, [group]: {...current[group], [field]: value}} : current);
    setDirty(true);
  }
  async function save() {
    if (!draft) return;
    const changes = structuredClone(draft);
    try {changes.auth.role_mapping = JSON.parse(roles);} catch {throw new Error('角色映射须为 JSON 对象，例如 {"analyst":"analyst","admin":"admin"}。');}
    const credentials = Object.fromEntries(Object.entries(secrets).filter(([, v]) => v !== '' && v !== MODEL_KEY_MASK));
    if (secrets.model === '') changes.model.credential_ref = null;
    adopt(await api<ConfigState>('/admin/configs', {expected_version: version, changes, credentials}));
    setMessage('已保存草稿，尚未生效。请重载整份配置。'); await sourceList();
  }
  async function publish() {
    if (dirty) throw new Error('请先保存或放弃当前表单编辑，再重载配置。');
    const result = await api<{message: string}>('/admin/reload', {expected_version: version});
    await load(); setMessage(result.message);
  }
  async function queryLogs(next?: string) {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {if (value) query.set(key, key.endsWith('_time') ? `${value}:00+08:00` : value);}
    if (next) query.set('cursor', next);
    query.set('limit', '25');
    const result = await api<{items: Log[]; next_cursor: string | null}>('/admin/logs?' + query);
    setLogItems(result.items); setCursor(result.next_cursor);
  }
  function openSource(source: Source | 'new') {
    setEditing(source); setTest(null);
    setSourceForm(source === 'new'
      ? {name: '', host: '', port: 5432, database_name: '', schema_name: '', username: '', password: '', status: 'enabled'}
      : {name: (source.pending || source.effective)!.name, host: source.connection_target.host, port: source.connection_target.port,
         database_name: source.database_name, schema_name: source.schema_name, username: '', password: '',
         status: (source.pending || source.effective)!.status});
  }
  async function saveSource() {
    const payload = {
      expected_version: version, name: sourceForm.name, status: sourceForm.status,
      ...(editing !== 'new' && editing ? {id: editing.id} : {}),
      connection_target: {host: sourceForm.host, port: sourceForm.port}, database_name: sourceForm.database_name, schema_name: sourceForm.schema_name,
      ...(sourceForm.username || sourceForm.password ? {username: sourceForm.username, password: sourceForm.password} : {}),
    };
    await api('/admin/data-sources', payload);
    setEditing(null); await load(); setMessage('数据源草稿已保存，重载后生效。');
  }
  const field = (group: Exclude<keyof Bundle, 'data_sources'>, key: string, label: string, type = 'text', hint?: string) => {
    const value = (draft?.[group] as unknown as Record<string, unknown>)?.[key];
    const unit = key.endsWith('_seconds') ? '秒' : key.endsWith('_bytes') ? '字节' : key.endsWith('_tokens') ? 'tokens' : key === 'recent_message_count' ? '条' : key === 'parse_max_rows' ? '行' : key === 'text_max_chars' ? 'Unicode 码点' : '';
    return <label className="field" key={key}><span className="label">{group === 'parse_limits' ? label.split('（')[0] : label}</span>
      {type === 'checkbox'
        ? <span className="check"><input type="checkbox" checked={Boolean(value)} onChange={e => change(group, key, e.target.checked)}/>{value ? '已启用' : '已停用'}</span>
        : <div className={type === 'number' ? 'numeric-field' : undefined}><input type={type} aria-label={label} value={value === null || value === undefined ? '' : String(value)} step={key === 'summary_trigger_ratio' ? '0.01' : undefined}
            onChange={e => change(group, key, type === 'number' ? (e.target.value === '' ? null : Number(e.target.value)) : e.target.value)}/>
            {type === 'number' && <span aria-hidden="true">{typeof value === 'number' ? value.toLocaleString('en-US') : ''}{value !== null && value !== undefined && unit ? ` ${unit}` : ''}</span>}</div>}
      {hint && <small>{hint}</small>}
    </label>;
  };
  if (user.role !== 'admin') return <main className="callback-main"><section className="callback-card">
    <ShieldCheck className="accent" size={32} aria-hidden="true"/>
    <h1>需要管理员权限</h1>
    <p>当前身份无法访问配置与系统日志。</p>
    <a className="secondary" href="/workbench">返回工作台</a>
  </section></main>;

  const activeVersion = state?.active_version ?? null;
  const pendingVersion = state?.pending_version ?? null;
  const tabTitles: Record<TabKey, {title: string; lead: string}> = {
    overview: {title: '配置总览与重载', lead: '所有页签共享同一完整草稿。保存后需重载，才会对后续请求生效。'},
    auth: {title: '认证与模型配置', lead: '秘密仅可替换，不回显明文；保存为全局草稿，统一重载。'},
    sources: {title: '示例数据源', lead: '每个数据源固定绑定 PostgreSQL 数据库与一个业务 schema。'},
    tools: {title: '工具与资源配置', lead: '所有值保存到完整草稿，重载后生效；资源不能超过静态隔离环境的承载上限。'},
    logs: {title: '运行日志', lead: '按任务或系统操作定位问题。日志不展示业务全文、原文件或完整提示词。'},
  };
  const tabTitle = tabTitles[tab];

  return <div className="admin-shell">
    <header className="toolbar">
      <div className="brand">
        <span className="brand-mark"><ChartNoAxesCombined size={22} aria-hidden="true"/></span>
        <span className="brand-text"><strong>经营归因分析系统</strong><small>Business Attribution Suite</small></span>
      </div>
      <div className="trail">
        <span className="conn"><i aria-hidden="true"/>已连接</span>
        <a className="secondary trail-link" href="/workbench"><ArrowLeft size={16} aria-hidden="true"/>返回工作台</a>
        <span className="identity-wrap" aria-label="当前身份：管理员">
          <span className="avatar" aria-hidden="true">{user.display_name.slice(0, 1)}</span>
          <span className="identity">{user.display_name} · 管理员</span>
        </span>
        <a className="secondary" href="/auth/login"><LogOut size={16} aria-hidden="true"/>退出</a>
      </div>
    </header>
    <div className="admin-body">
      <aside className="admin-nav" aria-label="管理导航">
        <div className="admin-nav-head">
          <span className="eyebrow">Administration</span>
          <h2>管理控制台</h2>
        </div>
        <div className="tabs" role="tablist" aria-label="管理页签">
          {tabs.map(({key, name, icon: Icon}) => <button key={key} role="tab" className="tab" aria-selected={tab === key}
            aria-controls="admin-panel" onClick={() => {setTab(key); setEditing(null); if (key === 'logs' && !logItems.length) void run(() => queryLogs());}}>
            <span className="tab-icon"><Icon size={17} aria-hidden="true"/></span>
            <strong>{name}</strong>
            <small aria-hidden="true">{navHints[key]}</small>
          </button>)}
        </div>
        <div className="admin-nav-foot">
          <div className="nav-version"><span>有效配置</span><strong>v{activeVersion ?? '—'}</strong></div>
          {pendingVersion ? <Chip tone="warn">草稿 v{pendingVersion} · 未生效</Chip> : <Chip tone="neutral">无待生效草稿</Chip>}
          <p className="footnote">配置保存为草稿，统一重载后对后续请求生效。</p>
        </div>
      </aside>
      <main className="admin-content" aria-busy={busy}>
      <div className="admin-head">
        <div className="titles"><span className="eyebrow">System console</span><h1>系统管理</h1><p>维护运行配置，查看脱敏过程；不提供其他用户的业务内容。</p></div>
        <div className="admin-head-meta">
          <Chip tone="good">有效 v{activeVersion ?? '—'}</Chip>
          {pendingVersion ? <Chip tone="warn">草稿 v{pendingVersion} · 未生效</Chip> : <Chip tone="neutral">无待生效草稿</Chip>}
        </div>
      </div>
      <div id="admin-panel" role="tabpanel" style={{display: 'flex', flexDirection: 'column', gap: 20}}>
        <div className="tab-title"><h2>{conflict ? '配置冲突与校验反馈' : editing ? editing === 'new' ? '新建数据源' : `编辑${(editing.pending || editing.effective)?.name || ''}数据源` : tabTitle.title}</h2>
          <p>{conflict ? '错误保留在页面内，不清空本地编辑，也不部分替换有效配置。' : editing ? editing === 'new' ? '使用专用只读账号；保存后测试并重载。' : '同一数据源的连接目标和业务范围不可就地修改；密码留空保留现有凭证。' : tabTitle.lead}</p></div>
        {message && <div className="info" role="status"><Info size={18} aria-hidden="true"/><div><strong>已更新</strong><p>{message}</p></div></div>}
        {error && !conflict && <div className="field-error" role="alert"><strong>操作未完成</strong><p>{error}</p></div>}
        {conflict && <ConflictBlock state={state} draft={draft} version={version} error={error} busy={busy} onLatest={() => void run(async () => {setState(await api<ConfigState>('/admin/configs'));})}
          onAdopt={() => {setVersion(state!.edit_version); setConflict(false); setMessage('已采用最新编辑版本，请核对后保存。');}}/>}
        {!draft ? <p className="footnote">正在读取配置…</p> : <>
          {tab === 'overview' && <OverviewTab draft={draft} state={state} busy={busy} dirty={dirty} onRefresh={() => void run(() => load(dirty))} onPublish={() => void run(publish)}/>}
          {tab === 'auth' && <form onSubmit={e => {e.preventDefault(); void run(save);}} style={{display: conflict ? 'none' : 'flex', flexDirection: 'column', gap: 22}}>
            <AuthTab draft={draft} secrets={secrets} setSecrets={value => {setSecrets(value); setDirty(true);}} roles={roles} setRoles={value => {setRoles(value); setDirty(true);}}
              field={field} busy={busy} conflict={conflict} onTestModel={() => void run(async () => {
                const result = await api<{message: string; elapsed_ms: number}>('/admin/model/test', {expected_version: version, model: draft.model, ...(secrets.model !== MODEL_KEY_MASK ? {api_key: secrets.model} : {})});
                setMessage(`${result.message} 耗时 ${result.elapsed_ms} ms。`);
              })}/>
            <SaveRow busy={busy} dirty={dirty} conflict={conflict} onDiscard={() => {if (confirm('放弃未保存的表单编辑？')) adopt(state!);}} note="地址与名称为原型示例，不包含真实连接或密钥。"/>
          </form>}
          {tab === 'tools' && <form onSubmit={e => {e.preventDefault(); void run(save);}} style={{display: conflict ? 'none' : 'flex', flexDirection: 'column', gap: 22}}>
            <ToolsTab draft={draft} field={field} change={change}/>
            <SaveRow busy={busy} dirty={dirty} conflict={conflict} onDiscard={() => {if (confirm('放弃未保存的表单编辑？')) adopt(state!);}} note="排队时间不计入运行时限；配置值不是完成耗时承诺。"/>
          </form>}
          {tab === 'sources' && !editing && <SourcesTab sources={sources} busy={busy} dirty={dirty} conflict={conflict}
            onNew={() => openSource('new')} onEdit={source => openSource(source)}
            onTest={source => void run(async () => {setTest(await api<TestResult>(`/admin/data-sources/${source.id}/test`, {}));})}
            onRemove={source => setRemove(source)}/>}
          {tab === 'logs' && <LogsTab items={logItems} filters={filters} setFilters={setFilters} busy={busy} cursor={cursor}
            onQuery={next => void run(() => queryLogs(next))} onDetail={setDetail}/>}
        </>}
      </div>
    {editing && <form onSubmit={e => {e.preventDefault(); void run(saveSource);}} style={{display: 'contents'}}>
        <div className="source-editor">
          <section className="card" style={{gap: 17}}>
            <h3 style={{fontSize: 18, fontWeight: 600}}>可变配置 · 保存为草稿</h3>
            <label className="field"><span className="label">显示名称</span><input value={sourceForm.name} required onChange={e => setSourceForm({...sourceForm, name: e.target.value})}/></label>
            <label className="field"><span className="label">草稿状态</span>
              <select value={sourceForm.status} onChange={e => setSourceForm({...sourceForm, status: e.target.value})}>
                <option value="enabled">已启用</option><option value="disabled">已停用</option><option value="removed">已移除</option>
              </select></label>
            <div className="credential-row"><Chip tone={editing !== 'new' && (editing.pending || editing.effective)?.credential_ref ? 'good' : 'neutral'}>{editing !== 'new' && (editing.pending || editing.effective)?.credential_ref ? '已配置' : '待配置'}</Chip>
              <details className="credential-editor" open={editing === 'new' || undefined}><summary className="secondary">{editing === 'new' ? '设置凭证' : '替换凭证'}</summary>
                <div className="stack" style={{gap: 14}}>
                  <label className="field"><span className="label">{editing === 'new' ? '数据库账号' : '数据库账号（留空保留）'}</span><input autoComplete="off" required={editing === 'new'} value={sourceForm.username} onChange={e => setSourceForm({...sourceForm, username: e.target.value})}/></label>
                  <label className="field"><span className="label">{editing === 'new' ? '数据库密码' : '数据库密码（留空保留）'}</span><input type="password" autoComplete="new-password" required={editing === 'new'} value={sourceForm.password} onChange={e => setSourceForm({...sourceForm, password: e.target.value})}/></label>
                </div>
              </details>
            </div>
            {editing !== 'new' && <div className="immutable"><LockKeyhole size={18} aria-hidden="true" style={{color: 'var(--secondary)'}}/>
              <p className="mono">{`连接目标：${sourceForm.host || '—'}:${sourceForm.port}\n数据库：${sourceForm.database_name || '—'}\nschema：${sourceForm.schema_name || '—'}`}</p>
              <p className="footnote">若要更换业务范围，请新建数据源 ID。</p></div>}
            {editing === 'new' && <div className="stack" style={{gap: 14}}>
              <label className="field"><span className="label">连接目标</span><input value={sourceForm.host} required onChange={e => setSourceForm({...sourceForm, host: e.target.value})}/></label>
              <label className="field"><span className="label">端口</span><input type="number" value={sourceForm.port} onChange={e => setSourceForm({...sourceForm, port: Number(e.target.value)})}/></label>
              <label className="field"><span className="label">数据库</span><input value={sourceForm.database_name} required onChange={e => setSourceForm({...sourceForm, database_name: e.target.value})}/></label>
              <label className="field"><span className="label">业务 schema</span><input value={sourceForm.schema_name} required onChange={e => setSourceForm({...sourceForm, schema_name: e.target.value})}/></label>
            </div>}
            <div className="dialog-actions">
              <button type="button" className="secondary" disabled={busy} onClick={() => setEditing(null)}><ArrowLeft size={16} aria-hidden="true"/>返回列表</button>
              <button className="primary" disabled={busy || conflict}><Save size={16} aria-hidden="true"/>保存草稿</button>
            </div>
          </section>
          <section className="card" style={{gap: 20}}>
            <div className="row"><h3 className="grow" style={{fontSize: 18, fontWeight: 600}}>连接测试</h3>
              <button type="button" className="secondary" disabled={busy || dirty || conflict || editing === 'new'}
                onClick={() => void run(async () => setTest(await api<TestResult>(`/admin/data-sources/${(editing as Source).id}/test`, {})))}>
                <RotateCcw size={16} aria-hidden="true"/>重新测试</button></div>
            <Chip tone="accent">测试对象：{pendingVersion ? `草稿 v${pendingVersion}` : `有效 v${activeVersion ?? '—'}`}</Chip>
            {test ? <>
              {Object.entries(test.checks).map(([key, ok]) => <TestCheck key={key} name={key} ok={ok}/>)}
              <div className="divider"/>
              {test.status !== 'success' && <p className="danger-text" role="alert">{test.message}</p>}
              <div className="info"><Info size={18} aria-hidden="true"/><div><strong>测试成功不代表已生效</strong><p>到配置总览重载整份草稿后，启用状态才对新请求生效。</p></div></div>
            </> : <p className="footnote">选择“重新测试”后显示连接、只读与跨 schema 检查结果。不会返回连接密码或含凭证连接串。</p>}
          </section>
        </div>
        {error && <div className="danger-box" role="alert"><CircleAlert size={18} aria-hidden="true"/><div><strong>未保存</strong><p>{error}</p></div></div>}
      </form>}
    </main>
    </div>
    {test && !editing && <Modal title="连接测试结果" onClose={() => setTest(null)}>
      <p>测试版本 v{test.tested_version} · {test.status === 'success' ? '通过' : '未通过'}</p>
      <p>{test.message}</p>
      {Object.entries(test.checks).map(([key, ok]) => <TestCheck key={key} name={key} ok={ok}/>)}
      <div className="info"><Info size={18} aria-hidden="true"/><div><strong>测试成功不代表已生效</strong><p>到配置总览重载整份草稿后，启用状态才对新请求生效。</p></div></div>
      <div className="dialog-actions"><button className="secondary" onClick={() => setTest(null)}>关闭</button></div>
    </Modal>}
    {remove && <Modal title="移除数据源" onClose={() => setRemove(null)} icon={<TriangleAlert size={30} aria-hidden="true" style={{color: 'var(--warn)'}}/>}>
      <p>将“{(remove.pending || remove.effective)!.name}”设为已移除草稿，重载后生效。原会话保留绑定和历史，不能继续用此源发起分析。</p>
      <div className="dialog-actions">
        <button className="secondary" onClick={() => setRemove(null)}>取消</button>
        <button className="btn--danger" disabled={busy} onClick={() => void run(async () => {
          await api(`/admin/data-sources/${remove.id}`, {expected_version: version}, 'DELETE');
          setRemove(null); await load(); setMessage('移除草稿已保存，重载后生效。');
        })}><Trash2 size={16} aria-hidden="true"/>保存移除草稿</button>
      </div>
    </Modal>}
    {detail && <Modal title={`日志详情 · ${detail.log_type}`} onClose={() => setDetail(null)}>
      <p className="footnote">{logTime(detail.created_at)} · {detail.log_level}<br/>任务：{detail.task_id || '—'} · 操作者：{detail.operator_id || '系统'}</p>
      <pre style={{whiteSpace: 'pre-wrap', fontSize: 12, lineHeight: 1.6}}>{JSON.stringify(detail.details, null, 2)}</pre>
      <p className="footnote">日志不展示业务全文、原文件或完整提示词。</p>
      <div className="dialog-actions"><button className="secondary" onClick={() => setDetail(null)}>关闭</button></div>
    </Modal>}
  </div>;
}

function SaveRow({busy, dirty, conflict, onDiscard, note}: {busy: boolean; dirty: boolean; conflict: boolean; onDiscard: () => void; note: string}) {
  return <div className="actions">
    <span className="footnote" style={{marginRight: 'auto'}}>{note}</span>
    {dirty && <button type="button" className="secondary" disabled={busy} onClick={onDiscard}>放弃编辑</button>}
    <button className="primary" disabled={busy || !dirty || conflict}><Save size={16} aria-hidden="true"/>保存为草稿</button>
  </div>;
}

function TestCheck({name,ok}: {name:string;ok:boolean}) {
  const labels: Record<string,[string,string]> = {connectivity:['连接成功','连接失败'],target_scope:['数据库与绑定 schema 存在','数据库或绑定 schema 检查未通过'],read_only:['分析账号只读','只读权限检查未通过'],cross_schema_denied:['拒绝访问其他业务 schema','跨 schema 隔离检查未通过'],application_db_denied:['拒绝访问应用运行库','应用运行库隔离检查未通过']};
  const Icon = ok ? CircleCheck : CircleAlert;
  return <div className="test-check"><Icon size={20} aria-hidden="true" style={{color:ok ? 'var(--good)' : 'var(--danger)'}}/>{labels[name]?.[ok ? 0 : 1] || `${name} · ${ok ? '通过' : '未通过'}`}</div>;
}

function ConflictBlock({state, draft, version, error, busy, onLatest, onAdopt}: {state: ConfigState | null; draft: Bundle | null; version: number; error: string; busy: boolean; onLatest: () => void; onAdopt: () => void}) {
  return <section className="conflict-box">
    <div className="warn-box" role="alert"><TriangleAlert size={18} aria-hidden="true"/><div><strong>配置已被其他管理员更新</strong>
      <p>本次基于 v{version} 编辑，服务端草稿已更新。读取最新版本后核对，本地修改会保留。</p>
      {error && <p className="footnote">{error}</p>}</div></div>
    <div className="conflict-compare">
      <div className="conflict-col"><h3>本地尚未保存</h3><span>基于草稿 v{version}</span><strong>模型完整调用时限：{draft?.model.timeout_seconds ?? '—'} 秒</strong></div>
      <div className="conflict-col"><h3>当前服务器草稿</h3><span>草稿 v{state?.pending_version ?? '—'} · 有效值仍为 v{state?.active_version ?? '—'}</span>
        <strong>模型完整调用时限：{((state?.pending_config || state?.effective_config) as Bundle | undefined)?.model.timeout_seconds ?? '—'} 秒</strong>
        <details><summary aria-label="查看最新已保存配置" title="查看最新已保存配置"><SlidersHorizontal size={16} aria-hidden="true"/></summary><pre style={{whiteSpace: 'pre-wrap', fontSize: 12}}>{JSON.stringify(state?.pending_config || state?.effective_config, null, 2)}</pre></details></div>
    </div>
    <div className="actions">
      <button className="secondary" disabled={busy} onClick={onAdopt}>保留本地编辑</button>
      <button className="primary" disabled={busy} onClick={onLatest}><RotateCcw size={16} aria-hidden="true"/>读取最新版本</button>
    </div>
  </section>;
}

function OverviewTab({draft, state, busy, dirty, onRefresh, onPublish}: {
  draft: Bundle; state: ConfigState | null; busy: boolean; dirty: boolean; onRefresh: () => void; onPublish: () => void;
}) {
  const pending = state?.pending_config ? state.pending_config as unknown as Bundle : null;
  const effective = state?.effective_config as Bundle | undefined;
  const diffs: Array<{name: string; from: string; to: string}> = [];
  if (pending && effective) {
    for (const [group, values] of Object.entries(pending)) {
      const current = effective[group as keyof Bundle] as unknown as Record<string, unknown>;
      for (const [key, value] of Object.entries(values)) {
        if (JSON.stringify(value) === JSON.stringify(current?.[key])) continue;
        if (group === 'data_sources') {
          const before = current?.[key] as SourceConfig | undefined, after = value as SourceConfig;
          const status = (source: SourceConfig | undefined) => !source ? '未发布' : source.status === 'enabled' ? '已启用' : source.status === 'disabled' ? '已停用' : '已移除';
          diffs.push({name: after.name || before?.name || key, from: status(before), to: status(after)});
        } else {
          const format = (item: unknown) => item === null || item === undefined ? '未配置' : typeof item === 'object' ? JSON.stringify(item) : String(item);
          diffs.push({name: group === 'model' && key === 'timeout_seconds' ? '模型完整调用时限' : `${groupNames[group]} · ${key}`, from: format(current?.[key]) + (key.endsWith('_seconds') ? ' 秒' : ''), to: format(value) + (key.endsWith('_seconds') ? ' 秒' : '')});
        }
      }
    }
  }
  return <>
    <div className="version-cards">
      <div className="version-card"><span>当前有效配置</span><strong>v{state?.active_version ?? '—'}</strong><small>当前请求与新任务使用的已发布配置。</small></div>
      <div className="version-card"><span>待生效草稿</span><strong>{state?.pending_version ? `v${state.pending_version}` : '无'}</strong><small>{state?.pending_version ? '已保存，尚未生效；重启不会自动发布。' : '保存草稿后会显示在这里。'}</small></div>
    </div>
    <div className="overview-stats">
      <div className="overview-stat"><span>数据源</span><strong>{Object.keys(draft.data_sources).length}</strong><small>含尚未发布的草稿</small></div>
      <div className="overview-stat"><span>外部模型</span><strong className="stat-text">{draft.model.model_id || '未配置'}</strong><small>{draft.model.api_url ? '地址已配置' : '待配置地址'}</small></div>
      <div className="overview-stat"><span>SQL 工具</span><strong className="stat-text">{draft.tools.sql_enabled ? '已启用' : '已停用'}</strong><small>{draft.tools.allowed_commands.length} 个批准命令</small></div>
      <div className="overview-stat"><span>任务时限</span><strong>{draft.tools.task_timeout_seconds}<em>秒</em></strong><small>排队时间不计入</small></div>
    </div>
    <div className="card overview-diffs">
      <div className="row"><h2 className="grow">待发布变更</h2><Chip tone={diffs.length ? 'warn' : 'neutral'}>{diffs.length ? `${diffs.length} 项` : '无变更'}</Chip></div>
      {diffs.length ? <div className="diff-list">{diffs.map(item => <div className="diff-row" key={item.name}>
        <span className="grow">{item.name}</span><span className="from">{item.from}</span>
        <ArrowRight size={16} className="arrow" aria-hidden="true"/><span className="to">{item.to}</span>
      </div>)}</div> : <p className="footnote">保存的草稿与当前有效配置一致，无需重载。</p>}
    </div>
    <div className="info"><Info size={18} aria-hidden="true"/><div><strong>重载一次性发布整份草稿</strong>
      <p>校验失败时保留当前有效版本和草稿。运行中任务固定原配置；排队任务启动前按新版本重新校验。</p></div></div>
    {dirty && <div className="warn-box"><TriangleAlert size={18} aria-hidden="true"/><div><strong>有未保存的表单编辑</strong><p>保存或放弃当前编辑后才能重载配置。</p></div></div>}
    <div className="actions">
      <button className="secondary" disabled={busy} onClick={onRefresh}><RotateCcw size={16} aria-hidden="true"/>刷新配置</button>
      <button className="primary" disabled={busy || !state || dirty} onClick={onPublish}><RefreshCw size={16} aria-hidden="true"/>重载配置并生效</button>
    </div>
    <p className="footnote">运行状态：配置读取成功 · 最近一次数据源测试可在日志中查看。未检测的服务状态显示未知。</p>
    <details className="effective-details">
      <summary>查看各分组已生效值</summary>
      <div className="effective-summary">
        <h3>认证</h3><p>{`issuer_url: ${effective?.auth.issuer_url || '未配置'}\ncredential_ref: ${effective?.auth.credential_ref || '未配置'}`}</p>
        <h3>模型</h3><p>{`model_id: ${effective?.model.model_id || '未配置'} · timeout_seconds: ${effective?.model.timeout_seconds ?? '—'}`}</p>
        <h3>工具与上下文</h3><p>{`SQL ${effective?.tools.sql_enabled ? '已启用' : '已停用'} · ${effective?.tools.allowed_commands.join(' / ') || '无批准命令'}\ncontext_window_tokens: ${effective?.context.context_window_tokens ?? '—'}`}</p>
        <h3>解析限制与数据源</h3><p>单文件 ≤ 20 MB · 会话 ≤ 200 MB<br/>仅展示脱敏配置，密钥原文不回显</p>
      </div>
      <details className="effective-full"><summary aria-label="完整脱敏配置" title="完整脱敏配置"><SlidersHorizontal size={16} aria-hidden="true"/></summary><pre>{JSON.stringify(state?.effective_config,null,2)}</pre></details>
    </details>
  </>;
}

function AuthTab({draft, secrets, setSecrets, roles, setRoles, field, busy, conflict, onTestModel}: {
  draft: Bundle; secrets: {auth: string; model: string}; setSecrets: (value: {auth: string; model: string}) => void;
  roles: string; setRoles: (value: string) => void; field: (group: Exclude<keyof Bundle, 'data_sources'>, key: string, label: string, type?: string, hint?: string) => ReactNode;
  busy: boolean; conflict: boolean; onTestModel: () => void;
}) {
  const [modelExpanded,setModelExpanded] = useState(false);
  return <div className="admin-grid auth-config">
    <section className="card auth-provider">
      <h2>认证中心</h2>
      <div className="stack" style={{gap: 16}}>{field('auth', 'issuer_url', '认证服务地址')}{field('auth', 'client_id', '客户端标识')}{field('auth', 'callback_url', '回调地址')}</div>
      <div className="credential-row"><Chip tone="good">{draft.auth.credential_ref ? '凭证已配置' : '未配置凭证'}</Chip>
        <details className="credential-editor"><summary className="secondary"><KeyRound size={16} aria-hidden="true"/>替换凭证</summary>
          <label className="field"><span className="label">替换认证凭证</span>
        <input type="password" autoComplete="new-password" value={secrets.auth} placeholder="留空保留现有凭证" onChange={e => setSecrets({...secrets, auth: e.target.value})}/>
        <small>当前引用：{draft.auth.credential_ref || '未配置'}</small></label>
        </details></div>
      <div className="field"><span className="label">角色映射</span>
        <details className="role-editor"><summary><span className="role-summary">{Object.entries(draft.auth.role_mapping).map(([name, role]) => `${name} → ${role === 'admin' ? '系统管理员' : role === 'analyst' ? '分析用户' : role}`).join(' / ')}</span><span className="role-expanded">角色映射（JSON）</span></summary>
          <label className="field"><span className="sr-only">角色映射（JSON）</span><textarea rows={3} value={roles} spellCheck={false} onChange={e => setRoles(e.target.value)}/></label>
        </details></div>
      <p className="footnote">未映射角色不会自动获得管理员权限。</p>
      <details className="advanced-config"><summary aria-label="高级认证配置" title="高级认证配置"><Settings2 size={16} aria-hidden="true"/></summary>
        <div className="stack" style={{gap: 16}}><span className="label">高级认证配置 · 已展开</span>{field('auth', 'issuer_internal_url', '服务端认证地址')}{field('auth', 'role_claim', '角色声明字段')}</div>
      </details>
    </section>
    <section className="card">
      <h2>外部模型</h2>
      {field('model', 'api_url', 'base_url')}
      {field('model', 'model_id', '模型名称')}
      <div className="credential-row"><Chip tone="good">{draft.model.credential_ref ? 'api_key · 已设置（遮掩）' : 'api_key · 未配置'}</Chip>
        <details className="credential-editor" onToggle={event => setModelExpanded(event.currentTarget.open)}><summary className="secondary"><KeyRound size={16} aria-hidden="true"/>替换凭证</summary>
      <label className="field"><span className="label">api_key</span>
        <input type="password" aria-label="api_key" autoComplete="new-password" onFocus={event => event.currentTarget.select()}
          value={secrets.model} onChange={e => setSecrets({...secrets, model: e.target.value})}/>
        <small>{draft.model.credential_ref ? '已配置 · 原值不回显，清空并保存可移除' : '未配置'}</small></label></details></div>
      {field('model', 'timeout_seconds', '完整调用时限', 'number', '当前有效值在重载后更新')}
      <div className="info"><Info size={18} aria-hidden="true"/><div>
        {modelExpanded ? <strong>测试连接 · 最小真实请求，不保存配置</strong> : <button className="model-test" type="button" disabled={busy || conflict || !draft.model.api_url || !draft.model.model_id || !secrets.model} onClick={onTestModel} title="发起最小真实请求，可能产生少量模型费用">
          测试连接 · 最小真实请求，不保存配置</button>}
        <p>不得使用预置答案或模拟成功降级。</p></div></div>
      {modelExpanded && <button className="secondary" type="button" style={{alignSelf:'flex-start'}} aria-label="测试连接 · 最小真实请求，不保存配置" disabled={busy || conflict || !draft.model.api_url || !draft.model.model_id || !secrets.model} onClick={onTestModel}>测试模型连接</button>}
    </section>
  </div>;
}

function ToolsTab({draft, field, change}: {
  draft: Bundle;
  field: (group: Exclude<keyof Bundle, 'data_sources'>, key: string, label: string, type?: string, hint?: string) => ReactNode;
  change: (group: Exclude<keyof Bundle, 'data_sources'>, field: string, value: unknown) => void;
}) {
  return <div className="admin-grid admin-grid--three">
    <section className="card">
      <h2>工具与执行时限</h2>
      <div className="row tool-toggles">
        <label className={`chip chip--${draft.tools.sql_enabled ? 'good' : 'neutral'}`}><input type="checkbox" aria-label="SQL 查询" checked={draft.tools.sql_enabled} onChange={e => change('tools','sql_enabled',e.target.checked)}/>{draft.tools.sql_enabled ? 'SQL 已启用' : 'SQL 已停用'}</label>
        <label className={`chip chip--${draft.tools.command_enabled ? 'good' : 'neutral'}`}><input type="checkbox" aria-label="批准命令" checked={draft.tools.command_enabled} onChange={e => change('tools','command_enabled',e.target.checked)}/>{draft.tools.command_enabled ? '批准脚本已启用' : '批准脚本已停用'}</label>
      </div>
      <div className="stack" style={{gap: 14}}>
        {field('tools', 'sql_timeout_seconds', 'SQL 查询时限', 'number')}
        {field('tools', 'command_timeout_seconds', '单次命令时限', 'number')}
        {field('tools', 'task_timeout_seconds', '运行任务时限', 'number')}
        {field('tools', 'cleanup_step_timeout_seconds', '清理单阶段时限', 'number')}
      </div>
      <details className="command-details"><summary aria-label="允许的批准命令"><SlidersHorizontal size={16} aria-hidden="true"/></summary><span className="command-label">允许的批准命令</span><div className="command-options">
        {(['python_stats', 'text_search'] as const).map(command => <label className="check" key={command}>
          <input type="checkbox" checked={draft.tools.allowed_commands.includes(command)}
            onChange={e => change('tools', 'allowed_commands', e.target.checked
              ? [...draft.tools.allowed_commands, command]
              : draft.tools.allowed_commands.filter(value => value !== command))}/>
          {command}</label>)}
      </div></details>
    </section>
    <section className="card">
      <h2>上下文与摘要</h2>
      <div className="stack" style={{gap: 14}}>
        {field('context', 'context_window_tokens', '模型窗口（必填）', 'number')}
        {field('context', 'response_reserved_tokens', '响应保留量', 'number')}
        {field('context', 'summary_trigger_ratio', '摘要触发比例', 'number')}
        {field('context', 'summary_max_tokens', '摘要最大长度', 'number')}
        {field('context', 'recent_message_count', '保留最近消息数', 'number')}
      </div>
      <p className="footnote">触发预算 = floor((窗口 − 响应保留) × 比例){"\n"}窗口须大于响应保留与摘要量之和。</p>
    </section>
    <section className="card">
      <h2>附件解析限制</h2>
      <div className="stack" style={{gap: 14}}>
        {field('parse_limits', 'parse_timeout_seconds', '单文件解析时限（秒，最多 60）', 'number')}
        {field('parse_limits', 'parse_max_memory_bytes', '解析内存（字节，最多 268435456）', 'number')}
        {field('parse_limits', 'xlsx_max_uncompressed_bytes', 'XLSX 解压总量（字节，最多 200000000）', 'number')}
        {field('parse_limits', 'parse_max_rows', '非空数据行数（最多 100000）', 'number')}
        {field('parse_limits', 'text_max_chars', '文本码点数（最多 2000000）', 'number')}
      </div>
      <p className="footnote">数据行不含表头，跨工作表累加。{"\n"}超限报错，不静默截断输入。</p>
    </section>
  </div>;
}

function SourcesTab({sources, busy, dirty, conflict, onNew, onEdit, onTest, onRemove}: {
  sources: Source[]; busy: boolean; dirty: boolean; conflict: boolean;
  onNew: () => void; onEdit: (source: Source) => void; onTest: (source: Source) => void; onRemove: (source: Source) => void;
}) {
  return <>
    <div className="row" style={{gap: 12, flexWrap: 'wrap'}}>
      <div className="info grow"><Info size={18} aria-hidden="true"/><div><strong>移除保留身份和历史引用</strong>
        <p>名称、凭证和状态可改；连接目标、database 与 schema 不可就地修改。</p></div></div>
      <button className="primary" disabled={busy || dirty || conflict} onClick={onNew}><Plus size={16} aria-hidden="true"/>新建数据源</button>
    </div>
    <div className="table-card">
      <div className="table-scroll">
        <div className="table-head sources-grid" role="row">
          <span>名称 / ID</span><span>固定业务范围</span><span>有效状态</span><span>草稿状态</span><span>凭证</span><span>操作</span>
        </div>
        {sources.map(source => {
          const name = (source.effective || source.pending)!.name;
          return <div className="table-row sources-grid" role="row" key={source.id}>
            <div className="cell"><strong>{name}</strong><small>{source.id}{source.pending && source.effective?.name !== source.pending.name ? ` · 草稿：${source.pending.name}` : ''}</small></div>
            <div className="cell"><span style={{fontSize: 12}}>{source.database_name} / {source.schema_name}</span><small>{source.connection_target.host}:{source.connection_target.port} · 只读范围</small></div>
            <div className="cell"><Chip tone={source.effective_status === 'enabled' ? 'good' : source.effective_status === 'removed' ? 'danger' : 'neutral'}>{source.effective_status === 'enabled' ? '已启用' : source.effective_status === 'disabled' ? '已停用' : source.effective_status === 'removed' ? '已移除' : source.effective_status}</Chip></div>
            <div className="cell">{source.pending
              ? <Chip tone="warn">{source.pending.status === 'enabled' ? '待启用' : source.pending.status === 'disabled' ? '待停用' : '待移除'}</Chip>
              : <Chip tone="neutral">无变更</Chip>}</div>
            <div className="cell"><Chip tone="neutral">已配置</Chip></div>
            <div className="cell"><div className="row-actions">
              <button className="secondary" disabled={busy || dirty || conflict} onClick={() => onEdit(source)}>编辑</button>
              <button className="secondary" disabled={busy} onClick={() => onTest(source)}>测试连接</button>
              <button className="btn--danger" disabled={busy || dirty || conflict} onClick={() => onRemove(source)}>移除</button>
            </div></div>
          </div>;
        })}
      </div>
      {!sources.length && <p className="empty-note">尚无数据源。运行样例初始化脚本或新建数据源。</p>}
    </div>
    <p className="footnote">仅已发布且启用的源出现在普通用户选择列表。源停用不自动改绑旧会话。</p>
  </>;
}

function LogsTab({items, filters, setFilters, busy, cursor, onQuery, onDetail}: {
  items: Log[]; filters: Record<string, string>; setFilters: (value: Record<string, string>) => void;
  busy: boolean; cursor: string | null; onQuery: (next?: string) => void; onDetail: (log: Log) => void;
}) {
  return <>
    <form onSubmit={e => {e.preventDefault(); onQuery();}} style={{display: 'contents'}}>
      <div className="log-filters">
        <label className="field"><span className="label">范围</span>
          <select value={filters.scope} onChange={e => setFilters({...filters, scope: e.target.value})}>
            <option value="all">全部日志</option><option value="system">系统操作</option><option value="task">任务日志</option></select></label>
        <div className="field"><span className="label">时间范围</span><details className="time-range">
          <summary>{filters.start_time || filters.end_time ? `${filters.start_time.replace('T',' ')} — ${filters.end_time.replace('T',' ')}` : '全部时间'} ▾</summary>
          <div className="time-range-fields">
          <label className="grow"><span className="sr-only">开始时间</span><input type="datetime-local" value={filters.start_time} onChange={e => setFilters({...filters, start_time: e.target.value})}/></label>
          <label className="grow"><span className="sr-only">结束时间</span><input type="datetime-local" value={filters.end_time} onChange={e => setFilters({...filters, end_time: e.target.value})}/></label>
        </div></details></div>
        <label className="field"><span className="label">级别</span>
          <select value={filters.level} onChange={e => setFilters({...filters, level: e.target.value})}>
            <option value="">全部级别</option>{['DEBUG', 'INFO', 'WARN', 'ERROR'].map(v => <option key={v}>{v}</option>)}</select></label>
        <label className="field"><span className="label">任务 ID</span><input value={filters.task_id} placeholder="输入任务 ID" onChange={e => setFilters({...filters, task_id: e.target.value})}/></label>
        <div className="actions"><button className="primary" disabled={busy}><Search size={16} aria-hidden="true"/>查询</button></div>
        <div className="log-related">
        <label className="field"><span className="label">操作者</span><input value={filters.operator_id} placeholder="全部操作者" onChange={e => setFilters({...filters, operator_id: e.target.value})}/></label>
        <label className="field"><span className="label">目标类型</span><input value={filters.target_type} placeholder="全部类型" onChange={e => setFilters({...filters, target_type: e.target.value})}/></label>
        <label className="field"><span className="label">目标 ID</span><input value={filters.target_id} placeholder="输入目标 ID" onChange={e => setFilters({...filters, target_id: e.target.value})}/></label>
        </div>
      </div>
    </form>
    <div className="table-card">
      <div className="table-scroll">
        <div className="table-head logs-grid"><span>时间</span><span>级别</span><span>事件</span><span>任务</span><span>操作者</span><span>目标</span><span>脱敏摘要</span></div>
        {items.map(log => <div className="table-row logs-grid" role="row" key={log.id}>
          <span className="log-row">{logTime(log.created_at)}</span>
          <span className="log-row"><span className={log.log_level === 'ERROR' ? 'level-error' : undefined}>{log.log_level}</span></span>
          <span className="log-row event">{log.log_type}</span>
          <span className="log-row">{log.task_id ?? '—'}</span>
          <span className="log-row">{log.operator_id ?? '系统'}</span>
          <span className="log-row">{log.target_type} {log.target_id}</span>
          <span className="log-row" style={{display: 'flex', flexDirection: 'row', alignItems: 'center', gap: 8}}>
            <span className="grow" style={{overflowWrap: 'anywhere'}}>{String((log.details as {message?: string}).message ?? log.log_type)}</span>
            <button className="log-detail" onClick={() => onDetail(log)}>查看详情</button>
          </span>
        </div>)}
      </div>
      {!items.length && <p className="empty-note">暂无符合筛选的日志</p>}
    </div>
    <div className="pager">
      <span>按时间与 ID 稳定分页 · 系统操作任务 ID 可为空</span>
      <button className="secondary" disabled={busy || !cursor} onClick={() => onQuery(cursor ?? undefined)}><ChevronDown size={16} aria-hidden="true"/>加载更多</button>
    </div>
    <div className="info"><Info size={18} aria-hidden="true"/><div><strong>日志只记录运行元数据和脱敏错误</strong>
      <p>未知事件以通用事件显示；管理员不能通过日志打开他人的会话正文。</p></div></div>
  </>;
}
