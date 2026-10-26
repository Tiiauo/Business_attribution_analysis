import {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ChartNoAxesCombined, ShieldCheck, ArrowUpRight, CircleCheck, CircleAlert, LoaderCircle, Info, MessageSquareText, FileSearch, FileCheck2, Lock} from 'lucide-react';
import './styles.css';
import type {UserInfo} from './contracts';
import {Admin} from './admin';
import {ConversationWorkbench} from './workbench';

type User = UserInfo;
type Failure = {message: string; request_id?: string};

async function getUser(): Promise<User> {
  const response = await fetch('/auth/me', {credentials: 'same-origin', cache: 'no-store'});
  if (!response.ok) {
    const failure: Failure = await response.json();
    throw new Error(failure.message || '登录状态校验失败，请重新登录。');
  }
  return response.json();
}

function Login() {
  const [signedIn, setSignedIn] = useState(false);
  useEffect(() => { getUser().then(() => setSignedIn(true)).catch(() => {}); }, []);
  return <div className="login-page">
    <aside className="login-hero">
      <div className="hero-brand">
        <span className="brand-mark"><ChartNoAxesCombined size={22} aria-hidden="true"/></span>
        <div>经营归因分析系统<small>Business Attribution Suite</small></div>
      </div>
      <div className="hero-copy">
        <span className="eyebrow">Evidence-first analytics</span>
        <h1 aria-label="从问题出发，以证据归因。"><span>从问题出发，</span><span>以证据归因。</span></h1>
        <p className="lead">围绕经营问题持续追问、补充资料，把每一次判断都落回可追溯的证据，最终形成可交付的分析报告。</p>
        <ul className="hero-points">
          <li>
            <span className="point-icon"><MessageSquareText size={18} aria-hidden="true"/></span>
            <div><strong>多轮追问，逐层拆解</strong><p>在同一会话中比较口径、按维度下钻，保留每一次判断的上下文。</p></div>
          </li>
          <li>
            <span className="point-icon"><FileSearch size={18} aria-hidden="true"/></span>
            <div><strong>每条结论，都能回到证据</strong><p>数据库查询、附件摘录与工具产物统一编号，并记录来源可用状态。</p></div>
          </li>
          <li>
            <span className="point-icon"><FileCheck2 size={18} aria-hidden="true"/></span>
            <div><strong>过程与报告，一并交付</strong><p>问题、指标、缺口与建议结构化呈现，支持 Markdown 与 HTML 导出。</p></div>
          </li>
        </ul>
      </div>
      <div className="hero-visual" aria-hidden="true">
        <svg className="hv-line" viewBox="0 0 560 220" preserveAspectRatio="none" fill="none">
          <defs>
            <linearGradient id="hero-line" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0" stopColor="#9fd6ff" stopOpacity=".05"/>
              <stop offset=".55" stopColor="#9fd6ff" stopOpacity=".55"/>
              <stop offset="1" stopColor="#8fe6cf" stopOpacity=".9"/>
            </linearGradient>
          </defs>
          <path d="M18 176C120 150 172 186 254 142S404 78 548 46" stroke="url(#hero-line)" strokeWidth="1.4" strokeDasharray="4 6"/>
          <path d="M18 196C132 178 190 200 278 166S430 116 548 96" stroke="#7a5cff" strokeOpacity=".28" strokeWidth="1.2"/>
        </svg>
        <div className="hv-card hv-card--metric"><span>访问下单转化率</span><strong>14%</strong><em>↓ 6.0 pt · 较基期</em></div>
        <div className="hv-card hv-card--evidence"><i>E02</i><div><strong>Windows 下单用户</strong><span>120 → 60 人 · 高置信</span></div></div>
        <span className="hv-node hv-node--a"/><span className="hv-node hv-node--b"/>
      </div>
      <div className="hero-foot"><span className="badge">内部试点 · 合成数据</span><span>Desktop · 分析工作台</span></div>
    </aside>
    <main className="login-panel">
      <p className="login-legal">© 2026 经营归因分析系统 · 内部试点 · 合成数据</p>
      <section className="login-card" aria-labelledby="login-title">
        <div className="login-icon"><ShieldCheck size={28} aria-hidden="true"/></div>
        <div className="stack" style={{gap: 7}}>
          <span className="eyebrow" style={{color: 'var(--accent)'}}>Secure access</span>
          <h2 id="login-title">登录以继续</h2>
          <p className="login-description">通过组织认证中心完成授权，进入你的分析工作台。</p>
        </div>
        <a className="primary login-button btn--block" href={signedIn ? '/workbench' : '/auth/login'}><ArrowUpRight size={16} aria-hidden="true"/>{signedIn ? '进入工作台' : '通过认证中心登录'}</a>
        <div className="info"><Info size={18} aria-hidden="true" className="accent"/><div><strong>将跳转至演示认证服务</strong><p>使用分析用户或管理员测试身份完成授权。</p></div></div>
        <div className="divider"/>
        <p className="login-footer">登录后仅可访问本人的会话与文件。<br/>管理员同样不具备默认跨用户业务内容权限。</p>
        <p className="login-trust"><Lock size={12} aria-hidden="true"/>会话与文件按用户隔离 · 凭证不落前端</p>
      </section>
    </main>
  </div>;
}

