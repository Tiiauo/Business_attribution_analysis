import {useEffect, useRef, useState, type ReactNode} from 'react';
import {
  Archive, ArchiveRestore, ArrowLeft, ArrowRight, ArrowUp, ArrowUpRight, ChartNoAxesCombined, Check, ChevronDown,
  ChevronRight, CircleAlert, CircleCheck, Copy, Database, Download, Ellipsis, FileSearch, FileText, History,
  Info, LoaderCircle, LockKeyhole, LogOut, PanelLeft, Paperclip, Pencil, RotateCcw, Search, Settings2,
  Square, SquarePen, Trash2, TriangleAlert, Upload, X,
} from 'lucide-react';
import type {ConversationInfo, Evidence, HistoryPage, MessageInfo, ResultInfo, SendRequest, TaskSnapshot, UserInfo} from './contracts';
import {Modal} from './admin';
import {request, useTaskSession} from './task-session';
import {Attachments} from './attachments';
import './workbench.css';

type Source = {id: string; name: string; database: string; schema: string};
type ConversationPage = {items: ConversationInfo[]; next_cursor: string | null};
type Scope = 'active' | 'archived' | 'deleted';

const shanghai = (value: string) => {
  const parts = new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false}).formatToParts(new Date(value));
  const get = (type: string) => parts.find(part => part.type === type)?.value;
  return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')}`;
};
const clock = (value: string) => new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false}).format(new Date(value));
const dayLabel = (value: string) => {
  const date = new Date(value);
  const parts = new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false}).formatToParts(date);
  const get = (type: string) => parts.find(part => part.type === type)?.value ?? '';
  const localDay = (item: Date) => new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Shanghai', year:'numeric',month:'2-digit',day:'2-digit'}).format(item);
  const today = new Date(), yesterday = new Date(today.getTime() - 86400000);
  if (localDay(date) === localDay(today)) return `${get('hour')}:${get('minute')}`;
  if (localDay(date) === localDay(yesterday)) return '昨天';
  return `${get('month')} 月 ${get('day')} 日`;
};
const dayGroup = (value: string | null) => {
  if (!value) return '更早';
  const date = new Date(value);
  const localDay = (item: Date) => new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Shanghai', year:'numeric',month:'2-digit',day:'2-digit'}).format(item);
  if (localDay(date) === localDay(new Date())) return '今天';
  if (localDay(date) === localDay(new Date(Date.now() - 86400000))) return '昨天';
  return '更早';
};
const roleLabel = (role: UserInfo['role']) => role === 'admin' ? '系统管理员' : '分析用户';
const confidenceLabel: Record<Evidence['confidence'], string> = {high: '高', medium: '中', low: '低'};
const statusLabel = (status: TaskSnapshot['task_status']) => ({queued: '排队中', running: '分析中', success: '分析完成', failed: '分析失败', cancelled: '已取消'}[status]);
const messageLabel = (message: MessageInfo) => ({complete: '已完成', streaming: '进行中', failed: '失败，保留部分内容', cancelled: '已取消'}[message.message_status]);
const exportLabel = (status: string) => ({not_generated: '尚未生成', generating: '正在生成', success: '已生成', failed: '生成失败'}[status] ?? status);
const cleanupLabel: Record<string, string> = {queued: '等待清理', stopping: '正在停止作业', deleting_files: '正在清理文件', deleting_records: '正在清理记录', failed: '清理失败', none: '等待清理'};
const sourceStatusLabel: Record<string, string> = {enabled: '已启用', disabled: '已停用', removed: '已移除', not_published: '未发布', unselected: '未选择'};

function InfoBlock({title, body, tone = 'info'}: {title: string; body?: ReactNode; tone?: 'info' | 'warn'}) {
  const Icon = tone === 'warn' ? TriangleAlert : Info;
  return <div className={tone === 'warn' ? 'warn-box' : 'info'}>{<Icon size={18} aria-hidden="true" style={{color: tone === 'warn' ? 'var(--warn)' : 'var(--accent)'}}/>}<div><strong>{title}</strong>{body && <p>{body}</p>}</div></div>;
}

