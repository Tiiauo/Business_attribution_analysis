import {useEffect, useRef, useState} from 'react';
import {CircleAlert, Download, Info, RotateCcw, Trash2, Upload} from 'lucide-react';
import type {AttachmentInfo, AttachmentPage, UserInfo} from './contracts';
import {request} from './task-session';
import {Modal} from './admin';

const size = (bytes: number) => bytes >= 1_000_000 ? `${(bytes / 1_000_000).toLocaleString('en-US',{maximumFractionDigits:1})} MB` : `${(bytes / 1000).toFixed(1)} KB`;
const time = (value: string) => new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false}).format(new Date(value));

export function Attachments({user, conversationId, readOnly, taskBusy, selected, onSelect, onChange}: {
  user: UserInfo; conversationId: string; readOnly: boolean; taskBusy: boolean;
  selected: string[]; onSelect: (ids: string[]) => void; onChange: () => void;
}) {
  const [items, setItems] = useState<AttachmentInfo[]>([]), [total, setTotal] = useState(0), [error, setError] = useState('');
  const [file, setFile] = useState<File | null>(null), [hasHeader, setHasHeader] = useState(true), [progress, setProgress] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<AttachmentInfo | null>(null), [busy, setBusy] = useState(false), [revision, setRevision] = useState(0);
  const generation = useRef(0), input = useRef<HTMLInputElement>(null), selection = useRef(selected), change = useRef(onSelect);
  selection.current = selected; change.current = onSelect;
  const blocked = readOnly || taskBusy || items.some(item => item.delete_status !== 'none');
  useEffect(() => {
    const epoch = ++generation.current;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setItems([]); setFile(null); setProgress(null); setError('');
    async function refresh() {
      if (epoch !== generation.current) return;
      let delay: number | null = null;
      try {
        let cursor: string | null = null, totalBytes = 0;
        const rows: AttachmentInfo[] = [];
        do {
          const page: AttachmentPage = await request<AttachmentPage>(user, `/api/attachment/ls?conversation_id=${conversationId}&page_size=100${cursor ? '&cursor=' + encodeURIComponent(cursor) : ''}`);
          if (epoch !== generation.current) return;
          rows.push(...page.items); cursor = page.next_cursor; totalBytes = page.total_bytes;
        } while (cursor);
        setItems(rows); setTotal(totalBytes); setError('');
        const ids = selection.current.filter(id => rows.some(item => item.id === id && item.parse_status === 'success' && item.delete_status === 'none'));
        if (ids.length !== selection.current.length) change.current(ids);
        if (rows.some(item => item.delete_status === 'deleting' || (item.delete_status === 'none' && ['pending', 'parsing'].includes(item.parse_status)))) delay = 2000;
      } catch (reason) {if (epoch === generation.current) setError((reason as Error).message); delay = 2000;}
      finally {if (epoch === generation.current && delay !== null) timer = setTimeout(() => {if (document.visibilityState === 'visible') void refresh();}, delay);}
    }
    const visible = () => {if (document.visibilityState === 'visible') {clearTimeout(timer); void refresh();}};
    void refresh(); document.addEventListener('visibilitychange', visible);
    return () => {generation.current++; clearTimeout(timer); document.removeEventListener('visibilitychange', visible);};
  }, [conversationId, revision]);

  async function upload() {
    if (!file) return;
    const epoch = generation.current;
    if (file.size > 20000000) {setError('单文件最多 20 MB。'); return;}
    const data = new FormData(); data.append('conversation_id', conversationId); data.append('file', file);
    if (/\.(csv|xlsx)$/i.test(file.name)) data.append('has_header', String(hasHeader));
    setProgress(0); setError('');
    try {
      await new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest(); xhr.open('POST', '/api/attachment/upload'); xhr.timeout = 60000;
        xhr.setRequestHeader('X-CSRF-Token', user.csrf_token);
        xhr.upload.onprogress = event => {if (epoch === generation.current && event.lengthComputable) setProgress(Math.round(event.loaded / event.total * 100));};
        xhr.onload = () => {
          let value; try {value = JSON.parse(xhr.responseText);} catch {reject(new Error('上传响应无效，请刷新附件列表核对。')); return;}
          if (xhr.status >= 200 && xhr.status < 300) resolve(); else reject(new Error(`${value.message}（${value.code}）`));
        };
        xhr.onerror = xhr.ontimeout = () => reject(new Error('上传未确认，请刷新列表核对后重试。'));
        xhr.send(data);
      });
      if (epoch === generation.current) {setFile(null); if (input.current) input.current.value = ''; setRevision(v => v + 1); onChange();}
    } catch (reason) {if (epoch === generation.current) setError((reason as Error).message);}
    finally {if (epoch === generation.current) setProgress(null);}
  }
  async function remove(item: AttachmentInfo) {
    const epoch = generation.current; setBusy(true); setError('');
    try {await request(user, '/api/attachment/delete', {attachment_id: item.id}); if (epoch === generation.current) {setDeleting(null); setRevision(v => v + 1); onChange();}}
    catch (reason) {if (epoch === generation.current) setError((reason as Error).message);}
    finally {setBusy(false);}
  }
  const statusChip = (item: AttachmentInfo) => {
    if (item.delete_status === 'deleting') return <span className="chip chip--accent">正在删除 · 暂不可用</span>;
    if (item.delete_status === 'failed') return <span className="chip chip--danger">删除失败 · 暂不可用</span>;
    if (item.parse_status === 'success') return <span className="chip chip--good">解析成功</span>;
    if (item.parse_status === 'failed') return <span className="chip chip--danger">解析失败 · 暂不可选</span>;
    if (item.parse_status === 'parsing') return <span className="chip chip--accent">解析中 · 暂不可选</span>;
    return <span className="chip chip--accent">等待解析 · 暂不可选</span>;
  };
  return <div className="attachments-panel" style={{display: 'flex', flexDirection: 'column', gap: 16, flex: 1, minHeight: 0}}>
    <p className="footnote">已保存 {size(total)} / 200 MB · 单文件 ≤ 20 MB</p>
    <div className="att-upload">
      <input ref={input} id="attachment-file" className="sr-only" type="file" accept=".csv,.xlsx,.txt,.md,.markdown"
        disabled={blocked || progress !== null} onChange={event => setFile(event.target.files?.[0] || null)}/>
      <label htmlFor="attachment-file" className="primary" style={{alignSelf: 'flex-start', cursor: 'pointer'}} aria-disabled={blocked || progress !== null}>
        <Upload size={16} aria-hidden="true"/>选择文件上传
      </label>
      <p className="footnote">CSV / XLSX / TXT / Markdown · UTF-8</p>
      <label className="check"><input type="checkbox" checked={hasHeader} disabled={blocked || progress !== null || Boolean(file && !/\.(csv|xlsx)$/i.test(file.name))}
        onChange={event => setHasHeader(event.target.checked)}/>首个非空行是表头</label>
      <p className="footnote">XLSX 仅静态值，不接受公式或外部链接。</p>
      {file && <div className="actions actions--start">
        <span className="footnote">{file.name} · {size(file.size)}</span>
        <button className="primary" disabled={blocked || progress !== null} onClick={() => void upload()}><Upload size={16} aria-hidden="true"/>上传文件</button>
        <button className="secondary" disabled={progress !== null} onClick={() => {setFile(null); if (input.current) input.current.value = '';}}>取消</button>
      </div>}
      {progress !== null && <div role="status"><label htmlFor="upload-progress" className="footnote">{progress === 100 ? '上传 100%，正在登记' : '上传中'}</label><progress id="upload-progress" max={100} value={progress}/></div>}
    </div>
    {error && !deleting && <div className="danger-box" role="alert"><CircleAlert size={18} aria-hidden="true"/><div><strong>附件操作未完成</strong><p>{error}</p></div></div>}
    {(readOnly || taskBusy) && <InfoBlock title={readOnly ? '归档会话只读' : '任务尚未结束'} body={readOnly ? '恢复后可管理附件。' : '暂不可新增或删除附件。'}/>}
    {items.some(item => item.delete_status !== 'none') && <InfoBlock title="附件清理尚未完成" body="仍计入会话配额，暂不能发送、上传或归档；失败项可单独重试删除。"/>}
    {!items.length && <p className="empty-note">暂无会话附件</p>}
    {items.map(item => <article className={`att-item ${item.parse_status === 'success' ? '' : 'unavailable'}`} key={item.id}>
      <div className="att-name">
        {item.parse_status === 'success' && <input type="checkbox" aria-label={`本轮使用 ${item.file_name}`}
          disabled={blocked || item.parse_status !== 'success' || item.delete_status !== 'none'}
          checked={selected.includes(item.id)}
          onChange={event => onSelect(event.target.checked ? [...selected, item.id] : selected.filter(id => id !== item.id))}/>}
        <strong className="grow">{item.file_name}</strong>
        {item.parse_status === 'success' && statusChip(item)}
      </div>
      {item.parse_status !== 'success' && <div style={{display: 'flex'}}>{statusChip(item)}</div>}
      {item.parse_status !== 'failed' && <p className="att-meta">{item.file_type.toUpperCase()} · {size(item.file_size)} · {time(item.created_at)} 上传</p>}
      {item.parse_summary?.map((table, index) => <p className="att-meta" key={index}>
        {`表头：${(table.columns as string[] | undefined)?.join(' / ') ?? '—'}\n${String(table.row_count)} 条数据 · 跳过 ${String(table.skipped_empty_rows ?? 0)} 个空行${item.parse_options?.has_header === false ? ' · 未启用表头' : ' · 已启用表头'}`}
      </p>)}
      {item.parse_status === 'success' && item.parse_summary === null && <p className="att-meta">解析摘要不可用</p>}
      {item.parse_error && <p className="att-meta" style={{color: 'var(--danger)'}}>{item.parse_error}</p>}
      {item.delete_error && <p className="att-meta" style={{color: 'var(--danger)'}}>{item.delete_error}</p>}
      {(item.parse_status === 'success' || item.parse_status === 'failed' || item.delete_status !== 'none') && <div className="att-actions">
        {item.download_url && <a className="secondary" href={item.download_url}><Download size={16} aria-hidden="true"/>下载</a>}
        <button className="secondary" disabled={readOnly || taskBusy || busy || item.delete_status === 'deleting' || (blocked && !item.can_retry)}
          onClick={() => setDeleting(item)}>{item.can_retry ? <RotateIcon/> : item.parse_status !== 'failed' && <Trash2 size={16} aria-hidden="true"/>}{item.can_retry ? '重试删除' : item.parse_status === 'failed' ? '删除后重新上传' : '删除'}</button>
      </div>}
    </article>)}
    <div className="att-spacer"/>
    <InfoBlock title={`本轮已选择 ${selected.length} 个附件`} body="勾选解析成功的文件后发送普通分析。"/>
    {deleting && <Modal title="删除附件" onClose={() => setDeleting(null)} icon={<Trash2 size={30} aria-hidden="true" style={{color: 'var(--danger)'}}/>}>
      <p>将删除“{deleting.file_name}”的原件、解析缓存、任务输入及等价副本。历史结果中的指标和必要证据摘要会保留。</p>
      {error && <div className="danger-box" role="alert"><CircleAlert size={18} aria-hidden="true"/><div><strong>删除未提交</strong><p>{error}</p></div></div>}
      <div className="dialog-actions">
        <button className="secondary" autoFocus data-autofocus onClick={() => setDeleting(null)}>保留附件</button>
        <button className="btn--danger" disabled={busy} onClick={() => void remove(deleting)}><Trash2 size={16} aria-hidden="true"/>确认删除附件</button>
      </div>
    </Modal>}
  </div>;
}

function RotateIcon() {
  return <RotateCcw size={16} aria-hidden="true"/>;
}

function InfoBlock({title, body}: {title: string; body: string}) {
  return <div className="info"><Info size={18} aria-hidden="true"/><div><strong>{title}</strong><p>{body}</p></div></div>;
}