const authFailures: Record<string, string> = {
  AUTH_PROVIDER_UNAVAILABLE: '认证服务暂不可用，请稍后重新登录。',
  ACCOUNT_DISABLED: '账户已停用，请联系管理员。', AUTH_ROLE_UNMAPPED: '当前身份尚未配置访问角色，请联系管理员。',
  AUTH_PROVIDER_INVALID: '认证响应未通过校验，请重新发起登录。', AUTH_CALLBACK_INVALID: '此授权已使用或过期，请重新发起登录。',
};

function CallbackHeader() {
  return <header className="toolbar">
    <div className="brand"><span className="brand-mark"><ChartNoAxesCombined size={22} aria-hidden="true"/></span><span className="brand-text"><strong>经营归因分析系统</strong><small>Business Attribution Suite</small></span></div>
  </header>;
}

function Complete() {
  const code = new URLSearchParams(location.search).get('error');
  const [status, setStatus] = useState<'pending' | 'success' | 'failure'>(code ? 'failure' : 'pending');
  const [message, setMessage] = useState(code ? (authFailures[code] || '授权未完成，请重新登录。') : '');
  useEffect(() => {
    if (code) { history.replaceState(null, '', '/auth/complete'); return; }
    let active = true;
    getUser().then(() => {if (active) setStatus('success');}).catch((error: Error) => {
      if (active) {setMessage(error.message); setStatus('failure');}
    });
    return () => { active = false; };
  }, [code]);
  useEffect(() => {
    if (status !== 'success') return;
    const timer = setTimeout(() => location.replace('/workbench'), 1800);
    return () => clearTimeout(timer);
  }, [status]);
  return <><CallbackHeader/><main className="callback-main"><section className="callback-card" aria-live="polite" aria-busy={status === 'pending'}>
    {status === 'pending' ? <LoaderCircle className="accent animate-spin" size={32} aria-hidden="true"/> :
      status === 'success' ? <CircleCheck className="good" size={32} aria-hidden="true"/> : <CircleAlert className="danger-text" size={32} aria-hidden="true"/>}
    <h1>{status === 'pending' ? '正在验证授权' : status === 'success' ? '授权成功' : '授权已失效'}</h1>
    <p>{status === 'pending' ? <>正在验证回调参数与请求关联，<br/>请稍候。</> : status === 'success' ? <>登录态已建立，<br/>正在进入工作台。</> : message}</p>
    {status === 'success' && <a className="primary" href="/workbench">进入工作台</a>}
    {status === 'failure' && <a className="secondary" href="/auth/login">重新登录</a>}
    <p className="footnote">{status === 'failure' ? '不展示令牌与授权敏感参数' : '认证状态与用户身份由服务端确认'}</p>
  </section></main></>;
}

function Workbench({admin = false}: {admin?: boolean}) {
  const [user, setUser] = useState<User | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {getUser().then(setUser).catch((reason: Error) => setError(reason.message));}, []);
  async function logout() {
    if (!user) return;
    const response = await fetch('/auth/logout', {method: 'POST', headers: {'X-CSRF-Token': user.csrf_token}});
    if (response.ok) location.replace('/login');
    else throw new Error('退出未完成，请刷新后重试。');
  }
  if (admin && user) return <Admin user={user}/>;
  if (user) return <ConversationWorkbench user={user} onLogout={logout}/>;
  return <><CallbackHeader/><main className="callback-main"><section className="callback-card">
    <LoaderCircle className="accent animate-spin" size={32} aria-hidden="true"/>
    <h1>正在验证登录状态</h1>
    {error ? <div role="alert"><p>{error}</p><a className="secondary" href="/auth/login">重新登录</a></div>
      : <p>认证状态与用户身份由服务端确认</p>}
  </section></main></>;
}

const path = location.pathname;
createRoot(document.getElementById('root')!).render(path === '/auth/complete' ? <Complete/> : path === '/admin' ? <Workbench admin/> : path === '/workbench' ? <Workbench/> : <Login/>);