export function ConversationWorkbench({user, onLogout}: {user: UserInfo; onLogout: () => Promise<void>}) {
  const storageKey = `ba_conversation_${user.id}`;
  const [scope, setScope] = useState<Scope>(() => {
    const saved = sessionStorage.getItem(storageKey + '_scope');
    return saved === 'archived' || saved === 'deleted' ? saved : 'active';
  });
  const [selected, setSelected] = useState<string | null>(() => sessionStorage.getItem(storageKey));
  const [list, setList] = useState<ConversationInfo[]>([]), [listCursor, setListCursor] = useState<string | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [renaming, setRenaming] = useState(false), [title, setTitle] = useState(''), [revision, setRevision] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false), [query, setQuery] = useState(''), [scrolled, setScrolled] = useState(false);
  const viewEpoch = useRef(0), listEpoch = useRef(0);
  const {history, setHistory, tasks, result, selectedTask, loading, notice, synchronize, selectTask} = useTaskSession(user, selected, revision);
  const [draft, setDraft] = useState(''), [retry, setRetry] = useState<SendRequest | null>(null);
  const [taskKind, setTaskKind] = useState<'analysis' | 'final_report'>('analysis');
  const conversation = history?.conversation;
  const currentTask = conversation?.current_task_id ? tasks[conversation.current_task_id] : null;
  const taskBusy = Boolean(conversation?.current_task_id);
  const [attachmentIds, setAttachmentIds] = useState<string[]>([]);
  const [panel, setPanel] = useState<'result' | 'attachments' | 'evidence'>('result');
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState(false), [cleanupNotice, setCleanupNotice] = useState('');
  const [cleanupIds, setCleanupIds] = useState<string[]>(() => {
    try { return JSON.parse(sessionStorage.getItem(storageKey + '_cleanup') || '[]'); } catch { return []; }
  });

  async function api<T>(path: string, body?: unknown): Promise<T> {
    const response = await fetch('/api/chat/' + path, {
      method: body === undefined ? 'GET' : 'POST', cache: 'no-store',
      headers: body === undefined ? {} : {'Content-Type': 'application/json', 'X-CSRF-Token': user.csrf_token},
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const value = await response.json();
    if (!response.ok) throw new Error(`${value.message}（${value.code}；请求 ${value.request_id}）`);
    return value;
  }
  useEffect(() => {
    let active = true;
    fetch('/api/data-sources', {cache: 'no-store'}).then(async response => {
      if (!response.ok) throw new Error('数据源读取失败，请刷新后重试。');
      return response.json();
    }).then(value => {if (active) setSources(value.items);}).catch(reason => {if (active) setError(reason.message);});
    return () => {active = false;};
  }, [revision]);
  useEffect(() => {
    const epoch = ++listEpoch.current;
    sessionStorage.setItem(storageKey + '_scope', scope);
    setList([]); setListCursor(null);
    api<ConversationPage>('ls?status=' + scope).then(value => {
      if (epoch === listEpoch.current) {setList(value.items); setListCursor(value.next_cursor);}
    }).catch(reason => {if (epoch === listEpoch.current) setError(reason.message);});
    return () => {listEpoch.current++;};
  }, [scope, revision]);
  useEffect(() => {
    ++viewEpoch.current;
    setError(''); setRetry(null); setAttachmentIds([]); setPanel('result'); setEvidence(null); setDeleteConfirm(false); setTaskKind('analysis');
    if (selected) {
      sessionStorage.setItem(storageKey, selected);
      setDraft(sessionStorage.getItem(`${storageKey}_draft_${selected}`) || '');
    } else {sessionStorage.removeItem(storageKey); setDraft('');}
    return () => {viewEpoch.current++;};
  }, [selected, revision]);

  async function send(kind: 'analysis' | 'final_report', payload?: SendRequest) {
    if (!conversation || !conversation.data_source_id) return;
    const epoch = viewEpoch.current;
    if (kind === 'final_report') setAttachmentIds([]);
    const body = payload || {
      conversation_id: conversation.id, client_request_id: crypto.randomUUID(),
      content: draft.trim() || (kind === 'final_report' ? '请汇总本会话并生成最终报告' : ''),
      data_source_id: conversation.data_source_id, task_kind: kind,
      attachment_ids: kind === 'analysis' ? attachmentIds : [],
    };
    setRetry(body);
    const accepted = await request<{task_id: string}>(user, '/api/chat/send', body);
    if (epoch !== viewEpoch.current) return;
    setRetry(null); setDraft(''); setAttachmentIds([]); setPanel('result'); setTaskKind('analysis');
    sessionStorage.removeItem(`${storageKey}_draft_${conversation.id}`);
    selectTask(accepted.task_id); synchronize();
    const rows = await api<ConversationPage>('ls?status=' + scope);
    if (epoch === viewEpoch.current) {setList(rows.items); setListCursor(rows.next_cursor);}
  }

  async function cancelTask() {
    if (!currentTask) return;
    await request(user, `/api/tasks/${currentTask.task_id}/cancel`, {});
    synchronize();
  }
  async function run(action: () => Promise<void>) {
    setBusy(true); setError('');
    try {await action();} catch (reason) {setError((reason as Error).message);} finally {setBusy(false);}
  }
  async function create() {
    const row = await api<ConversationInfo>('create', {});
    setScope('active'); setSelected(row.id); setRevision(value => value + 1);
  }
  async function update(body: unknown) {
    await api<ConversationInfo>('update', body);
    setRevision(value => value + 1);
  }
  async function older() {
    if (!selected || !history?.next_cursor) return;
    const epoch = viewEpoch.current;
    const value = await api<HistoryPage>(`ls/${selected}?cursor=${encodeURIComponent(history.next_cursor)}`);
    if (epoch === viewEpoch.current) setHistory(current => current ? {
      ...current,
      items: [...new Map([...value.items, ...current.items].map(message => [message.id, message])).values()].sort((a, b) => a.seq_no - b.seq_no),
      next_cursor: value.next_cursor,
    } : current);
  }
  async function moreConversations() {
    if (!listCursor) return;
    const epoch = listEpoch.current;
    const value = await api<ConversationPage>(`ls?status=${scope}&cursor=${encodeURIComponent(listCursor)}`);
    if (epoch === listEpoch.current) {setList(current => [...current, ...value.items]); setListCursor(value.next_cursor);}
  }
  async function changeStatus() {
    if (!conversation) return;
    const next = conversation.status === 'archived' ? 'active' : 'archived';
    await api(next === 'active' ? 'restore' : 'archive', {conversation_id: conversation.id});
    setScope(next); setRevision(value => value + 1); setMenuOpen(false);
  }
  async function deleteConversation() {
    if (!conversation) return;
    await api('delete', {conversation_ids: [conversation.id]});
    setCleanupIds(ids => [...new Set([...ids, conversation.id])]); setDeleteConfirm(false); setScope('deleted'); setRevision(v => v + 1);
  }
  useEffect(() => {
    sessionStorage.setItem(storageKey + '_cleanup', JSON.stringify(cleanupIds));
    if (!cleanupIds.length) return;
    let alive = true, running = false;
    const failed = new Set<string>();
    async function poll() {
      if (running || document.visibilityState !== 'visible') return;
      running = true;
      try {
        for (const id of cleanupIds) {
          if (failed.has(id)) continue;
          const response = await fetch('/api/chat/ls/' + id, {cache: 'no-store', signal: AbortSignal.timeout(15000)});
          if (!alive) return;
          if (response.status === 404) {
            setCleanupIds(ids => ids.filter(value => value !== id)); setList(rows => rows.filter(row => row.id !== id));
            setCleanupNotice(`会话 #${id} 的数据和文件已清理。`); if (selected === id) setSelected(null);
          } else if (response.ok) {
            const value: HistoryPage = await response.json(); if (!alive) return;
            if (value.conversation.cleanup_status === 'failed') failed.add(id);
            setList(rows => rows.map(row => row.id === id ? value.conversation : row));
            if (selected === id) setHistory(value);
          } else setCleanupNotice('清理状态暂未确认，稍后继续查询。');
        }
      } catch {if (alive) setCleanupNotice('清理状态查询失败，稍后继续查询。');} finally {running = false;}
    }
    void poll();
    const timer = setInterval(() => void poll(), 2000);
    const visible = () => void poll();
    document.addEventListener('visibilitychange', visible);
    return () => {alive = false; clearInterval(timer); document.removeEventListener('visibilitychange', visible);};
  }, [cleanupIds, selected, revision]);
  async function exportResult(format: 'markdown' | 'html') {
    if (!result) return;
    await request(user, `/api/results/${result.task_id}/export`, {format}); synchronize();
  }
  useEffect(() => {
    if (!menuOpen) return;
    const close = () => setMenuOpen(false);
    document.addEventListener('click', close);
    return () => document.removeEventListener('click', close);
  }, [menuOpen]);

  const archived = conversation?.status === 'archived';
  const deleted = conversation?.status === 'deleted';
  const selectedTaskSnapshot = selectedTask ? tasks[selectedTask] ?? null : null;
  const ready = result && (!selectedTaskSnapshot || selectedTaskSnapshot.task_status === 'success');
  const marketRound = useRef<HTMLDivElement>(null);
  const liveRound = useRef<HTMLDivElement>(null);
  const market = Boolean(ready && result.task_kind === 'analysis' && !currentTask && sources.some(source => source.id === conversation?.data_source_id && source.schema === 'market_performance'));
  useEffect(() => {
    const node = currentTask ? liveRound.current : market ? marketRound.current : null, parent = node?.parentElement;
    if (node && parent) parent.scrollTop = node.offsetTop - parent.offsetTop - 56;
  }, [market, currentTask?.task_id, selectedTask, result?.result_id, panel]);
  const panelClass = panel === 'evidence' ? 'show-evidence' : panel === 'attachments' ? 'show-attachments' : 'show-result';
  const visibleList = query.trim() ? list.filter(row => `${row.title} ${row.data_source_name || ''}`.toLowerCase().includes(query.trim().toLowerCase())) : list;
  const conversationMenu = menuOpen && conversation && <div className="menu" role="menu" onClick={() => setMenuOpen(false)}
    onKeyDown={event => {
      if (event.key === 'Escape') {setMenuOpen(false); event.currentTarget.parentElement?.querySelector<HTMLButtonElement>('[aria-haspopup="menu"]')?.focus();}
      if (['ArrowDown','ArrowUp','Home','End'].includes(event.key)) {
        event.preventDefault();const rows=[...event.currentTarget.querySelectorAll<HTMLButtonElement>('button')];
        const index=rows.indexOf(document.activeElement as HTMLButtonElement);
        rows[event.key === 'Home' ? 0 : event.key === 'End' ? rows.length-1 : (index+(event.key === 'ArrowDown' ? 1 : -1)+rows.length)%rows.length]?.focus();
      }
    }}>
    <button role="menuitem" onClick={() => {setTitle(conversation.title); setRenaming(true);}}><Pencil size={18} aria-hidden="true"/>重命名…</button>
    <button role="menuitem" onClick={() => void run(changeStatus)}>{archived ? <ArchiveRestore size={18} aria-hidden="true"/> : <Archive size={18} aria-hidden="true"/>}{archived ? '恢复会话' : '归档会话'}</button>
    <button role="menuitem" className="danger" onClick={() => setDeleteConfirm(true)}><Trash2 size={18} aria-hidden="true"/>删除会话…</button>
  </div>;

  return <div className="wb">
    <header className="toolbar">
      <div className="brand">
        <span className="brand-mark"><ChartNoAxesCombined size={22} aria-hidden="true"/></span>
        <span className="brand-text"><strong>经营归因分析系统</strong><small>Business Attribution Suite</small></span>
      </div>
      <div className="trail">
        <span className={`conn ${notice ? 'is-offline' : ''}`}><i aria-hidden="true"/>{notice ? '正在重连' : '已连接'}</span>
        {user.role === 'admin' && <a className="secondary trail-link" href="/admin"><Settings2 size={16} aria-hidden="true"/>系统管理</a>}
        <span className="identity-wrap" aria-label={`当前身份：${roleLabel(user.role)}`}>
          <span className="avatar" aria-hidden="true">{user.display_name.slice(0, 1)}</span>
          <span className="identity">{user.display_name} · {roleLabel(user.role)}</span>
        </span>
        <button className="secondary" disabled={busy} onClick={() => void run(onLogout)}><LogOut size={16} aria-hidden="true"/>退出</button>
      </div>
    </header>
    <div className={`wb-body ${panelClass} ${scope === 'deleted' || deleted ? 'cleanup-view' : ''}`}>
      <aside className="wb-side" aria-label="会话侧栏">
        <div className="side-nav"><PanelLeft size={17} aria-hidden="true"/><span>会话</span><span className="side-count">{list.length}</span></div>
        <button className="primary side-new" disabled={busy} onClick={() => void run(create)}><SquarePen size={16} aria-hidden="true"/>新建会话</button>
        <label className="side-search">
          <Search size={15} aria-hidden="true"/>
          <input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索会话或数据源" aria-label="搜索会话"/>
          {query && <button type="button" aria-label="清除搜索" onClick={() => setQuery('')}><X size={13} aria-hidden="true"/></button>}
        </label>
        <div className="seg" role="group" aria-label="会话分类">
          {(['active', 'archived'] as const).map(value =>
            <button key={value} className={`seg-item ${scope === value ? 'is-active' : ''}`} aria-pressed={scope === value}
              disabled={busy} onClick={() => {setScope(value); setSelected(null);}}>
              {value === 'active' ? '活跃' : '已归档'}
            </button>)}
        </div>
        <div className="side-scroll">
          <nav className="conv-list" aria-label="会话列表">
            {(scope === 'active' ? ['今天', '昨天', '更早'] : [scope === 'archived' ? '已归档会话' : '待清理会话']).map(label => {
              const rows = scope === 'active' ? visibleList.filter(row => dayGroup(row.last_message_at || row.created_at) === label) : visibleList;
              if (!rows.length) return null;
              return <div className="conv-group" key={label}>
                <span className="side-date">{label}</span>
                {rows.map(row => <button key={row.id} className="conv-item" aria-current={selected === row.id ? 'page' : undefined}
                  disabled={busy} onClick={() => {setSelected(row.id);}}>
                  <span className="conv-dot" aria-hidden="true"/>
                  <span className="conv-copy"><strong>{row.title}</strong><span>{row.last_message_at || row.created_at ? `${dayLabel(row.last_message_at || row.created_at)} · ${row.data_source_name || '尚未选源'}` : '尚未选源'}</span></span>
                </button>)}
              </div>;
            })}
            {!visibleList.length && <p className="side-empty">{query ? '没有匹配的会话' : scope === 'active' ? '还没有活跃会话' : scope === 'archived' ? '暂无已归档会话' : '暂无待清理会话'}</p>}
          </nav>
          {listCursor && <button className="secondary more-conversations" disabled={busy} onClick={() => void run(moreConversations)}><ChevronDown size={15} aria-hidden="true"/>加载更多会话</button>}
        </div>
        <div className="side-foot-area">
          <button className="side-cleanup" disabled={busy} onClick={() => {setScope('deleted'); setSelected(null);}}><Trash2 size={15} aria-hidden="true"/>查看删除进度</button>
          <p className="side-foot">仅显示本人的会话与文件</p>
        </div>
      </aside>
      <main className="wb-main" aria-busy={loading || busy}>
        {scope !== 'deleted' && !deleted && <div className="conv-head">
          <div className="grow">
            <h1>{conversation?.title || (selected ? '读取中…' : '新会话')}</h1>
            <div className="conv-sub">
              {conversation?.data_source_locked ? <LockKeyhole size={13} aria-hidden="true"/> : <Database size={13} aria-hidden="true"/>}
              <span>{conversation?.data_source_id
                ? `${conversation.data_source_name || '数据源'} · ${conversation.data_source_locked ? '已绑定，换源请新建会话' : '首次问题接受后锁定'}`
                : '尚未选择数据源 · 首次问题接受后锁定'}</span>
            </div>
          </div>
          <div className="conv-head-actions">
            <button className="secondary" disabled={busy || !conversation || taskKind === 'final_report'} onClick={() => setPanel('attachments')}><Paperclip size={16} aria-hidden="true"/>附件{attachmentIds.length ? ` ${attachmentIds.length}` : ''}</button>
            <div className="head-menu">
              <button className="secondary" aria-haspopup="menu" aria-expanded={menuOpen} disabled={busy || !conversation}
                onKeyDown={event => {if (event.key === 'ArrowDown') {event.preventDefault(); const parent=event.currentTarget.parentElement; setMenuOpen(true); requestAnimationFrame(() => parent?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus());}}}
                onClick={event => {event.stopPropagation(); setMenuOpen(value => !value);}}><Ellipsis size={16} aria-hidden="true"/>更多</button>
              {conversationMenu}
            </div>
          </div>
        </div>}

        {error && !renaming && <div className="page-alert"><div className="danger-box" role="alert"><CircleAlert size={18} aria-hidden="true"/><div><strong>操作未完成</strong><p>{error}</p>{/SESSION_EXPIRED|UNAUTHENTICATED/.test(error) && <a className="secondary" href="/auth/login">重新登录</a>}</div></div></div>}
        {notice && <div className="page-alert"><div className="info" role="status"><LoaderCircle size={18} aria-hidden="true"/><div><strong>连接状态</strong><p>{notice}</p>{/SESSION_EXPIRED|UNAUTHENTICATED/.test(notice) ? <a className="secondary" href="/auth/login">重新登录</a> : <button className="secondary" onClick={() => setRevision(value => value + 1)}>重新连接</button>}</div></div></div>}
        {cleanupNotice && <div className="page-alert"><div className="info" role="status"><Info size={18} aria-hidden="true"/><div><strong>清理进度</strong><p>{cleanupNotice}</p></div></div></div>}
        {loading && <div className="page-alert"><p className="footnote" role="status">正在读取会话…</p></div>}
        {conversation && !deleted && conversation.data_source_id && conversation.data_source_status !== 'enabled' && <div className="page-alert">
          <InfoBlock tone="warn" title={`绑定数据源${sourceStatusLabel[conversation.data_source_status] || '不可用'}`} body={<>{'保留原绑定和历史，不自动换源。更换数据源请新建会话。'}<button className="secondary" onClick={() => void run(create)} disabled={busy}>新建会话</button></>}/>
        </div>}

        {(scope === 'deleted' || deleted) && <div className="conv-scroll cleanup-content">
          <h2>删除进度</h2>
          <p className="empty-lead">只保留清理所需元数据。已删除会话不能恢复分析。</p>
          {list.length ? <section className="card cleanup-list">{list.map(row => <article key={row.id}>
            <div className="row"><h3 className="grow">{row.title}</h3>
              {row.can_retry && <button className="secondary" disabled={busy} onClick={() => void run(async () => {
                await api('delete', {conversation_ids: [row.id]});
                setCleanupIds(ids => [...new Set([...ids, row.id])]); setRevision(value => value + 1);
              })}><RotateCcw size={16} aria-hidden="true"/>重试清理</button>}</div>
            <span className={`chip chip--${row.cleanup_status === 'failed' ? 'danger' : 'accent'}`}>{cleanupLabel[row.cleanup_status] || row.cleanup_status}</span>
            <p className="footnote">{row.cleanup_error || '已停止分析与解析作业，正在清理附件、导出和工作目录。'}{row.cleanup_updated_at ? ` 最后更新 ${clock(row.cleanup_updated_at)}。` : ''}</p>
          </article>)}</section> : <p className="footnote">没有待清理的会话。</p>}
          <InfoBlock title="处理中只查询，失败项单独重试" body="已确认完成的项目移出列表，不重新提交包含已清理 ID 的旧批次。"/>
          <p className="footnote">当前记录每 2 秒同步。清理失败时停止该项自动轮询，等待重试。<br/>未确认受理的资源缺失时显示“删除结果未确认”，不会直接宣告成功。</p>
        </div>}

        {!selected && scope !== 'deleted' && <div className="conv-scroll empty">
          <FileSearch size={40} aria-hidden="true" style={{color: 'var(--secondary)'}}/>
          <h2 className="empty-title">今天想弄清什么经营问题？</h2>
          <p className="empty-lead">先选择一个数据源。首次问题接受后，该会话会绑定此源。</p>
          <button className="primary" disabled={busy} onClick={() => void run(create)}><SquarePen size={16} aria-hidden="true"/>新建会话</button>
          <InfoBlock title="每轮附件初始不勾选" body="仅使用附件分析时，也需要绑定启用的数据源。"/>
        </div>}

        {selected && archived && <ArchivedView conversation={conversation} result={ready ? result : null} messages={history?.items || []} busy={busy}
          onRestore={() => void run(changeStatus)}/>}

        {selected && conversation && !archived && !deleted && <>
          <div className={`conv-scroll ${scrolled ? 'is-scrolled' : ''} ${panel === 'attachments' || panel === 'evidence' ? 'context-guide' : taskKind === 'final_report' ? 'report-preparation' : !history?.items.length ? 'empty' : ready && result.task_kind === 'final_report' ? 'report-saved' : ''}`} aria-label="消息历史"
            onScroll={event => { const next = event.currentTarget.scrollTop > 6; if (next !== scrolled) setScrolled(next); }}>
            {taskKind === 'final_report' ? <>
              <h2>将多轮发现汇总为一份报告</h2>
              <p className="empty-lead">接受请求时固定汇总范围，不覆盖之前轮次；之后仍可继续追问。</p>
              <section className="card analysis-scope"><h3>本会话已有的分析</h3>
                {history.items.filter(message => message.role === 'user' && message.task_id && tasks[message.task_id]?.task_kind !== 'final_report').map(message => {
                  const snapshot = tasks[message.task_id!];
                  return <div className="row" key={message.id}><CircleCheck size={18} className="good" aria-hidden="true"/><span className="grow">{roundLabel(history.items, message)} · {message.content}</span><small>{snapshot ? statusLabel(snapshot.task_status) : '已保存 · 接受请求时核对状态'}</small></div>;
                })}
                {!history.items.some(message => message.role === 'user') && <p className="footnote">尚无可汇总分析，请先完成普通分析。</p>}
              </section>
              <InfoBlock title="新增资料请先发送普通分析，再生成最终报告" body="最终报告不重新查询数据库、不读取新附件，仅汇总固定范围的已保存结果和证据。"/>
            </> : panel === 'attachments' ? <>
              <h2>补充资料，继续验证判断</h2>
              <p className="empty-lead">上传成功后，在侧栏勾选本轮需要使用的文件。<br/>未勾选文件仍保留在会话中，不会自动进入本轮分析。</p>
              <InfoBlock title={attachmentIds.length ? `本轮已选择 ${attachmentIds.length} 个文件` : '本轮尚未选择文件'} body="每轮选择初始为空，不自动沿用上一轮。"/>
              <div className="draft-preview"><span className="footnote">问题草稿</span><p>{draft || '尚未输入问题'}</p></div>
            </> : panel === 'evidence' && evidence ? <>
              <h2>每一个判断，都能回到证据。</h2>
              <p className="footnote">{selectedTaskSnapshot?.task_kind === 'final_report' ? '最终报告' : '补充资料分析'}</p>
              <h3 className="section-title">04 归因结论</h3>
              <p className="evidence-conclusion">{result?.conclusion_text || evidence.evidence_text}</p>
              <div className="row"><span className="chip chip--accent">{evidence.evidence_id} · {evidence.related_metric}</span>{evidence.confidence !== 'high' && <span className="chip chip--warn">待验证假设</span>}</div>
              <InfoBlock title="保留历史摘录，不代表可重新读取原文件" body="读取原附件仍需本轮显式选择；最终报告只使用已保存证据。"/>
            </> : ready && result.task_kind === 'final_report' ? <>
              <span className="chip chip--good">最终报告已保存</span>
              <h2>{conversation.title}分析报告</h2>
              <ReportScope scope={result.final_report_scope}/>
              <MessageBody className="report-summary" text={selectedTaskSnapshot?.messages.filter(message => message.role === 'assistant').map(message => message.content).join('\n\n') || result.conclusion_text}/>
              <ReportFiles result={result} busy={busy} onExport={format => void run(() => exportResult(format))}/>
              <p className="footnote">来源状态截至结果生成时；当前状态以系统为准，离线文件不会自动更新。</p>
            </> : market && result && selectedTaskSnapshot ? <>
              <div className="market-history" aria-label="更早的分析过程">{history.items.filter(message => message.task_id !== result.task_id).map(message => message.role === 'tool' ? <ToolHistory key={message.id} message={message} items={history.items}/> : <div key={message.id} className={message.role === 'user' ? 'msg-user' : 'msg-answer'}>
                <MessageBody text={message.content}/>
              </div>)}</div>
              <div className="market-round" ref={marketRound}>
                {selectedTaskSnapshot.messages.filter(message => message.role === 'user').map(message => <UserMessage key={message.id} message={message} items={history.items}/>)}
                {selectedTaskSnapshot.messages.filter(message => message.role === 'assistant').map(message => <div className="msg-answer" key={message.id}>
                  <p className="lead">{message.content.split('\n\n')[0]}</p>{message.content.includes('\n\n') && <MessageBody text={message.content.slice(message.content.indexOf('\n\n') + 2)}/>}
                </div>)}
                <ToolProcess key={selectedTaskSnapshot.task_id} steps={selectedTaskSnapshot.messages.filter(message => message.role === 'tool')} label={selectedTaskSnapshot.messages.length ? roundLabel(history.items, selectedTaskSnapshot.messages[0]) : '本轮'}/>
                <InfoBlock title="ROAS 不等于利润率或 ROI" body="销售收入按有效支付订单实付金额、退款前统计。不能据此证明广告策略导致下降。"/>
                {result.evidence_list.filter(item => item.source_type === 'file').map(item => <div className="row market-file" key={item.evidence_id}><FileText size={18} aria-hidden="true"/><span className="grow">{item.source_name}</span><button className="secondary" onClick={() => {setEvidence(item);setPanel('evidence');}}>查看证据</button></div>)}
              </div>
            </> : !history?.items.length
              ? <SourcePicker sources={sources} busy={busy} conversation={conversation} onPick={id => void run(() => update({conversation_id: conversation.id, data_source_id: id}))}/>
              : <>
            {history?.next_cursor && <button className="secondary older" disabled={busy} onClick={() => void run(older)}>加载更早消息</button>}
            <p className="date-line">{new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', year: 'numeric', month: 'long', day: 'numeric'}).format(new Date(history.items[0]?.created_at || Date.now()))} · 当前会话</p>
            {!history?.items.length && !currentTask && <InfoBlock title="还没有消息" body="选择一个数据源并发送第一个问题；接受后本会话会绑定该源。"/>}
            {history?.items.map(message => message.task_id === currentTask?.task_id ? null : message.role === 'tool'
              ? <ToolHistory key={message.id} message={message} items={history.items}/>
              : message.role === 'user'
                ? <UserMessage key={message.id} message={message} items={history.items}/>
                : <div className={`msg-answer ${message.task_id === result?.task_id ? 'msg-answer--selected' : ''}`} key={message.id}>
                    {(message.message_status !== 'complete' || !history.items.some(item => item.role === 'assistant' && item.seq_no < message.seq_no)) && <span className={`chip ${message.message_status === 'complete' ? 'chip--good' : message.message_status === 'failed' ? 'chip--danger' : 'chip--accent'}`}>
                      {message.message_status === 'complete' ? '分析完成' : messageLabel(message)}
                    </span>}
                    {message.content.includes('\n\n') && history.items.some(item => item.role === 'assistant' && item.seq_no < message.seq_no)
                      ? <><p className="lead">{message.content.split('\n\n')[0]}</p><MessageBody text={message.content.slice(message.content.indexOf('\n\n') + 2)}/></>
                      : <><MessageBody text={message.content.split('\n\n')[0] || '正在生成回答…'}/>
                          {message.content.includes('\n\n') && <MessageBody className="note" text={message.content.slice(message.content.indexOf('\n\n') + 2)}/>}</>}
                    {message.message_status !== 'complete' && <p className="note">{messageLabel(message)} · {clock(message.created_at)}</p>}
                    {message.task_id && message.task_id !== result?.task_id && <div className="actions"><button className="secondary" onClick={() => selectTask(message.task_id)}><ArrowUpRight size={16} aria-hidden="true"/>查看{roundLabel(history.items, message)}结果</button></div>}
                  </div>)}
            {currentTask && <div className="live-round" ref={liveRound}>
              {history.items.filter(message => message.role === 'user' && message.task_id === currentTask.task_id).map(message => <UserMessage key={message.id} message={message} items={history.items}/>)}
              <TaskCard task={currentTask} busy={busy} onCancel={() => void run(cancelTask)}/>
              <p className="footnote">任务执行期间不能新增或删除附件。切换会话、刷新或断线不会取消分析。</p>
            </div>}
            {!currentTask && selectedTaskSnapshot?.task_status === 'success' && <>
              <InfoBlock title="先定位变化，再验证原因" body="结合证据与待补充数据验证判断，避免直接将相关性视为因果。"/>
            </>}
              </>}
          </div>
          <Composer
            conversation={conversation} draft={draft} setDraft={value => {setDraft(value); setRetry(null); if (conversation) sessionStorage.setItem(`${storageKey}_draft_${conversation.id}`, value);}}
            taskBusy={taskBusy} busy={busy} attachmentCount={attachmentIds.length} taskKind={taskKind} setTaskKind={kind => {setTaskKind(kind); if (kind === 'final_report') {setAttachmentIds([]); setPanel('result');}}}
            onAttach={() => setPanel('attachments')}
            onSend={() => void run(() => send(taskKind))}
            onRetry={retry ? () => void run(() => send(retry.task_kind || 'analysis', retry)) : null}
          />
        </>}
      </main>

      {taskKind === 'final_report' ? <aside className="report-range" aria-label="报告范围">
          <h2>报告范围</h2><span className="chip chip--accent">最终范围由服务端接受时固定</span>
          <p>包括前序成功普通分析。<br/>失败或取消分析仅列为未完成事项。<br/>此前最终报告不重复纳入。</p>
          <div className="divider"/><h3>本轮附件</h3>
          <div className="range-attachment"><input type="checkbox" disabled aria-label="最终报告不接受新增附件"/><span>不使用新附件</span></div>
          <small>最终报告不接受新增附件，已清空本轮选择。</small>
          <small>汇总要求中的新数值或事实，只作为待验证要求。</small>
        </aside> : panel === 'attachments' && conversation
        ? <aside className="att-panel" aria-label="会话附件">
            <div className="row"><h2 className="grow">会话附件</h2><button className="secondary" onClick={() => setPanel('result')}><X size={16} aria-hidden="true"/>关闭</button></div>
            <Attachments key={conversation.id} user={user} conversationId={conversation.id} readOnly={archived || deleted} taskBusy={taskBusy}
              selected={attachmentIds} onSelect={setAttachmentIds} onChange={synchronize}/>
          </aside>
        : panel === 'evidence' && evidence
          ? <aside className="evidence-panel" aria-label="证据详情">
              <div className="row"><h2 className="grow">证据 {evidence.evidence_id}</h2><button className="secondary" onClick={() => {setEvidence(null); setPanel('result');}}><X size={16} aria-hidden="true"/>关闭</button></div>
              <div className="tags"><span className="chip chip--neutral">{evidence.source_type === 'database' ? '数据库' : evidence.source_type === 'file' ? '附件文件' : '工具产物'}</span>
                <span className={`chip ${evidence.confidence === 'high' ? 'chip--good' : evidence.confidence === 'medium' ? 'chip--warn' : 'chip--neutral'}`}>支撑强度 · {confidenceLabel[evidence.confidence]}</span></div>
              <p className="big">{evidence.source_name}</p>
              <p className="quote">{evidence.evidence_text}</p>
              <p className="meta">相关指标：{evidence.related_metric}{evidence.confidence !== 'high' ? '\n支撑说明：证据尚有缺口，不能据此确认因果。' : ''}</p>
              <div className="divider"/>
              <h3>来源定位</h3>
              <EvidenceLocator evidence={evidence} taskId={result?.task_id}/>
              <div className="divider"/>
              <h3>来源状态</h3>
              <div className="two-time">
                <span className={`chip ${evidence.source_locator.available_at_result === true ? 'chip--good' : 'chip--warn'}`}>生成时 · {availabilityText(evidence.source_locator.available_at_result,evidence.source_locator.unavailable_reason_at_result)}</span>
                <p className="src">{result ? `结果生成于 ${shanghai(result.created_at)}` : '尚未记录结果生成时间'}</p>
                <span className={`chip ${evidence.source_locator.available === true ? 'chip--good' : 'chip--warn'}`}>当前 · {availabilityText(evidence.source_locator.available,evidence.source_locator.unavailable_reason)}</span>
                <p className="src">{evidence.source_locator.availability_checked_at ? `检查于 ${shanghai(evidence.source_locator.availability_checked_at)}` : '尚未记录检查时间'}{evidence.source_locator.available === false ? ' · 无法再次读取原件' : ''}</p>
              </div>
              <InfoBlock title="报告正文保留生成时状态" body="复制与离线文件不会自动更新；当前状态以系统为准。"/>
            </aside>
          : <ResultPanel result={ready ? result : null} task={selectedTaskSnapshot} busy={busy} onCopy={async text => {await navigator.clipboard.writeText(text);}} onExport={format => void run(() => exportResult(format))} onEvidence={item => {setEvidence(item); setPanel('evidence');}} taskKindLabel={ready && result?.task_kind === 'final_report' ? '最终报告' : '分析结果'}
              rounds={(history?.items || []).filter(message => message.role === 'user').map((message,index) => ({id:message.task_id,label:`第 ${index + 1} 轮 · ${tasks[message.task_id]?.task_kind === 'final_report' ? '最终报告' : message.content}`}))} onSelectTask={selectTask}/>}
    </div>

    {deleteConfirm && conversation && <Modal title={`删除“${conversation.title}”？`} onClose={() => setDeleteConfirm(false)} icon={<Trash2 size={30} aria-hidden="true" style={{color: 'var(--danger)'}}/>}>
      <p>将清理本会话全部消息、附件、任务、结果、上下文摘要及相关文件。未结束作业将被终止。</p>
      <div className="warn-box"><TriangleAlert size={18} aria-hidden="true"/><div><strong>删除后不能恢复</strong><p>已下载到本机的文件不会被撤回。</p></div></div>
      {error && <div className="danger-box" role="alert"><CircleAlert size={18} aria-hidden="true"/><div><strong>删除未提交</strong><p>{error}</p></div></div>}
      <div className="dialog-actions">
        <button className="primary" autoFocus data-autofocus onClick={() => setDeleteConfirm(false)}>保留会话</button>
        <button className="btn--danger" disabled={busy} onClick={() => void run(deleteConversation)}><Trash2 size={16} aria-hidden="true"/>确认删除</button>
      </div>
      <p className="footnote">按 Return 不触发删除。选择确认后显示实际清理进度。</p>
    </Modal>}
    {renaming && conversation && <Modal title="重命名会话" className="rename-dialog" onClose={() => setRenaming(false)}>
      <form onSubmit={event => {event.preventDefault(); void run(async () => {await update({conversation_id: conversation.id, title}); setRenaming(false);});}} style={{display: 'contents'}}>
        {error && <div className="danger-box" role="alert"><CircleAlert size={18} aria-hidden="true"/><div><strong>名称未保存</strong><p>{error}</p></div></div>}
        <div className="field"><label htmlFor="conversation-title">会话标题</label>
          <input id="conversation-title" aria-describedby="title-help" autoFocus data-autofocus value={title} maxLength={100} onChange={event => setTitle(event.target.value)}/>
          <small id="title-help">1–100 个 Unicode 码点。</small></div>
        <div className="dialog-actions">
          <button type="button" className="secondary" onClick={() => setRenaming(false)}>取消</button>
          <button className="primary" disabled={busy || ![...title.trim()].length || [...title.trim()].length > 100}>保存名称</button>
        </div>
      </form>
    </Modal>}
  </div>;
}

function roundLabel(items: MessageInfo[], message: MessageInfo) {
  const index = items.filter(item => item.role === 'user' && item.seq_no <= message.seq_no).length;
  return `第 ${Math.max(index, 1)} 轮`;
}

function UserMessage({message,items}: {message:MessageInfo;items:MessageInfo[]}) {
  return <div className="msg-user"><p>{message.content}</p>
    <span>{roundLabel(items,message)} · {clock(message.created_at)} · {message.attachments.length ? `已选择 ${message.attachments.map(item => item.file_name).join('、')}` : '未使用附件'}</span>
    {message.attachments.filter(attachment => attachment.available === false).map(attachment => <span key={attachment.attachment_id}>{attachment.file_name} · 来源不可用</span>)}
  </div>;
}

function MessageBody({text, className = 'body'}: {text: string; className?: string}) {
  return <>{text.split(/\n\s*\n/).map((block, index) => {
    const lines = block.trim().split('\n');
    const cells = (line: string) => line.trim().replace(/^\||\|$/g, '').split(/(?<!\\)\|/).map(cell => cell.trim().replace(/\\\|/g, '|'));
    const rows = lines.map(cells), header = rows[0], separator = rows[1];
    // shortcut: 只呈现单行单元格的管道表格，出现多行单元格时改用完整 Markdown 解析器。
    if (!block.startsWith('```') && header.length > 1 && separator?.length === header.length && separator.every(cell => /^:?-{3,}:?$/.test(cell)) && rows.slice(2).every(row => row.length === header.length)) {
      return <div className="answer-table" key={index}><table><thead><tr>{header.map((cell, column) => <th key={column} scope="col">{cell}</th>)}</tr></thead>
        <tbody>{rows.slice(2).map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, column) => <td key={column}>{cell}</td>)}</tr>)}</tbody></table></div>;
    }
    return <p className={className} key={index}>{block}</p>;
  })}</>;
}

function SourcePicker({sources, busy, conversation, onPick}: {sources: Source[]; busy: boolean; conversation: ConversationInfo; onPick: (id: string) => void}) {
  const [open, setOpen] = useState(!conversation.data_source_id);
  const trigger = useRef<HTMLButtonElement>(null), options = useRef<HTMLDivElement>(null);
  const disabled = busy || conversation.status !== 'active' || conversation.data_source_locked;
  function moveOption(direction: number) {
    const rows = [...options.current?.querySelectorAll<HTMLButtonElement>('button') || []];
    if (!rows.length) return;
    const current = rows.indexOf(document.activeElement as HTMLButtonElement);
    rows[current < 0 ? direction > 0 ? 0 : rows.length - 1 : (current + direction + rows.length) % rows.length]?.focus();
  }
  return <>
    <h2 className="empty-title">今天想弄清什么经营问题？</h2>
    <p className="empty-lead">先选择一个数据源。首次问题接受后，该会话会绑定此源。</p>
    <div className="field" style={{gap: 7}}>
      <label id="source-label" htmlFor="conversation-source">数据源</label>
      <button ref={trigger} id="conversation-source" className="source-trigger" aria-labelledby="source-label" aria-haspopup="listbox" aria-expanded={open}
        aria-controls="source-options" disabled={disabled} onClick={() => setOpen(value => !value)}
        onKeyDown={event => {if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {event.preventDefault(); setOpen(true); requestAnimationFrame(() => moveOption(event.key === 'ArrowDown' ? 1 : -1));}}}>
        {conversation.data_source_name || '请选择已启用的示例数据源'}{conversation.data_source_id && !sources.some(source => source.id === conversation.data_source_id) ? ` · ${sourceStatusLabel[conversation.data_source_status] || conversation.data_source_status}` : ''} ▾
      </button>
    </div>
    {open && <div ref={options} id="source-options" className="source-options" role="listbox" aria-label="可用数据源"
      onKeyDown={event => {
        if (event.key === 'Escape') {setOpen(false); trigger.current?.focus();}
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {event.preventDefault(); moveOption(event.key === 'ArrowDown' ? 1 : -1);}
      }}>
      {sources.map(source => <button key={source.id} role="option" aria-selected={conversation.data_source_id === source.id} disabled={disabled}
        onClick={() => {setOpen(false); trigger.current?.focus(); onPick(source.id);}}>
        <Database size={17} aria-hidden="true"/><span className="grow">{source.name}</span>{conversation.data_source_id === source.id && <Check size={17} aria-hidden="true"/>}
      </button>)}
      {!sources.length && <p className="footnote">暂无已启用的数据源</p>}
    </div>}
    <p className="footnote example-label">示例问题</p>
    <div className="example-list">
      <div className="example-card"><span>01</span><p>为什么第二周访问到下单的转化率下降？</p></div>
      <div className="example-card"><span>02</span><p>为什么本期销售收入下降，投放效率是否同步恶化？</p></div>
    </div>
    <InfoBlock title="每轮附件初始不勾选" body="仅使用附件分析时，也需要绑定启用的数据源。"/>
  </>;
}

function ToolHistory({message, items}: {message: MessageInfo; items: MessageInfo[]}) {
  const steps = items.filter(item => item.role === 'tool' && item.task_id === message.task_id);
  const question = items.find(item => item.role === 'user' && item.task_id === message.task_id);
  return steps[0]?.id === message.id ? <ToolProcess steps={steps} label={question ? roundLabel(items, question) : '较早轮次'}/> : null;
}

function ToolProcess({steps, label}: {steps: MessageInfo[]; label: string}) {
  if (!steps.length) return null;
  const failed = steps.filter(step => step.tool_status === 'failed').length;
  return <details className="task-complete tool-process">
    <summary><ChevronRight size={16} className="process-chevron" aria-hidden="true"/>
      <span className="grow">{label} · {steps.length} 次工具调用{failed ? ` · ${failed} 次失败` : ''}</span>
      <span className="process-show">查看过程</span><span className="process-hide">收起过程</span>
    </summary>
    <div className="tool-records">{steps.map(step => <div className="msg-tool" key={step.id}>
      {step.tool_status === 'success' ? <CircleCheck size={19} className="good" aria-hidden="true"/> : <CircleAlert size={19} aria-hidden="true"/>}
      <div className="grow"><strong>{step.tool_name || '工具调用'}</strong>
        <details><summary>{step.tool_status === 'success' ? '完成' : step.tool_status === 'failed' ? '失败' : step.tool_status === 'cancelled' ? '已取消' : '执行中'} · 查看记录</summary>
          <pre className="body">{step.content || '暂无记录内容。'}</pre>
        </details>
      </div>
    </div>)}</div>
  </details>;
}

function TaskCard({task, busy, onCancel}: {task: TaskSnapshot; busy: boolean; onCancel: () => void}) {
  const steps = task.messages.filter(message => message.role === 'tool');
  return <section className="task-card" aria-label="当前任务">
    <div className="task-head">
      <span className={`chip ${task.is_cancelling ? 'chip--accent' : task.task_status === 'queued' ? 'chip--warn' : task.task_status === 'failed' ? 'chip--danger' : 'chip--accent'}`}>
        {task.is_cancelling ? '正在取消' : statusLabel(task.task_status)}
      </span>
      <h2>{task.current_step || (task.task_status === 'queued' ? '等待调度' : '正在分析')}</h2>
      {(task.task_status === 'queued' || task.task_status === 'running') &&
        <button className="secondary" aria-label="取消任务" disabled={busy || task.is_cancelling} onClick={onCancel}><Square size={16} aria-hidden="true"/>{task.task_status === 'queued' ? '取消排队' : '取消分析'}</button>}
    </div>
    <p className="task-meta">{task.started_at ? `${clock(task.started_at)} 开始 · 当前步骤由服务端实时更新` : '任务已保存，等待调度'}</p>
    {steps.some(step => step.tool_status === 'running') && <div className="task-steps">
      {steps.filter(step => step.tool_status === 'running').map(step => <div className="task-step" key={step.id}>
        <LoaderCircle size={19} aria-hidden="true" className="accent animate-spin"/>
        <div><strong>{step.tool_name || '工具'}</strong><span>执行中 · 仅访问本轮授权的安全输入{step.content ? ` · ${step.content}` : ''}</span></div>
      </div>)}
    </div>}
    <ToolProcess steps={steps.filter(step => step.tool_status !== 'running')} label="已结束的工具步骤"/>
    {task.task_status === 'queued' && <p className="task-note">问题与任务已保存，等待调度。全局最多 3 个任务同时运行。</p>}
    {task.is_cancelling && <p className="task-note">取消请求已受理，等待执行停止。此时仍不可发送同会话新任务。</p>}
    {task.messages.some(message => message.role === 'assistant') && <><div className="divider"/>
      <p className="stream-note">{task.task_status === 'running' ? '正在生成回答 · 未完成' : statusLabel(task.task_status)}</p>
      {task.messages.filter(message => message.role === 'assistant').map(message => <p className="task-partial" key={message.id}>{message.content || '正在生成回答…'}</p>)}
    </>}
    {task.error_message && <div className="danger-box"><CircleAlert size={18} aria-hidden="true"/><div><strong>分析失败</strong><p>{task.error_message}</p></div></div>}
  </section>;
}

function Composer({conversation, draft, setDraft, taskBusy, busy, attachmentCount, taskKind, setTaskKind, onAttach, onSend, onRetry}: {
  conversation: ConversationInfo; draft: string; setDraft: (value: string) => void; taskBusy: boolean; busy: boolean;
  attachmentCount: number; taskKind: 'analysis' | 'final_report'; setTaskKind: (value: 'analysis' | 'final_report') => void;
  onAttach: () => void; onSend: () => void; onRetry: (() => void) | null;
}) {
  const archived = conversation.status === 'archived';
  const disabled = conversation.status !== 'active' || taskBusy || busy || !conversation.data_source_id || conversation.data_source_status !== 'enabled';
  const finalMode = taskKind === 'final_report';
  const tooLong = [...draft.trim()].length > 20000;
  return <section className="composer">
    <div className="composer-types">
      <span className="chip chip--accent">{finalMode ? '最终报告' : '普通分析'}</span>
      <button className="secondary" disabled={busy || taskBusy || archived || !conversation.latest_task_id} aria-pressed={finalMode}
        onClick={() => setTaskKind(finalMode ? 'analysis' : 'final_report')}>
        {finalMode ? <ArrowLeft size={16} aria-hidden="true"/> : <FileText size={16} aria-hidden="true"/>}{finalMode ? '返回普通分析' : '生成最终报告'}
      </button>
    </div>
    <div className="composer-box">
      <label htmlFor="question-input" className="sr-only">问题输入</label>
      <textarea id="question-input" value={draft} disabled={conversation.status !== 'active' || taskBusy || busy}
        placeholder={archived ? '已归档，暂不可输入' : taskBusy ? '任务执行中，结束后可继续追问…' : finalMode ? '请汇总本会话并生成最终报告' : '继续追问，或补充新的分析问题…'}
        onChange={event => setDraft(event.target.value)} rows={1}/>
      <div className="composer-foot">
        <span>{finalMode ? '不使用新附件 · 仅汇总已保存分析' : `附件 ${attachmentCount} 个 · 每轮需重新选择`}</span>
        <button className="primary" disabled={disabled || tooLong || (!finalMode && !draft.trim())} onClick={onSend}>
          <ArrowUp size={16} aria-hidden="true"/>{finalMode ? '生成最终报告' : '发送分析'}
        </button>
      </div>
    </div>
    <p className="composer-note">将向外部模型发送必要的会话、合成数据与证据片段。请勿使用真实敏感资料。</p>
    {onRetry && <button className="secondary" style={{alignSelf: 'flex-start'}} disabled={busy} onClick={onRetry}>用原请求 ID 重试提交</button>}
  </section>;
}

function ArchivedView({conversation, result, messages, busy, onRestore}: {
  conversation: ConversationInfo | undefined; result: ResultInfo | null; messages: MessageInfo[]; busy: boolean; onRestore: () => void;
}) {
  const [showHistory,setShowHistory] = useState(false);
  return <>
    <div className="conv-scroll archived-content">
      <div className="info" style={{alignItems: 'center', background: 'var(--sidebar)'}}>
        <Archive size={22} aria-hidden="true" style={{color: 'var(--secondary)'}}/>
        <div className="grow"><strong style={{color: 'var(--text)', fontSize: 19, fontWeight: 600}}>会话已归档</strong>
          <p>历史可查看、复制、导出和下载。恢复后可继续追问。</p></div>
        <button className="primary" disabled={busy} onClick={onRestore}><ArchiveRestore size={16} aria-hidden="true"/>恢复会话</button>
      </div>
      <h2 className="empty-title">{conversation?.title}</h2>
      <p className="empty-lead">分析与最终报告仍然保留，数据源仍绑定{conversation?.data_source_name || '原数据源'}。</p>
      {result && <div className="card" style={{gap: 16}}>
        <p className="footnote">{result.task_kind === 'final_report' ? '最终报告' : '最近分析'} · {shanghai(result.created_at)}</p>
        <p style={{fontSize: 20, fontWeight: 600, lineHeight: '30px',whiteSpace:'pre-wrap'}}>{result.conclusion_text || '暂无结论'}</p>
        <button className="secondary" style={{alignSelf: 'flex-start'}} aria-expanded={showHistory} onClick={() => setShowHistory(value => !value)}><History size={16} aria-hidden="true"/>{showHistory ? '收起历史过程' : '查看历史过程'}</button>
      </div>}
      {showHistory && <section className="archived-history" aria-label="归档历史过程">{messages.map(message => <div key={message.id} className={message.role === 'user' ? 'msg-user' : 'msg-answer'}>
        <span className="footnote">{message.role === 'user' ? '问题' : message.role === 'tool' ? message.tool_name || '工具调用' : '回答'} · {clock(message.created_at)} · {messageLabel(message)}</span>
        <MessageBody text={message.content}/>
      </div>)}</section>}
      <InfoBlock title="归档期间不能发送、上传或删除附件" body="恢复会话不会解锁数据源。需要更换数据源时，请新建会话。"/>
    </div>
    <section className="composer archived-composer">
      <p className="footnote">此会话只读，恢复后可继续分析。</p>
      <div className="field" style={{opacity: .4}}><span className="label">问题输入</span><input readOnly value="已归档，暂不可输入"/></div>
    </section>
  </>;
}

function availabilityText(available: boolean | null | undefined, reason: string | null | undefined) {
  return available === true ? '原件可用' : available !== false ? '状态未记录' : ({deleted:'原件已删除',attachment_deleted:'输入原件已删除',deleting:'原件正在删除',delete_failed:'原件删除失败',storage_missing:'原件缺失'}[reason || ''] || '原件不可用');
}

function positionText(value: unknown): string {
  if (Array.isArray(value)) return value.map(positionText).join('；');
  if (value && typeof value === 'object') {
    const labels: Record<string,string> = {record:'逻辑记录',line:'物理行',line_start:'物理起始行',line_end:'物理结束行',sheet:'工作表',row:'原始行',columns:'列',part:'分区',items:'记录'};
    return Object.entries(value).map(([key,item]) => `${labels[key] || key}：${positionText(item)}`).join('，');
  }
  return value === null || value === undefined ? '未记录' : String(value);
}

function EvidenceLocator({evidence,taskId}: {evidence: Evidence;taskId?:string}) {
  const locator = evidence.source_locator as Record<string, unknown>;
  if (evidence.source_type === 'database') return <>
    <p className="src">{`数据源：${String(locator.data_source_id)}\n数据库：${String(locator.database)}\nschema：${String(locator.schema)}\n过滤：${String(locator.filter_summary)}\n取数时间：${shanghai(String(locator.extracted_at))}`}</p>
    <details className="evidence-query"><summary>查看只读 SQL</summary><pre>{String(locator.query_text)}</pre></details>
  </>;
  if (evidence.source_type === 'file') return <p className="src">
    {`附件 ID：${String(locator.attachment_id)}\n文件名：${String(locator.file_name)}\n位置：${positionText(locator.position)}\n来源任务：${String(locator.source_task_id || taskId || '未记录')}`}
  </p>;
  return <><p className="src">
    {`任务：${String(locator.task_id)}\n调用：${String(locator.tool_call_id)}\n产物：${String(locator.output_id)} · ${String(locator.output_kind)}\n相对路径：${String(locator.relative_path)}\n位置：${positionText(locator.position)}\n生成时间：${shanghai(String(locator.generated_at))}`}
  </p>{Array.isArray(locator.input_refs) && <div className="input-sources"><h3>输入原件当前状态</h3>{locator.input_refs.map((input,index) => {
    if (!input || typeof input !== 'object') return null;
    const ref = input as Record<string,unknown>;
    return <p className="src" key={index}>{String(ref.file_name || ref.attachment_id || ref.evidence_id || ref.output_id || '输入')} · {availabilityText(typeof ref.available === 'boolean' ? ref.available : undefined,typeof ref.unavailable_reason === 'string' ? ref.unavailable_reason : undefined)}</p>;
  })}</div>}</>;
}

function ResultPanel({result, task, busy, onCopy, onExport, onEvidence, taskKindLabel, rounds, onSelectTask}: {
  result: ResultInfo | null; task: TaskSnapshot | null; busy: boolean;
  onCopy: (text: string) => Promise<void>; onExport: (format: 'markdown' | 'html') => void;
  onEvidence: (item: Evidence) => void; taskKindLabel: string;
  rounds: Array<{id: string; label: string}>; onSelectTask: (id: string) => void;
}) {
  if (!result) {
    if (task && task.task_status !== 'success') {
      return <aside className="result-panel" aria-label="分析结果">
        <div className="result-empty" style={{paddingTop: 36}}>
          {task.task_status === 'failed' ? <CircleAlert size={40} aria-hidden="true" style={{color: 'var(--danger)'}}/> : <LoaderCircle size={40} aria-hidden="true" className={task.task_status === 'running' ? 'accent animate-spin' : ''}/>}
          <h2>{statusLabel(task.task_status)}</h2>
          <p>{task.error_message || task.current_step || '完成后这里将展示问题、指标、证据、归因结论、数据缺口和下一步建议。'}</p>
        </div>
      </aside>;
    }
    return <aside className="result-panel" aria-label="分析结果">
      <div className="result-empty">
        <FileSearch size={40} aria-hidden="true"/>
        <h2>暂无分析结果</h2>
        <p>发送问题后，这里将按固定结构呈现本轮分析，并保留可回溯证据。</p>
        <ol className="result-outline" aria-label="结果结构">
          {['问题定义', '关键指标', '证据列表', '归因结论', '待补充数据', '下一步建议'].map((label, index) =>
            <li key={label}><span>0{index + 1}</span>{label}</li>)}
        </ol>
      </div>
    </aside>;
  }
  return <aside className="result-panel" aria-label="分析结果">
    <div className="result-head"><h2 className="grow">{result.task_kind === 'final_report' ? '最终报告' : '分析结果'}</h2><span className="chip chip--good">{result.evidence_list.length ? '已保存' : '证据不足 · 已保存'}</span></div>
    <div className="round-select"><select aria-label="查看分析轮次" value={result.task_id} onChange={event => onSelectTask(event.target.value)}>
      {rounds.map(round => <option key={round.id} value={round.id}>{round.label}</option>)}
      {!rounds.some(round => round.id === result.task_id) && <option value={result.task_id}>{result.task_kind === 'final_report' ? '最终报告 · 固定范围' : '当前分析结果'}</option>}
    </select><ChevronDown size={15} aria-hidden="true" style={{color: 'var(--secondary)'}}/></div>
    <p className="result-generated">生成于 {shanghai(result.created_at)} · Asia/Shanghai</p>
    <section className="stack" style={{gap: 15}}>
      <h3 className="sec-title">01 问题定义</h3>
      <p className="sec-body">{result.problem_definition || '暂无'}</p>
    </section>
    <section className="stack" style={{gap: 15}}>
      <h3 className="sec-title">02 关键指标</h3>
      {result.key_metrics.length ? <dl className="metric-grid">{result.key_metrics.map((metric, index) =>
        <div className="metric" key={index}>
          <dt>{metric.metric_name}</dt>
          <dd>{typeof metric.metric_value === 'number' ? metric.metric_value.toLocaleString('en-US', {maximumFractionDigits:20}).replace('-', '−') : metric.metric_value ?? '未知'}{metric.metric_unit ? `${metric.metric_unit === '%' ? '' : ' '}${metric.metric_unit}` : ''}</dd>
          <small>{metric.metric_period || metric.calculation_note || ''}</small>
        </div>)}</dl> : <p className="sec-body">暂无可计算指标</p>}
    </section>
    <section className="stack" style={{gap: 15}}>
      <h3 className="sec-title">03 证据列表</h3>
      {result.evidence_list.length ? result.evidence_list.map(item => <button className="ev-row" key={item.evidence_id} onClick={() => onEvidence(item)}>
        <span className="chip chip--neutral">{item.evidence_id}</span>
        <span className="ev-body"><strong>{item.evidence_text.length > 40 ? item.evidence_text.slice(0, 40) + '…' : item.evidence_text}</strong>
          <span>{item.source_type === 'database' ? '数据库' : item.source_type === 'file' ? '附件' : '工具'} · {item.related_metric} · 支撑强度：{confidenceLabel[item.confidence]}</span></span>
        <ChevronRight size={16} aria-hidden="true" style={{color: 'var(--secondary)'}}/>
      </button>) : <p className="sec-body">暂无足够证据</p>}
    </section>
    <section className="stack" style={{gap: 15}}>
      <h3 className="sec-title">04 归因结论</h3>
      <p className="sec-body">{result.conclusion_text || '证据不足，暂不下结论。'}</p>
    </section>
    <section className="stack" style={{gap: 15}}>
      <h3 className="sec-title">05 待补充数据</h3>
      <p className="sec-body">{result.missing_data_text || '暂无'}</p>
    </section>
    <section className="stack" style={{gap: 15}}>
      <h3 className="sec-title">06 下一步建议</h3>
      <p className="sec-body">{result.next_action_text || '暂无'}</p>
    </section>
    <div className="result-spacer"/>
    <div className="divider"/>
    <div className="result-actions">
      <button className="secondary" disabled={busy} onClick={() => void onCopy(result.result_markdown)}><Copy size={16} aria-hidden="true"/>复制</button>
      {(['markdown', 'html'] as const).map(format => {
        const state = result.export_status[format], label = format === 'markdown' ? 'Markdown' : 'HTML';
        return state.status === 'success'
          ? <a key={format} className="secondary" href={`/api/results/${result.task_id}/download?format=${format}`}><Download size={16} aria-hidden="true"/>下载 {label}</a>
          : <button key={format} className="secondary" disabled={busy || state.status === 'generating'} onClick={() => onExport(format)}>
              {state.status === 'failed' && <RotateCcw size={16} aria-hidden="true"/>}{state.status === 'failed' ? '重试' : state.status === 'generating' ? '正在生成' : '导出'} {label}</button>;
      })}
    </div>
    <p className="result-note">{Object.values(result.export_status).every(state => state.status === 'not_generated') ? '按需生成文件 · 两种格式均尚未生成' : (['markdown','html'] as const).map(format => `${format === 'markdown' ? 'Markdown' : 'HTML'} ${exportLabel(result.export_status[format].status)}${result.export_status[format].status === 'failed' ? '，可单独重试' : ''}`).join(' · ')}<span className="sr-only">{taskKindLabel} · 来源状态截至结果生成时；当前状态以系统为准，离线文件不会自动更新。</span></p>
  </aside>;
}

function ReportScope({scope}: {scope:Record<string,unknown> | null}) {
  return <details className="report-scope" aria-label="固定汇总范围"><summary>{scope && Array.isArray(scope.source_task_ids) && Array.isArray(scope.incomplete_task_ids)
    ? `固定汇总 ${scope.source_task_ids.length} 轮成功分析 · 未完成事项 ${scope.incomplete_task_ids.length} 项`
    : '固定汇总范围以保存记录为准'}</summary><pre>{JSON.stringify(scope,null,2)}</pre></details>;
}

function ReportFiles({result, busy, onExport}: {result: ResultInfo; busy: boolean; onExport: (format: 'markdown' | 'html') => void}) {
  return <>
    <div className="export-block">
      <h3>报告文件</h3>
      {(['markdown', 'html'] as const).map(format => {
        const state = result.export_status[format], label = format === 'markdown' ? 'Markdown' : 'HTML';
        return <div className="export-row" key={format}>
          <div className="grow"><strong>{label}</strong>
            <span className={`chip chip--${state.status === 'success' ? 'good' : state.status === 'failed' ? 'danger' : 'neutral'}`}>{exportLabel(state.status)}{state.error_message && ` · ${state.error_message}`}</span></div>
          {state.status === 'success'
            ? <a className="secondary" href={`/api/results/${result.task_id}/download?format=${format}`}><Download size={16} aria-hidden="true"/>下载 {label}</a>
            : <button className="secondary" disabled={busy || state.status === 'generating'} onClick={() => onExport(format)}>
                {state.status === 'failed' ? <RotateCcw size={16} aria-hidden="true"/> : <Download size={16} aria-hidden="true"/>}{state.status === 'failed' ? '重试导出' : '生成'} {label}</button>}
        </div>;
      })}
      <p className="footnote">文件失败不影响分析成功，也不影响另一种格式。</p>
    </div>
  </>;
}
