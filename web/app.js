/* =========================================================
   落笔 · 前端逻辑  v1.4
   状态只有一个 source of truth：S.profile（后端同构的结构）
   所有 AI 动作都走 /api/*，拿到结果后立即重渲染 —— 不做"假对话"
   ========================================================= */
const S = {
  sid: localStorage.getItem('jh_sid') || ('s_' + Math.random().toString(36).slice(2, 12)),
  profile: null,
  jd: '',
  sampleJd: '',
  lint: null,
  match: null,
  qs: null,
  apps: [],
  llm: null,
  user: null,
  credits: 0,
  pricing: null,
  anonymous: true,
  sideTab: 'diag',
  rewritten: 0,
  exported: false,
  busy: false,
};
localStorage.setItem('jh_sid', S.sid);

/* 简历配色：用纸墨色谱，不用 SaaS 蓝。
   旧档案里仍存着 theme:'blue'，所以 blue 键必须留着并映射到墨色——
   删掉这个键，老用户打开会拿到 undefined，简历标题会变成默认黑。 */
const THEMES = [
  { key: 'ink', hex: '#1c1917' },
  { key: 'wine', hex: '#8d2716' },
  { key: 'green', hex: '#2f6b4f' },
  { key: 'sepia', hex: '#7a5230' },
];
const THEME_MAP = {
  ink: '#1c1917', blue: '#1c1917', wine: '#8d2716',
  green: '#2f6b4f', sepia: '#7a5230',
};
const STAGES = ['建档', '定向', '改写', '排版', '导出'];

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function api(path, body, method) {
  const opt = {
    method: method || (body ? 'POST' : 'GET'),
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-Session': S.sid },
  };
  if (body) opt.body = JSON.stringify(body);
  const res = await fetch(path, opt);
  const data = await res.json().catch(() => ({ ok: false, error: '服务端返回异常' }));
  if (!res.ok || data.ok === false) {
    // 带上 code，前端才知道该弹登录还是弹付费，而不是一律报错
    const err = new Error(data.error || ('请求失败 ' + res.status));
    err.code = data.code;
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

/* ---------------------------------------------------------------- 埋点
   漏斗靠这个算。用缓冲合并请求，别让埋点把主流程拖慢。 */
let _evtBuf = [];
let _evtTimer = null;
function track(name, payload) {
  _evtBuf.push({ name, payload: payload || {} });
  clearTimeout(_evtTimer);
  _evtTimer = setTimeout(flushTrack, 800);
}
function flushTrack() {
  if (!_evtBuf.length) return;
  const batch = _evtBuf;
  _evtBuf = [];
  try {
    fetch('/api/events', {
      method: 'POST',
      credentials: 'include',
      keepalive: true,
      headers: { 'Content-Type': 'application/json', 'X-Session': S.sid },
      body: JSON.stringify({ events: batch }),
    });
  } catch (e) { /* 埋点失败不影响使用 */ }
}
window.addEventListener('visibilitychange', () => { if (document.hidden) flushTrack(); });


function toast(msg, ms = 2200) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._t);
  t._t = setTimeout(() => t.classList.remove('show'), ms);
}

/* ---------------------------------------------------------------- 启动 */
async function boot() {
  try {
    const d = await api('/api/boot');
    S.profile = d.profile;
    S.sampleJd = d.sampleJd || '';
    S.llm = d.llm;
    S.user = d.user || null;
    S.credits = d.credits || 0;
    S.wallet = d.wallet || null;
    S.families = d.families || [];
    S.pitfallStats = d.pitfallStats || null;
    S.variants = d.variants || [];
    S.pricing = d.pricing || null;
    S.llmInfo = { status: d.llm, mine: d.llmMine, presets: d.llmPresets };
    S.anonymous = d.anonymous !== false;
    paintEngine(d.llm);
    renderAcct();
    renderAll();
    welcome(d.isSample);
    loadApps();
    // 版本条：一岗一版（独立文件，通过钩子接入，不让这个文件继续膨胀）
    if (window.JHVer) JHVer.boot();
    const h = (location.hash || '').replace('#', '');
    if (['track', 'drill', 'industry', 'pitfall', 'contests'].indexOf(h) >= 0) switchView(h);
  } catch (e) {
    document.body.insertAdjacentHTML('beforeend',
      `<div style="position:fixed;inset:0;background:#fff;z-index:99;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:10px;font-size:14px">
        <strong>连不上本地服务</strong>
        <span style="color:#8b8479;font-size:13px">请先在项目目录运行：python server.py，然后打开 http://127.0.0.1:8848</span>
        <code style="background:#f5f6f7;padding:6px 10px;border-radius:6px;font-size:12px">${esc(e.message)}</code>
      </div>`);
  }
}

/* ---------------------------------------------------------------- 账户态 */
function renderAcct() {
  const t = $('#acctText');
  const b = $('#btnAcct');
  if (!t || !b) return;
  if (S.anonymous) {
    t.textContent = '登录 / 注册';
    b.classList.remove('has-credit', 'no-credit');
    b.title = '注册送 3 次导出，档案跟账号走';
    return;
  }
  const w = S.wallet || {};
  const pts = w.points || 0;
  if (w.member) {
    t.textContent = `会员 · ${pts} 点`;
    b.classList.add('has-credit');
    b.classList.remove('no-credit');
    b.title = `会员不限次导出 · 剩余 ${pts} 点 AI 额度`;
  } else if (pts > 0) {
    t.textContent = `${pts} 点`;
    b.classList.add('has-credit');
    b.classList.remove('no-credit');
    b.title = `${pts} 点 AI 额度 · 未开通会员`;
  } else {
    t.textContent = S.credits > 0 ? `${S.credits} 次导出` : '点数用尽';
    b.classList.toggle('has-credit', S.credits > 0);
    b.classList.toggle('no-credit', S.credits <= 0);
    b.title = (S.user && S.user.email) || '';
  }
}

/** 是否处于"可导出干净版"状态：会员不限次，否则看免费次数 */
function unlocked() {
  return !S.anonymous && ((S.wallet && S.wallet.member) || S.credits > 0);
}


function welcome(isSample) {
  const name = S.profile.basic.name || '同学';
  pushBot(`<div>我是落笔。我不帮你写漂亮话，只帮你把做过的事写成<b>经得起追问</b>的话。</div>
    <div style="margin-top:6px">现在这份档案是${isSample ? '示例（林知远 · 虚构演示数据）' : '你上次保存的'}，可以直接点「一键诊断」看问题，也可以先「导入旧简历」换成你自己的。</div>`);
  // 自动跑一次诊断，让右侧不是空的（不追问用户，直接给结果）
  S.booting = true;
  doLint().finally(() => { S.booting = false; });
}

/* ---------------------------------------------------------------- 渲染总入口 */
function renderAll() {
  renderSteps();
  renderPreview();
  renderSide();
}

function stageDone(idx) {
  const p = S.profile;
  if (!p) return false;
  const bullets = [...(p.internships || []), ...(p.projects || [])].reduce((a, i) => a + (i.bullets || []).length, 0);
  switch (idx) {
    case 0: return !!(p.basic.name && bullets > 0);
    case 1: return !!S.match;
    case 2: return S.rewritten > 0;
    case 3: return !!(S.lint && !S.lint.page.overflow);
    case 4: return S.exported;
  }
  return false;
}

function renderSteps() {
  let cur = 0;
  for (let i = 0; i < STAGES.length; i++) {
    if (stageDone(i)) cur = i + 1; else break;
  }
  $('#steps').innerHTML = STAGES.map((s, i) =>
    `<span class="step ${i < cur ? 'done' : ''} ${i === Math.min(cur, 4) ? 'cur' : ''}">${i + 1} ${s}</span>`).join('');
  $('#chatSub').textContent = `当前阶段：${STAGES[Math.min(cur, 4)]} · ${cur}/5 已完成`;
}

/* ---------------------------------------------------------------- A4 预览 */
function bulletHtml(text, bid, flagged) {
  return `<li class="${flagged ? 'flagged' : ''}" data-bid="${bid}" title="${flagged ? '点击改写这条' : ''}">${esc(text)}</li>`;
}

function renderPreview() {
  const p = S.profile;
  if (!p) return;
  const theme = THEME_MAP[p.preferences.theme] || THEME_MAP.blue;
  const two = p.preferences.template === 'two';
  const fontPct = p.preferences.fontPct || 100;
  const flagMap = {};
  if (S.lint) {
    S.lint.issues.forEach((i) => {
      if (!i.bulletId) return;
      if (i.level === 'error' || i.level === 'warn') flagMap[i.bulletId] = true;
    });
  }

  const contact = [p.basic.phone, p.basic.email, p.basic.city].filter(Boolean)
    .map((x) => `<span>${esc(x)}</span>`).join('');
  const head = `<div class="r-head">
      <div>
        <div class="r-name">${esc(p.basic.name) || '<span class="r-empty">你的名字</span>'}</div>
        <div class="r-title">${esc((p.intent.targetRoles || [])[0] || '求职意向待填')}</div>
        <div class="r-contact">${contact || '<span class="r-empty">手机号 / 邮箱 / 城市</span>'}</div>
      </div>
      <div class="r-photo" id="photoBox">${p.basic.photo ? `<img src="${p.basic.photo}">` : '头像'}<span class="up">上传</span></div>
    </div>`;

  const edu = (p.education || []).map((e) => `
    <div class="r-item">
      <div class="r-item-head">
        <div><span class="r-item-name">${esc(e.school) || '学校'}</span><span class="r-item-role">${esc(e.major) || '专业'}</span><span class="r-item-role">${esc(e.degree) || ''}</span></div>
        <span class="r-time">${esc(e.time)}</span>
      </div>
      ${e.gpa || (e.notes || []).length ? `<div class="r-edu-note">${e.gpa ? 'GPA ' + esc(e.gpa) : ''}${e.gpa && (e.notes || []).length ? ' · ' : ''}${esc((e.notes || []).join(' · '))}</div>` : ''}
    </div>`).join('') || '<div class="r-empty">还没有教育经历</div>';

  const mkItems = (arr, nameKey, sect) => (arr || []).map((it, i) => `
    <div class="r-item">
      <div class="r-item-head">
        <div><span class="r-item-name">${esc(it[nameKey]) || '未命名'}</span>${it.role ? `<span class="r-item-role">${esc(it.role)}</span>` : ''}</div>
        <span class="r-time">${esc(it.time)}</span>
      </div>
      <ul class="r-bullets">${(it.bullets || []).map((b, j) => bulletHtml(b.text, `${sect}.${i}.${j}`, flagMap[`${sect}.${i}.${j}`])).join('')}</ul>
    </div>`).join('') || `<div class="r-empty">还没有${sect === 'internships' ? '实习' : '项目'}经历</div>`;

  const skills = (p.skills || []).map((g) => `
    <div style="margin-bottom:5px"><span class="r-tag" style="background:var(--pas);border-color:var(--pas);color:var(--pa);font-weight:600">${esc(g.group)}</span>
    ${(g.items || []).map((x) => `<span class="r-tag">${esc(x)}</span>`).join('')}</div>`).join('') || '<div class="r-empty">还没有技能清单</div>';

  const summary = p.summary ? `<div class="r-summary">${esc(p.summary)}</div>` : '<div class="r-empty">还没有自我评价（建议 3 句话：做什么 + 做成过什么 + 想做什么）</div>';

  let body;
  if (two) {
    body = `<div class="resume-two">
      <aside class="r-side">
        <div class="r-photo" id="photoBox">${p.basic.photo ? `<img src="${p.basic.photo}">` : '头像'}<span class="up">上传</span></div>
        <div class="r-name">${esc(p.basic.name) || '你的名字'}</div>
        <div class="r-title">${esc((p.intent.targetRoles || [])[0] || '求职意向')}</div>
        <div class="r-contact">${contact}</div>
        <div class="r-sec"><div class="r-sec-title">技能</div>${skills}</div>
        <div class="r-sec"><div class="r-sec-title">教育</div>${edu}</div>
      </aside>
      <div class="r-main">
        <div class="r-sec"><div class="r-sec-title">实习经历</div>${mkItems(p.internships, 'org', 'internships')}</div>
        <div class="r-sec"><div class="r-sec-title">项目经历</div>${mkItems(p.projects, 'name', 'projects')}</div>
        <div class="r-sec"><div class="r-sec-title">自我评价</div>${summary}</div>
      </div>
    </div>`;
  } else {
    body = `${head}
      <div class="r-sec"><div class="r-sec-title">教育背景</div>${edu}</div>
      <div class="r-sec"><div class="r-sec-title">实习经历</div>${mkItems(p.internships, 'org', 'internships')}</div>
      <div class="r-sec"><div class="r-sec-title">项目经历</div>${mkItems(p.projects, 'name', 'projects')}</div>
      <div class="r-sec"><div class="r-sec-title">技能证书</div>${skills}</div>
      <div class="r-sec"><div class="r-sec-title">自我评价</div>${summary}</div>`;
  }

  const paper = $('#paper');
  paper.className = 'paper' + (two ? ' two-col' : '') + (unlocked() ? '' : ' locked');
  paper.style.setProperty('--pa', theme);
  paper.style.setProperty('--pas', hexA(theme, .12));
  paper.style.fontSize = (14 * fontPct / 100) + 'px';
  paper.innerHTML = body;

  // 点击带标记的 bullet → 直接改写
  $$('.r-bullets li.flagged', paper).forEach((li) => {
    li.onclick = () => doRewrite(li.dataset.bid);
  });
  const pb = $('#photoBox', paper);
  if (pb) pb.onclick = pickPhoto;

  fitPaper();
  updatePageBadge();
}

function hexA(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

function fitPaper() {
  const wrap = $('#paperScroll'), frame = $('#paperFrame'), paper = $('#paper');
  if (!wrap || !paper) return;
  const avail = wrap.clientWidth - 36;
  const scale = Math.min(1, avail / 794);
  paper.style.transform = `scale(${scale})`;
  frame.style.width = (794 * scale) + 'px';
  frame.style.height = (paper.offsetHeight * scale) + 'px';
}

function updatePageBadge() {
  const b = $('#pageBadge');
  if (S.lint) {
    const pg = S.lint.page;
    b.textContent = pg.overflow ? `超出 ${pg.pages} 页` : 'A4 一页内';
    b.classList.toggle('warn', pg.overflow);
  } else {
    b.textContent = '待诊断';
    b.classList.remove('warn');
  }
}

function pickPhoto() {
  const inp = document.createElement('input');
  inp.type = 'file';
  inp.accept = 'image/*';
  inp.onchange = () => {
    const f = inp.files[0];
    if (!f) return;
    const r = new FileReader();
    r.onload = () => { S.profile.basic.photo = r.result; renderPreview(); toast('头像已更新，会一起打印进 PDF'); };
    r.readAsDataURL(f);
  };
  inp.click();
}

/* ---------------------------------------------------------------- 右栏 */
function renderSide() {
  const box = $('#sideBody');
  $$('#sideTabs button').forEach((b) => b.classList.toggle('is-active', b.dataset.stab === S.sideTab));
  if (S.sideTab === 'diag') box.innerHTML = sideDiag();
  else if (S.sideTab === 'profile') box.innerHTML = sideProfile();
  else if (S.sideTab === 'jd') box.innerHTML = sideJd();
  else box.innerHTML = sideQs();
  bindSide();
}

function completeness() {
  const p = S.profile;
  const items = [
    ['基础信息', !!(p.basic.name && p.basic.phone), '姓名 + 手机号是导出 PDF 的最低要求'],
    ['求职意向', !!(p.intent.targetRoles || []).length, '没有目标岗位，改写就没有方向'],
    ['教育背景', (p.education || []).length > 0, '至少一段教育经历'],
    ['实习经历', (p.internships || []).length > 0, 'HR 最看重的板块'],
    ['项目经历', (p.projects || []).length > 0, '无实习时，项目就是主战场'],
    ['技能清单', (p.skills || []).length > 0, '按「工具 / 业务 / AI」分组'],
    ['自我评价', !!p.summary, '200 字内，别堆形容词'],
    ['证据链', (p.evidence || []).length > 0, '每个数字挂一条证据'],
  ];
  const done = items.filter((i) => i[1]).length;
  return { score: Math.round(done / items.length * 100), items };
}

function sideDiag() {
  const c = completeness();
  let html = `<div class="s-block">
    <h4>档案完整度<span class="sub">${c.score}%</span></h4>
    <div class="prog"><i style="width:${c.score}%"></i></div>
    ${c.items.map(([n, ok, hint]) => `<div class="chk ${ok ? 'ok' : 'no'}" title="${esc(hint)}"><span class="box"></span><span class="t">${n}</span></div>`).join('')}
  </div>`;

  if (!S.lint) {
    html += `<div class="s-block"><h4>HR 诊断</h4><p class="empty">点「一键诊断」，用规则引擎跑一遍：首屏 / 经历价值 / 语言 / 排版四维打分。</p></div>`;
    return html;
  }
  const L = S.lint;
  const cls = L.score >= 80 ? 's-good' : L.score >= 60 ? 's-mid' : 's-bad';
  html += `<div class="s-block">
    <h4>HR 诊断<span class="sub">${L.issueCount.error} 严重 / ${L.issueCount.warn} 提醒</span></h4>
    <div class="score-line"><span class="score-big ${cls}">${L.score}</span><span style="color:#8b8479;font-size:12px">/ 100 · ${L.grade} 档</span></div>
    <div class="dim">${L.dimensions.map((d) => `
      <div class="dim-row" title="${esc(d.comment)}"><span class="nm">${d.name}</span>
        <span class="bar"><i style="width:${d.score}%;background:${d.score >= 80 ? '#2f6b4f' : d.score >= 60 ? '#57514a' : '#b5341f'}"></i></span>
        <span class="vl">${d.score}</span></div>`).join('')}</div>
  </div>`;

  const top = L.issues.slice(0, 8);
  html += `<div class="s-block"><h4>要改的问题<span class="sub">共 ${L.issues.length} 条</span></h4>
    ${top.map((i, k) => `<div class="issue ${i.level}">
      <div class="ih"><span class="rule">${esc(i.rule)}</span>${i.where ? `<span class="rule">· ${esc(i.where)}</span>` : ''}</div>
      <div class="p">${esc(i.problem)}</div>
      <div class="f">→ ${esc(i.fix)}</div>
      ${i.bulletId ? `<button class="mini act" data-rewrite="${i.bulletId}">改写这条</button>` : ''}
    </div>`).join('')}
  </div>`;
  return html;
}

function sideProfile() {
  const p = S.profile;
  const b = (p.internships || []).length + (p.projects || []).length;
  const bl = [...(p.internships || []), ...(p.projects || [])].reduce((a, i) => a + (i.bullets || []).length, 0);
  return `<div class="s-block"><h4>档案概览<span class="sub">${b} 段经历 / ${bl} 条 bullet</span></h4>
    <div class="kv"><span>姓名</span><b>${esc(p.basic.name) || '—'}</b></div>
    <div class="kv"><span>联系方式</span><b>${esc(p.basic.phone || p.basic.email) || '—'}</b></div>
    <div class="kv"><span>目标岗位</span><b>${esc((p.intent.targetRoles || []).join(' / ')) || '—'}</b></div>
    <div class="kv"><span>可到岗</span><b>${esc(p.intent.available) || '—'}</b></div>
    <div class="kv"><span>证据条数</span><b>${(p.evidence || []).length}</b></div>
    <div class="kv"><span>最后更新</span><b>${esc((p.updatedAt || '').slice(0, 16).replace('T', ' '))}</b></div>
    <div style="margin-top:10px"><button class="mini primary" id="sideEdit">逐项编辑</button>
    <button class="mini" id="sideEvidence">看证据链</button></div>
  </div>
  <div class="s-block"><h4>记忆说明</h4>
    <p style="font-size:12px;color:#57514a;line-height:1.6">落笔不靠"长对话"记住你：每轮只读这份结构化档案 + 最近几条消息。所以对话可以断，档案不会丢；档案里的内容你随时能改、能删、能导出。</p>
  </div>`;
}

function sideJd() {
  if (!S.match) {
    return `<div class="s-block"><h4>岗位定向</h4><p class="empty">还没贴 JD。贴一段岗位描述，落笔会拆出：硬门槛、关键词、隐性信号，并算出匹配度。</p>
      <div style="text-align:center"><button class="mini primary" id="sideJdBtn">贴 JD</button></div></div>`;
  }
  const M = S.match;
  const cls = M.score >= 75 ? 's-good' : M.score >= 55 ? 's-mid' : 's-bad';
  return `<div class="s-block"><h4>匹配度<span class="sub">${M.level}</span></h4>
    <div class="score-line"><span class="score-big ${cls}">${M.score}</span><span style="color:#8b8479;font-size:12px">/ 100</span></div>
    <div class="kv"><span>关键词覆盖</span><b>${M.keywords.covered}/${M.keywords.total}</b></div>
    <div style="margin-top:8px">${M.keywords.hit.slice(0, 10).map((k) => `<span class="tag green">${esc(k.word)}</span>`).join('')}</div>
    ${M.keywords.missing.length ? `<div style="margin-top:6px;font-size:11.5px;color:#8b8479">缺口</div>
      <div style="margin-top:3px">${M.keywords.missing.slice(0, 8).map((k) => `<span class="tag orange">${esc(k.word)}</span>`).join('')}</div>` : ''}
  </div>
  <div class="s-block"><h4>硬门槛</h4>
    ${M.hardReqs.map((h) => `<div class="kv"><span>${esc(h.name)} · ${esc(h.text)}</span>
      <b style="color:${h.status === 'pass' ? '#2f6b4f' : '#b5341f'}">${h.status === 'pass' ? '满足' : '待确认'}</b></div>`).join('') || '<p class="empty">JD 里没写明确门槛</p>'}
  </div>
  <div class="s-block"><h4>隐性信号<span class="sub">决定面试怎么问</span></h4>
    ${M.signals.map((s) => `<div style="margin-bottom:6px"><span class="tag blue">${esc(s.word)}</span>
      <div style="font-size:11.5px;color:#57514a;line-height:1.5;margin-top:2px">${esc(s.why)}</div></div>`).join('') || '<p class="empty">没识别到明显信号</p>'}
  </div>`;
}

function sideQs() {
  if (!S.qs) {
    return `<div class="s-block"><h4>面试追问</h4><p class="empty">生成后，简历上每个数字都会对应一个口径问题。</p>
      <div style="text-align:center"><button class="mini primary" id="sideQsBtn">生成追问清单</button></div></div>`;
  }
  return `<div class="s-block"><h4>面试追问<span class="sub">${S.qs.total} 题</span></h4>
    ${S.qs.questions.slice(0, 10).map((q) => `<div class="qitem">
      <div class="qh"><span class="tag ${q.kind === '数字口径' || q.kind === '证据核验' ? 'red' : 'blue'}">${esc(q.kind)}</span></div>
      <div class="q">${esc(q.q)}</div>
      <div class="meta">考察：${esc(q.point)}</div>
    </div>`).join('')}
    <button class="mini" id="sideGoDrill">去面试陪练看完整版</button>
  </div>`;
}

function bindSide() {
  $$('[data-rewrite]', $('#sideBody')).forEach((b) => { b.onclick = () => doRewrite(b.dataset.rewrite); });
  const e1 = $('#sideEdit'); if (e1) e1.onclick = openProfileModal;
  const e2 = $('#sideEvidence'); if (e2) e2.onclick = showEvidence;
  const j = $('#sideJdBtn'); if (j) j.onclick = openJdModal;
  const q = $('#sideQsBtn'); if (q) q.onclick = doInterview;
  const g = $('#sideGoDrill'); if (g) g.onclick = () => { switchView('drill'); renderDrill(); };
}

/* ---------------------------------------------------------------- 对话 */
function pushUser(text) {
  $('#msgs').insertAdjacentHTML('beforeend',
    `<div class="msg user"><span class="ava">我</span><div class="body"><div class="bubble">${esc(text)}</div></div></div>`);
  scrollMsgs();
}

function pushBot(html) {
  const el = document.createElement('div');
  el.className = 'msg';
  el.innerHTML = `<span class="ava">落</span><div class="body"><div class="bubble">${html}</div></div>`;
  $('#msgs').appendChild(el);
  scrollMsgs();
  return el;
}

function scrollMsgs() {
  const m = $('#msgs');
  m.scrollTop = m.scrollHeight;
}

/* ---------------------------------------------------------------- 诊断 */
async function doLint(silent) {
  if (!S.profile || S.busy) return;
  S.busy = true;
  const prev = S.lint ? S.lint.score : null;
  if (!silent && !S.booting) pushUser('帮我诊断一下这份简历');
  const load = silent ? null : pushBot('<span class="typing"><i></i><i></i><i></i></span> 正在跑规则引擎…');
  try {
    const d = await api('/api/lint', { profile: S.profile, jd: S.jd });
    S.lint = d.result;
    renderPreview();
    renderSide();
    renderSteps();
    if (load) load.remove();
    if (silent) {
      const delta = prev == null ? '' : (S.lint.score - prev >= 0 ? `（+${S.lint.score - prev}）` : `（${S.lint.score - prev}）`);
      pushBot(`<span style="color:#57514a">已重新打分：<b>${S.lint.score}</b> 分${prev == null ? '' : esc(delta)}，还剩 ${S.lint.issueCount.error} 处硬伤。</span>`);
    } else {
      pushBot(diagCardHtml(S.lint));
    }
  } catch (e) {
    if (load) load.remove();
    pushBot(`<span style="color:#b5341f">诊断失败：${esc(e.message)}</span>`);
  } finally {
    S.busy = false;
  }
}

function diagCardHtml(L, brief) {
  const cls = L.score >= 80 ? 's-good' : L.score >= 60 ? 's-mid' : 's-bad';
  const top = L.top3.length ? L.top3 : L.issues.slice(0, 3);
  return `<div>诊断完了：<b>${L.score} 分（${L.grade} 档）</b>。${L.score >= 75 ? '底子能用，接下来是把每条都对到岗位上。' : '先别急着投，三处硬伤修掉再看。'}</div>
  <div class="cc">
    <div class="cc-head">HR 诊断 · 四维<span class="sub">${L.issueCount.error} 严重 / ${L.issueCount.warn} 提醒 / ${L.issueCount.info} 建议</span></div>
    <div class="cc-body">
      <div class="score-line"><span class="score-big ${cls}">${L.score}</span><span style="color:#8b8479;font-size:12px">/100</span></div>
      <div class="dim">${L.dimensions.map((d) => `<div class="dim-row"><span class="nm">${d.name}</span>
        <span class="bar"><i style="width:${d.score}%;background:${d.score >= 80 ? '#2f6b4f' : d.score >= 60 ? '#57514a' : '#b5341f'}"></i></span>
        <span class="vl">${d.score}</span></div>`).join('')}</div>
      ${brief ? '' : `<div style="margin-top:9px;font-size:12.5px">最该动的三处：</div>`}
      ${brief ? '' : top.map((i) => `<div class="issue ${i.level}" style="margin-top:7px">
        <div class="ih"><span class="rule">${esc(i.rule)}</span></div>
        <div class="p">${esc(i.problem)}</div>
        <div class="f">→ ${esc(i.fix)}</div>
        ${i.bulletId ? `<button class="mini act" data-rewrite="${i.bulletId}" style="margin-top:4px">改写这条</button>` : ''}
      </div>`).join('')}
    </div>
    <div class="cc-foot"><button class="mini primary" data-act="jd">贴 JD 定向</button>
      <button class="mini" data-act="rewrite-worst">批量改写</button>
      <span class="hint">${L.page.overflow ? '超页了，先压缩' : '当前 ' + L.page.pages + ' 页'}</span></div>
  </div>`;
}

/* 绑定对话内按钮（事件委托，一次就够） */
document.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-act],[data-rewrite],[data-adopt]');
  if (!btn) return;
  if (btn.dataset.rewrite) return doRewrite(btn.dataset.rewrite);
  if (btn.dataset.adopt) return adoptVersion(btn.dataset.adopt, btn.dataset.text, btn);
  const act = btn.dataset.act;
  if (act === 'import') openImport();
  if (act === 'jd') openJdModal();
  if (act === 'lint') doLint();
  if (act === 'rewrite-all' || act === 'rewrite-worst') rewriteWorst();
  if (act === 'interview') doInterview();
  if (act === 'export') exportPdf();
  if (act === 'apply-profile') applyParsed(btn);
});

/* ---------------------------------------------------------------- 导入 */
function openImport() {
  $('#importTip').textContent = '';
  showModal('#modalImport');
}

async function doParse() {
  const text = $('#importText').value.trim();
  if (!text) return toast('先粘贴简历原文');
  const btn = $('#btnDoParse');
  btn.disabled = true;
  btn.textContent = '解析中…';
  try {
    const d = await api('/api/parse', { text });
    S._parsed = d.profile;
    hideModal();
    pushUser('导入我的旧简历');
    const p = d.profile;
    const bullets = [...(p.internships || []), ...(p.projects || [])].reduce((a, i) => a + (i.bullets || []).length, 0);
    pushBot(`<div>切好了，先跟你对一遍，别急着当最终版。</div>
      <div class="cc">
        <div class="cc-head">解析结果<span class="sub">${d.stats.lines} 行 → ${bullets} 条经历</span></div>
        <div class="cc-body">
          <div class="kv"><span>姓名</span><b>${esc(p.basic.name) || '未识别'}</b></div>
          <div class="kv"><span>联系方式</span><b>${esc(p.basic.phone || p.basic.email) || '未识别'}</b></div>
          <div class="kv"><span>教育</span><b>${esc((p.education[0] || {}).school) || '未识别'}</b></div>
          <div class="kv"><span>实习</span><b>${(p.internships || []).length} 段</b></div>
          <div class="kv"><span>项目</span><b>${(p.projects || []).length} 段</b></div>
          <div class="kv"><span>技能分组</span><b>${(p.skills || []).length} 组</b></div>
          ${d.warnings.length ? `<div style="margin-top:8px;font-size:12px;color:#b5341f">${d.warnings.map((w) => '· ' + esc(w)).join('<br>')}</div>` : ''}
        </div>
        <div class="cc-foot"><button class="mini primary" data-act="apply-profile">用这份档案</button>
          <button class="mini" data-act="import">重新粘贴</button>
          <span class="hint">应用后可在「编辑档案」逐项改</span></div>
      </div>`);
  } catch (e) {
    toast('解析失败：' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '开始解析';
  }
}

function applyParsed() {
  if (!S._parsed) return;
  S.profile = S._parsed;
  S.lint = null; S.match = null; S.qs = null; S.rewritten = 0;
  renderAll();
  saveProfile();
  pushBot('<div>已换成你的档案。我顺手再诊断一次，看看从哪开始改最值。</div>');
  doLint(true);
}

/* ---------------------------------------------------------------- JD */
function openJdModal() {
  const ta = $('#jdText');
  if (!ta.value) ta.value = S.jd || S.sampleJd;
  showModal('#modalJd');
}

async function doJd() {
  const jd = $('#jdText').value.trim();
  if (!jd) return toast('先贴一段 JD');
  hideModal();
  S.jd = jd;
  pushUser('目标岗位的 JD 我贴进来了，看看匹配度');
  const load = pushBot('<span class="typing"><i></i><i></i><i></i></span> 正在拆 JD…');
  try {
    const d = await api('/api/jd', { profile: S.profile, jd });
    S.match = d.result;
    load.remove();
    renderSide();
    renderSteps();
    const M = S.match;
    const cls = M.score >= 75 ? 's-good' : M.score >= 55 ? 's-mid' : 's-bad';
    pushBot(`<div>匹配度 <b>${M.score}</b>（${M.level}）。${M.advice[0] || ''}</div>
      <div class="cc">
        <div class="cc-head">岗位定向<span class="sub">覆盖 ${M.keywords.covered}/${M.keywords.total} 个关键词</span></div>
        <div class="cc-body">
          <div class="score-line"><span class="score-big ${cls}">${M.score}</span><span style="font-size:12px;color:#8b8479">/100</span></div>
          <div style="margin-top:6px">${M.keywords.hit.slice(0, 12).map((k) => `<span class="tag green">${esc(k.word)}</span>`).join('')}</div>
          ${M.keywords.missing.length ? `<div style="margin-top:8px;font-size:12px;color:#57514a">JD 里有、你简历里没有：</div>
            <div style="margin-top:4px">${M.keywords.missing.slice(0, 10).map((k) => `<span class="tag orange">${esc(k.word)}</span>`).join('')}</div>` : ''}
          ${M.signals.length ? `<div style="margin-top:8px;font-size:12px;color:#57514a">面试大概率会问：</div>
            <div style="margin-top:2px;font-size:12px">${M.signals.slice(0, 4).map((s) => '· ' + esc(s.why)).join('<br>')}</div>` : ''}
        </div>
        <div class="cc-foot"><button class="mini primary" data-act="rewrite-worst">按这个岗位改写</button>
          <button class="mini" data-act="interview">生成追问清单</button></div>
      </div>`);
  } catch (e) {
    load.remove();
    pushBot(`<span style="color:#b5341f">JD 解析失败：${esc(e.message)}</span>`);
  }
}

/* ---------------------------------------------------------------- 改写 */
function findBullet(bid) {
  const [sect, i, j] = bid.split('.');
  const item = (S.profile[sect] || [])[+i];
  if (!item) return null;
  return { sect, item, b: (item.bullets || [])[+j] };
}

async function doRewrite(bid) {
  const found = findBullet(bid);
  if (!found) return;
  const text = found.b.text;
  pushUser(`帮我改写这条：「${text.slice(0, 30)}${text.length > 30 ? '…' : ''}」`);
  const load = pushBot('<span class="typing"><i></i><i></i><i></i></span> 正在改写（不新增事实）…');
  try {
    const words = (S.match ? S.match.keywords.missing.map((k) => k.word) : []);
    const d = await api('/api/rewrite', { text, evidence: found.b.evidence, jdWords: words });
    load.remove();
    pushBot(rewriteCard(d, bid, text));
  } catch (e) {
    load.remove();
    pushBot(`<span style="color:#b5341f">改写失败：${esc(e.message)}</span>`);
  }
}

function rewriteCard(d, bid, original) {
  const head = d.mode === 'llm' ? '改写 · 大模型版' : '改写 · 规则版';
  const tips = d.mode === 'llm' ? '已按「不新增事实」约束生成' : '离线规则生成，建议配置 API Key 得到更好的版本';
  return `<div>三个侧重版本，选一个点「采用」。${d.mode === 'llm' ? '' : '（规则版改写偏模板化，主要作用是定位问题，最终版建议自己再落一遍。）'}</div>
  <div class="cc">
    <div class="cc-head">${head}<span class="sub">${tips}</span></div>
    <div class="cc-body">
      <div class="diff">
        <div class="row old"><span class="mark">原</span><span class="txt">${esc(original)}</span></div>
      </div>
      ${d.versions.map((v) => `<div class="ver-block">
        <div class="vh"><span class="tag blue">${esc(v.version)}</span>${esc(v.why)}
          ${v.guard && !v.guard.ok ? `<span class="tag red">疑似新增数字：${esc(v.guard.newNumbers.join(' / '))}</span>` : ''}</div>
        <div class="vt">${esc(v.text)}</div>
        <div class="vw">${v.evidenceStatus === 'verified' ? '已挂证据：' + esc(v.evidence) : '⚠ 这条还没有证据，导出前补上'}</div>
        <div style="margin-top:6px"><button class="mini primary" data-adopt="${bid}" data-text="${esc(v.text)}">采用这版</button></div>
      </div>`).join('')}
    </div>
    <div class="cc-foot"><span class="hint">采用后会自动重渲染并重新打分</span></div>
  </div>`;
}

function adoptVersion(bid, text, btn) {
  const found = findBullet(bid);
  if (!found) return;
  found.b.text = text;
  S.rewritten++;
  renderAll();
  saveProfile();
  if (btn) { btn.textContent = '已采用 ✓'; btn.disabled = true; btn.classList.remove('primary'); }
  toast('已替换，重新打分中…');
  doLint(true);
}

async function rewriteWorst() {
  if (!S.lint) { await doLint(true); }
  const seen = new Set();
  const targets = [];
  for (const i of (S.lint ? S.lint.issues : [])) {
    if (!i.bulletId || seen.has(i.bulletId)) continue;
    if (i.level !== 'error' && i.level !== 'warn') continue;
    seen.add(i.bulletId);
    targets.push(i.bulletId);
    if (targets.length >= 3) break;
  }
  if (!targets.length) return pushBot('目前没有需要改写的条目，先补经历或贴个 JD 再试。');
  for (const t of targets) {
    await doRewrite(t);
  }
}

/* ---------------------------------------------------------------- 面试 */
async function doInterview(silent) {
  if (S.qs && silent) { renderDrill(); return; }
  if (!silent) pushUser('把面试可能追问我的问题列出来');
  const load = silent ? null : pushBot('<span class="typing"><i></i><i></i><i></i></span> 站在面试官角度读一遍你的简历…');
  try {
    const d = await api('/api/interview', { profile: S.profile, jd: S.jd });
    S.qs = d.result;
    S.drill = {};                    // 重新出题，旧答案链作废——题号对不上，留着会串题
    if (load) load.remove();
    S.sideTab = 'qs';
    renderSide();
    renderDrill();
    if (!silent) pushBot(`<div>生成 ${S.qs.total} 个追问，按被问到的概率排序。核心是前三个：<b>你的数字怎么来的</b>。</div>
      <div class="cc"><div class="cc-head">面试防守清单<span class="sub">${S.qs.total} 题</span></div>
      <div class="cc-body">${S.qs.questions.slice(0, 4).map((q, k) => `<div style="padding:6px 0;border-bottom:1px dashed #eff0f1">
        <div style="font-size:12.5px"><b>${k + 1}.</b> ${esc(q.q)}</div>
        <div style="font-size:11.5px;color:#8b8479;margin-top:2px">考察：${esc(q.point)}</div></div>`).join('')}</div>
      <div class="cc-foot"><span class="hint">完整版在顶部「面试陪练」</span></div></div>`);
  } catch (e) {
    if (load) load.remove();
    pushBot(`<span style="color:#b5341f">生成失败：${esc(e.message)}</span>`);
  }
}

function renderDrill() {
  const grid = $('#drillGrid');
  if (!S.qs) {
    grid.innerHTML = `<div class="card" style="padding:30px;text-align:center;grid-column:1/-1">
      <p style="color:#8b8479;font-size:12.5px">正在按你的简历 + JD 缺口出题…</p></div>`;
    $('#qCount').textContent = '生成中';
    return;
  }
  $('#qCount').textContent = S.qs.total + ' 题';
  grid.innerHTML = S.qs.questions.map((q, k) => `
    <div class="qcard" data-i="${k}">
      <div class="qh"><span class="idx">${k + 1}</span>
        <span class="tag ${q.kind === '数字口径' || q.kind === '证据核验' ? 'red' : 'blue'}">${esc(q.kind)}</span></div>
      <div class="q">${esc(q.q)}</div>
      <div class="lab">考察点</div><div class="val">${esc(q.point)}</div>
      <div class="lab">回答框架</div><div class="val">${esc(q.frame)}</div>
      <div class="lab">常见坑</div><div class="val trap">${esc(q.trap)}</div>
      ${drillChain(k)}
    </div>`).join('');
  bindMic();          // 首次渲染的卡片也要挂上麦克风，不能只在重绘时挂
}

/* ---------------------------------------------------------------- 陪练：答完之后
   出题只是上半场。真正能救人的是"我把这句说出口，会被怎么追"。
   所以每道题下面挂一条答题链：写答案 → 审 → 系统给出四个检查点
   和面试官下一句会问什么 → 那条追问可以继续答，最多往下三层。

   为什么是链而不是分数：
   分数让人去追分，链让人看见自己第三次卡在同一个地方。
   规则判错的时候用户也能一眼看出来（每个检查点都带"凭什么这么判"）。 */
if (!S.drill) S.drill = {};

function drillChain(k) {
  if (!S.drill[k]) S.drill[k] = [{ q: (S.qs.questions[k] || {}).q || '', a: '', res: null, depth: 0 }];
  const chain = S.drill[k];
  return `<div class="qans">${chain.map((s, j) => `
    <div class="qstep${s.depth ? ' is-sub' : ''}">
      ${s.depth ? `<div class="qstep-q"><i>第 ${s.depth} 层追问</i>${esc(s.q)}</div>` : ''}
      <div class="qstep-box">
        <textarea data-k="${k}" data-j="${j}" placeholder="${s.depth ? '就着这一句答，两三行就够' : '用 STAR 写 3 行，面试前读两遍'}">${esc(s.a)}</textarea>
        <div class="qstep-bar">
          <button class="mini" data-act="check" data-k="${k}" data-j="${j}">审这一答</button>
          <button class="mic" data-act="voice" data-k="${k}" data-j="${j}" hidden>说</button>
          ${j > 0 ? `<button class="mini quiet" data-act="drop" data-k="${k}" data-j="${j}">去掉这层</button>` : ''}
          <span class="qstep-n">${s.a ? s.a.replace(/\s/g, '').length + ' 字' : ''}</span>
        </div>
      </div>
      ${s.res ? drillRes(k, j, s.res) : ''}
    </div>`).join('')}</div>`;
}

function drillRes(k, j, r) {
  const passed = r.dims.filter(d => d.ok).length;
  const DIMLAB = { spec: '有具体的事', quant: '有能核查的数', own: '分得清你我', result: '有前后对比' };
  const chk = r.dims.map(d => `<li class="${d.ok ? 'ok' : ''}">
      <i></i><b>${DIMLAB[d.key] || d.key}</b><span>${esc(d.hit)}</span></li>`).join('');
  const asks = r.asks.length ? `<div class="dask-h">面试官接下来会问</div>
    <ol class="dask">${r.asks.map((x, n) => `<li>
      <span class="dask-t">${esc(x.label)}</span>${esc(x.q)}
      ${(r.__depth || 0) + 1 < 3 ? `<button class="mini quiet" data-act="push" data-k="${k}" data-j="${j}" data-n="${n}">接着答</button>` : ''}
    </li>`).join('')}</ol>` : '';
  const vague = (r.vague && r.vague.length)
    ? `<div class="dvague">这段里有「${r.vague.map(esc).join('」「')}」——面试官听到这类词会直接往下追。</div>` : '';
  return `<div class="dres lv${r.gradeLevel}">
    <div class="dres-top"><span class="dres-g">${esc(r.grade)}</span>
      <span class="dres-n">四个检查点过了 ${passed} 个</span></div>
    <ul class="dchk">${chk}</ul>
    ${vague}
    ${asks}
    <div class="dfix"><i>改一处</i>${esc(r.fix)}</div>
  </div>`;
}

function drillRerender(k, focusLast) {
  const card = document.querySelector(`.qcard[data-i="${k}"]`);
  if (!card) return;
  const old = card.querySelector('.qans');
  if (old) old.outerHTML = drillChain(k);
  if (focusLast) {
    const tas = card.querySelectorAll('.qans textarea');
    const t = tas[tas.length - 1];
    if (t) { t.focus(); t.scrollIntoView({ block: 'center' }); }
  }
  bindMic();
}

async function drillCheck(k, j) {
  const s = (S.drill[k] || [])[j];
  if (!s) return;
  if (!s.a.trim()) { toast('先写两句——空白答不出东西'); return; }
  const btn = document.querySelector(`.qcard[data-i="${k}"] button[data-act="check"][data-j="${j}"]`);
  const old = btn ? btn.textContent : '';
  if (btn) { btn.textContent = '在看…'; btn.disabled = true; }
  try {
    const d = await api('/api/drill/check', { q: s.q, answer: s.a });
    s.res = d.result;
    s.res.__depth = s.depth;
  } catch (e) {
    if (btn) { btn.textContent = old; btn.disabled = false; }
    return toast('审不了：' + e.message);
  }
  drillRerender(k, false);
  if (btn) btn.disabled = false;
}

/* 语音输入：浏览器原生识别，不支持就藏起来，不装第三方。
   面试是"说出来"的事，但对着屏幕敲字会写成作文——
   所以这里留一个「说」的入口，让人用嘴先过一遍。 */
let _micRec = null;
function bindMic() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const mics = document.querySelectorAll('#drillGrid .mic');
  mics.forEach(m => { m.hidden = !SR; });
  if (!SR) return;
  mics.forEach(m => {
    if (m.__bound) return;
    m.__bound = true;
    m.onclick = () => {
      if (_micRec) { try { _micRec.stop(); } catch (e) {} _micRec = null; return; }
      const k = +m.dataset.k, j = +m.dataset.j;
      const ta = document.querySelector(`.qcard[data-i="${k}"] textarea[data-j="${j}"]`);
      const rec = new SR();
      rec.lang = 'zh-CN'; rec.continuous = true; rec.interimResults = true;
      const base = ta.value;
      rec.onresult = (ev) => {
        let txt = '';
        for (let x = ev.resultIndex; x < ev.results.length; x++) txt += ev.results[x][0].transcript;
        ta.value = base + txt;
        ta.dispatchEvent(new Event('input', { bubbles: true }));
      };
      rec.onend = () => { m.classList.remove('on'); m.textContent = '说'; _micRec = null; };
      rec.onerror = (ev) => { m.classList.remove('on'); m.textContent = '说'; _micRec = null;
        if (ev.error === 'not-allowed') toast('麦克风被浏览器拦了——地址栏左边那把锁里允许一下'); };
      try { rec.start(); } catch (e) { return toast('开不了麦克风'); }
      _micRec = rec;
      m.classList.add('on'); m.textContent = '停';
    };
  });
}

/* 事件委托：卡片是整块重绘的，逐个绑定会漏 */
(function bindDrillOnce() {
  const grid = $('#drillGrid');
  if (!grid || grid.__bound) return;
  grid.__bound = true;
  grid.addEventListener('input', (e) => {
    const t = e.target;
    if (t.tagName !== 'TEXTAREA') return;
    const k = +t.dataset.k, j = +t.dataset.j;
    const s = (S.drill[k] || [])[j];
    if (!s) return;
    s.a = t.value;
    s.res = null;                       // 改了答案，旧的判断就作废，不能留着误导人
    const n = t.closest('.qstep').querySelector('.qstep-n');
    if (n) n.textContent = t.value.trim() ? t.value.replace(/\s/g, '').length + ' 字' : '';
    const res = t.closest('.qstep').querySelector('.dres');
    if (res) res.remove();
  });
  grid.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-act]');
    if (!b) return;
    const act = b.dataset.act, k = +b.dataset.k, j = +b.dataset.j;
    if (act === 'check') return drillCheck(k, j);
    if (act === 'drop') {
      const chain = S.drill[k] || [];
      chain.splice(j, 1);
      return drillRerender(k, false);
    }
    if (act === 'push') {
      const chain = S.drill[k] || [];
      const s = chain[j];
      const ask = (s.res && s.res.asks[+b.dataset.n]) || null;
      if (!ask) return;
      if (s.depth + 1 >= 3) return toast('已经挖到第三层了，再往下真实面试里对方会换题');
      chain.push({ q: ask.q, a: '', res: null, depth: s.depth + 1 });
      return drillRerender(k, true);
    }
  });
})();
bindMic();

/* ---------------------------------------------------------------- 导出 / 保存
   导出是全产品唯一的收费点，也是唯一的转化闸门。
   服务端返回三种状态，前端各走一条路 —— 不在前端"假装成功"。 */
async function exportPdf() {
  if (!S.profile) return;
  if (S.busy) return;
  const overflow = S.lint && S.lint.page.overflow;

  S.busy = true;
  pushUser('导出 PDF');
  const load = pushBot('<span class="typing"><i></i><i></i><i></i></span> 正在生成…');
  try {
    const d = await api('/api/export', {
      company: (S.apps[0] && S.apps[0].company) || '',
    });
    load.remove();
    S.credits = d.credits;
    S.exported = true;
    S.busy = false;
    renderAcct();
    renderPreview();
    renderSteps();
    // 这版投出去过一次，版本条上的「已投」要立刻变
    if (window.JHVer) JHVer.refresh();
    if (overflow) toast('超出一页了，打印会分成两页——先按建议压缩', 3000);
    const head = d.member
      ? '会员不限次，直接导。'
      : `扣了 1 次，还剩 <b>${d.credits}</b> 次。`;
    pushBot(`<div>${head}正在打开打印面板。</div>
      <div style="margin-top:6px">目标选「另存为 PDF」，页边距「无」，勾上「背景图形」——这样出来的 PDF 保留文字层，ATS 能读。</div>
      <div style="margin-top:6px;font-size:12px;color:#8b8479">文件名建议：姓名-岗位-学校.pdf</div>`);
    setTimeout(() => window.print(), 320);
  } catch (e) {
    load.remove();
    S.busy = false;
    if (e.code === 'need_login') {
      track('gate', { reason: 'need_login' });
      pushBot(`<div>导出这一步要账号。<b>注册送 3 次</b>，你现在改的档案会一起带过去，不用重来。</div>`);
      openAuth('export');
    } else if (e.code === 'need_member' || e.code === 'need_credit') {
      S.credits = e.data && e.data.credits != null ? e.data.credits : 0;
      renderAcct();
      renderPreview();
      track('gate', { reason: 'need_member' });
      pushBot(`<div>免费导出次数用完了。你已经改到可以投的程度了——<b>4.9 开通会员，导出不限次</b>，顺带解锁岗位库和坑库。</div>
        <div style="margin-top:6px;font-size:12px;color:#8b8479">AI 能力是另一套计费，按点扣；不开会员也能单独充点。</div>`);
      openPay();
    } else {
      pushBot(`<span style="color:#b5341f">导出失败：${esc(e.message)}</span>`);
    }
  }
}

/* ---------------------------------------------------------------- 账号流程 */
let authMode = 'register';
function openAuth(after) {
  S._afterAuth = after || null;
  authMode = 'register';
  $('#authTitle').textContent = '先建个账号';
  $('#btnAuthGo').textContent = '注册并继续';
  $('#btnAuthToggle').textContent = '已有账号？去登录';
  $('#authErr').textContent = '';
  $('#authPw').value = '';
  showModal('#modalAuth');
  setTimeout(() => $('#authEmail').focus(), 60);
}

function toggleAuth() {
  authMode = authMode === 'register' ? 'login' : 'register';
  const reg = authMode === 'register';
  $('#authTitle').textContent = reg ? '先建个账号' : '登录';
  $('#btnAuthGo').textContent = reg ? '注册并继续' : '登录';
  $('#btnAuthToggle').textContent = reg ? '已有账号？去登录' : '还没账号？去注册';
  $('#authErr').textContent = '';
}

async function doAuth() {
  const email = $('#authEmail').value.trim();
  const pw = $('#authPw').value;
  const btn = $('#btnAuthGo');
  const err = $('#authErr');
  err.textContent = '';
  if (!email || !pw) { err.textContent = '邮箱和口令都要填'; return; }
  btn.disabled = true;
  btn.textContent = authMode === 'register' ? '注册中…' : '登录中…';
  try {
    const path = authMode === 'register' ? '/api/auth/register' : '/api/auth/login';
    const d = await api(path, { email, pw });
    S.user = d.user;
    S.credits = d.credits;
    S.anonymous = false;
    track(authMode === 'register' ? 'signup' : 'login', {});
    hideModal();
    renderAcct();
    renderPreview();
    // 匿名时建的版本已经迁到账号下，重新拉一次列表
    if (window.JHVer) JHVer.refresh();
    const moved = d.migrated || 0;
    pushBot(`<div>好了，${esc((d.user && d.user.name) || '同学')}。这个号有 <b>${d.credits}</b> 次导出。${moved ? `刚才的档案和 ${moved} 条记录已经并到账号上了。` : ''}</div>`);
    if (S._afterAuth === 'export') {
      S._afterAuth = null;
      setTimeout(exportPdf, 400);
    }
  } catch (e) {
    err.textContent = e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = authMode === 'register' ? '注册并继续' : '登录';
  }
}

function openPay() {
  const pr = S.pricing || {};
  const m = pr.member || { price: 4.9, gift: 20, benefits: [] };
  const w = S.wallet || {};
  const est = pr.estimate || {};

  const isMember = !!w.member;
  $('#memberCard').innerHTML = `
    <div class="member-head">
      <div>
        <div class="member-title">创始会员 ${isMember ? '<span class="on">已开通</span>' : ''}</div>
        <div class="member-sub">一次性开通，不是订阅，不会自动续费</div>
      </div>
      <div class="member-price">${isMember ? '已解锁' : '¥' + m.price}</div>
    </div>
    <ul class="member-list">${(m.benefits || []).map((b) => `<li>${esc(b)}</li>`).join('')}</ul>
    ${isMember ? `<div class="member-foot">你现在有 <b>${w.points || 0}</b> 点，导出不限次。</div>`
      : `<div class="member-foot">开通即赠 <b>${m.gift}</b> 点，够试 ${Math.max(1, Math.floor(m.gift / Math.max(1, est.industry || 1)))} 次 AI 岗位分析。</div>`}`;

  const packs = pr.packs || [];
  $('#plans').innerHTML = packs.map((it) => {
    const per = (it.price / it.points * 100).toFixed(2);
    const times = Math.round(it.points / Math.max(1, est.industry || 1));
    return `<div class="plan ${it.hot ? 'hot' : ''}" data-pack="${esc(it.key)}">
      ${it.hot ? '<span class="plan-tag">最多人选</span>' : ''}
      <div class="plan-name">${esc(it.name)}</div>
      <div class="plan-price"><b>¥${it.price}</b><span>${it.points} 点 · ${per} 分/点</span></div>
      <div class="plan-desc">${esc(it.desc)}<br><span class="dim">约 ${times} 次 AI 岗位分析</span></div>
    </div>`;
  }).join('');

  $('#payNote').textContent = pr.note || '';
  $('#redeemTip').textContent = '会员码和充值码都在这里激活，系统会自动分辨。';
  $('#leadTip').textContent = '12 小时内人工联系，直接转给你，不跳第三方页面。';
  showModal('#modalPay');
  track('payview', {});
}

async function doRedeem() {
  const code = $('#codeInput').value.trim();
  if (!code) return;
  const tip = $('#redeemTip');
  tip.textContent = '正在激活…';
  // 一个输入框吃两种码：先试会员码，若服务端说"这是充值码"，自动改试充值
  try {
    let d;
    try {
      d = await api('/api/member', { code });
    } catch (e1) {
      if (/充值码/.test(e1.message || '')) {
        d = await api('/api/topup', { code });
      } else {
        throw e1;
      }
    }
    S.wallet = d.wallet || S.wallet;
    applyMe(d);
    tip.textContent = d.added
      ? `充值成功，+${d.added} 点，现在有 ${d.points} 点。`
      : (d.already ? '你已经是会员了，码没有消耗。' : `会员已开通，赠 ${d.gift} 点，现在有 ${d.points} 点。`);
    pushBot(d.added
      ? `<div>到账 <b>${d.added}</b> 点，现在共 <b>${d.points}</b> 点。AI 能力可以直接用了。</div>`
      : `<div>会员已开通，导出不限次，另送 <b>${d.gift || 0}</b> 点 AI 额度。</div>`);
  } catch (e) {
    tip.textContent = e.message;
  }
}

/** 服务端返回的钱包/额度，统一同步到本地状态 */
function applyMe(d) {
  if (d.credits != null) S.credits = d.credits;
  if (d.wallet) S.wallet = d.wallet;
  renderAcct();
  renderPreview();
}

async function doLead() {
  const contact = $('#leadContact').value.trim();
  if (!contact) { $('#leadTip').textContent = '至少留一个能联系上的方式'; return; }
  try {
    const d = await api('/api/lead', {
      contact, intent: 'buy', note: (S.profile && S.profile.basic.name) || '',
    });
    $('#leadTip').textContent = d.msg || '收到了，我尽快联系你。';
    $('#leadContact').value = '';
  } catch (e) {
    $('#leadTip').textContent = e.message;
  }
}


let saveTimer = null;
function saveProfile() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try {
      await api('/api/save', { profile: S.profile, score: S.lint ? S.lint.score : 0 });
    } catch (e) { /* 静默 */ }
  }, 600);
}

/* ---------------------------------------------------------------- 档案编辑 */
function openProfileModal() {
  const p = S.profile;
  const fld = (label, val, path, type) => `<div class="fld"><label>${label}</label>
    <${type === 'area' ? 'textarea' : 'input'} data-path="${path}" ${type === 'area' ? 'rows="3"' : ''}>${type === 'area' ? esc(val) : ''}</${type === 'area' ? 'textarea' : 'input'}></div>`;
  let html = `<div class="sec-title">基础信息</div>
    <div class="fld-row">
      <div class="fld"><label>姓名</label><input data-path="basic.name" value="${esc(p.basic.name)}"></div>
      <div class="fld"><label>手机号</label><input data-path="basic.phone" value="${esc(p.basic.phone)}"></div>
    </div>
    <div class="fld-row">
      <div class="fld"><label>邮箱</label><input data-path="basic.email" value="${esc(p.basic.email)}"></div>
      <div class="fld"><label>城市</label><input data-path="basic.city" value="${esc(p.basic.city)}"></div>
    </div>
    <div class="fld-row">
      <div class="fld"><label>目标岗位（逗号分隔）</label><input data-path="intent.targetRoles" value="${esc((p.intent.targetRoles || []).join(','))}"></div>
      <div class="fld"><label>可到岗</label><input data-path="intent.available" value="${esc(p.intent.available)}"></div>
    </div>
    <div class="sec-title">教育背景</div>`;
  (p.education || []).forEach((e, i) => {
    html += `<div class="fld-row">
      <div class="fld"><label>学校</label><input data-path="education.${i}.school" value="${esc(e.school)}"></div>
      <div class="fld"><label>专业</label><input data-path="education.${i}.major" value="${esc(e.major)}"></div>
    </div>
    <div class="fld-row">
      <div class="fld"><label>学历</label><input data-path="education.${i}.degree" value="${esc(e.degree)}"></div>
      <div class="fld"><label>时间</label><input data-path="education.${i}.time" value="${esc(e.time)}"></div>
    </div>`;
  });
  ['internships', 'projects'].forEach((sect) => {
    html += `<div class="sec-title">${sect === 'internships' ? '实习经历' : '项目经历'}</div>`;
    (p[sect] || []).forEach((it, i) => {
      html += `<div class="fld-row">
        <div class="fld"><label>${sect === 'internships' ? '公司' : '项目名'}</label>
          <input data-path="${sect}.${i}.${sect === 'internships' ? 'org' : 'name'}" value="${esc(it[sect === 'internships' ? 'org' : 'name'])}"></div>
        <div class="fld"><label>角色</label><input data-path="${sect}.${i}.role" value="${esc(it.role)}"></div>
      </div>
      <div class="fld"><label>时间</label><input data-path="${sect}.${i}.time" value="${esc(it.time)}"></div>
      <div style="font-size:11.5px;color:#8b8479;margin:4px 0">经历条目（一条一行，写清动作 + 结果）</div>`;
      (it.bullets || []).forEach((b, j) => {
        html += `<div class="bullet-edit">
          <textarea data-path="${sect}.${i}.bullets.${j}.text">${esc(b.text)}</textarea>
          <button class="x" data-del="${sect}.${i}.${j}" title="删除这条">×</button></div>`;
      });
      html += `<button class="add-line" data-add="${sect}.${i}">+ 加一条</button>`;
    });
  });
  html += `<div class="sec-title">自我评价</div>
    <div class="fld"><textarea rows="3" data-path="summary">${esc(p.summary)}</textarea></div>
    <div class="sec-title">技能（每组一行，格式：分组：技能1、技能2）</div>
    <div class="fld"><textarea rows="4" id="skillEdit">${esc((p.skills || []).map((g) => g.group + '：' + (g.items || []).join('、')).join('\n'))}</textarea></div>`;
  $('#profileForm').innerHTML = html;

  $$('[data-del]', $('#profileForm')).forEach((b) => {
    b.onclick = () => {
      const [sect, i, j] = b.dataset.del.split('.');
      S.profile[sect][+i].bullets.splice(+j, 1);
      openProfileModal();
    };
  });
  $$('[data-add]', $('#profileForm')).forEach((b) => {
    b.onclick = () => {
      const [sect, i] = b.dataset.add.split('.');
      S.profile[sect][+i].bullets.push({ text: '', evidence: '', metrics: [], status: 'missing' });
      openProfileModal();
    };
  });
  showModal('#modalProfile');
}

function saveProfileModal() {
  const form = $('#profileForm');
  $$('[data-path]', form).forEach((el) => {
    const path = el.dataset.path.split('.');
    let obj = S.profile;
    for (let i = 0; i < path.length - 1; i++) {
      const k = isNaN(+path[i]) ? path[i] : +path[i];
      obj = obj[k];
      if (!obj) return;
    }
    const last = path[path.length - 1];
    const key = isNaN(+last) ? last : +last;
    let v = el.value;
    if (path[0] === 'intent' && last === 'targetRoles') v = v.split(/[,，、]/).map((x) => x.trim()).filter(Boolean);
    obj[key] = v;
  });
  const sk = $('#skillEdit').value.split('\n').filter((x) => x.trim());
  S.profile.skills = sk.map((line) => {
    const [g, rest] = line.split(/[:：]/);
    return { group: (g || '技能').trim(), items: (rest || '').split(/[、,，]/).map((x) => x.trim()).filter(Boolean) };
  }).filter((g) => g.items.length);
  hideModal();
  renderAll();
  saveProfile();
  pushBot('<div>档案更新了，我重新读一遍。</div>');
  doLint(true);
}

/* ---------------------------------------------------------------- 证据链 */
function showEvidence() {
  const p = S.profile;
  const rows = [];
  (p.evidence || []).forEach((e) => rows.push({ claim: e.claim, source: e.source, status: e.status }));
  [...(p.internships || []), ...(p.projects || [])].forEach((it) => {
    (it.bullets || []).forEach((b) => {
      if (/\d/.test(b.text)) {
        const has = !!(b.evidence || '').trim();
        rows.push({ claim: b.text.slice(0, 46) + (b.text.length > 46 ? '…' : ''), source: b.evidence || '（缺）', status: has ? 'verified' : 'missing' });
      }
    });
  });
  if (!rows.length) return pushBot('目前没检测到需要挂证据的数字。');
  const miss = rows.filter((r) => r.status !== 'verified').length;
  pushBot(`<div>证据链：<b>${rows.length}</b> 个数字，<b>${miss}</b> 个还没有来源。导出前把这 ${miss} 个补上，面试才敢问。</div>
    <div class="cc"><div class="cc-head">证据链核验<span class="sub">${miss} 条待补</span></div>
    <div class="cc-body">${rows.slice(0, 14).map((r) => `<div class="evi"><span class="dot" style="background:${r.status === 'verified' ? '#2f6b4f' : '#b5341f'}"></span>
      <div class="grow"><div><b>${esc(r.claim)}</b></div>
      <div style="font-size:11.5px;color:#8b8479">来源：${esc(r.source)}</div></div>
      <span class="tag ${r.status === 'verified' ? 'green' : 'orange'}">${r.status === 'verified' ? '有证据' : '待补'}</span></div>`).join('')}</div>
    <div class="cc-foot"><span class="hint">在「编辑档案」里给每条加证据（台账 / 截图 / 报表日期）</span></div></div>`);
}

/* ---------------------------------------------------------------- 台账 */
async function loadApps() {
  try {
    const d = await api('/api/apps');
    S.apps = d.items || [];
    renderTrack();
  } catch (e) { /* 静默 */ }
}

function renderTrack() {
  const list = S.apps;
  const today = new Date();
  const days = (d) => (d ? Math.ceil((new Date(d + 'T23:59:59') - today) / 86400000) : null);
  const kpi = [
    ['总投递', list.length, ''],
    ['已投', list.filter((a) => a.status !== '待投').length, ''],
    ['进面', list.filter((a) => ['面试', 'Offer'].includes(a.status)).length, ''],
    ['7 天内截止', list.filter((a) => { const d2 = days(a.deadline); return d2 != null && d2 >= 0 && d2 <= 7; }).length, 's-soon'],
    ['平均匹配', list.filter((a) => a.match).length ? Math.round(list.reduce((a, b) => a + (b.match || 0), 0) / list.filter((a) => a.match).length) : '—', ''],
  ];
  $('#trackKpi').innerHTML = kpi.map(([l, n, c]) => `<div class="kpi"><div class="n ${c}">${n}</div><div class="l">${l}</div></div>`).join('');
  const tb = $('#appTable tbody');
  tb.innerHTML = list.map((a) => {
    const d = days(a.deadline);
    const soon = d != null && d >= 0 && d <= 7;
    // 公司名有链接时直接变成站外入口：投递台账最大的痛点是"想再看一眼 JD 要翻五层菜单"
    const co = a.url
      ? `<a class="app-link" href="${esc(a.url)}" target="_blank" rel="noopener noreferrer"
            title="${esc(a.url)}">${esc(a.company)}<i>↗</i></a>`
      : `${esc(a.company)}<button class="link-add" data-add-url title="给这条加个 JD 链接">＋链</button>`;
    return `<tr data-id="${a.id}">
      <td class="co-cell">${co}</td>
      <td>${esc(a.role)}</td>
      <td><select data-status>${['待投', '已投', '笔试', '面试', 'Offer', '已挂'].map((s) => `<option ${s === a.status ? 'selected' : ''}>${s}</option>`).join('')}</select></td>
      <td class="dead ${soon ? 'soon' : ''}">${esc(a.deadline) || '—'}${soon ? ` · ${d}天` : ''}</td>
      <td>${a.match || '—'}</td>
      <td style="color:#8b8479">${esc(a.note) || '—'}</td>
      <td><button class="del" data-del-app>删除</button></td>
    </tr>`;
  }).join('');
  $('#appEmpty').style.display = list.length ? 'none' : 'block';
  $$('#appTable [data-status]').forEach((sel) => {
    sel.onchange = async () => {
      const id = sel.closest('tr').dataset.id;
      const d = await api('/api/apps/' + id, { status: sel.value }, 'PATCH');
      S.apps = d.items; renderTrack(); toast('状态已更新');
    };
  });
  $$('#appTable [data-del-app]').forEach((b) => {
    b.onclick = async () => {
      const id = b.closest('tr').dataset.id;
      const d = await api('/api/apps/' + id, null, 'DELETE');
      S.apps = d.items; renderTrack(); toast('已删除');
    };
  });
  $$('#appTable [data-add-url]').forEach((b) => {
    b.onclick = async () => {
      const id = b.closest('tr').dataset.id;
      const raw = window.prompt('粘贴这个岗位的 JD 页面地址（http/https）', 'https://');
      if (!raw) return;
      try {
        const d = await api('/api/apps/' + id, { url: raw.trim() }, 'PATCH');
        S.apps = d.items; renderTrack();
        const ok = (d.items || []).find((x) => String(x.id) === String(id) && x.url);
        // 服务端会把 javascript: 之类的协议丢掉，这里必须如实反馈，
        // 否则用户以为自己贴上了，回头发现点不动
        toast(ok ? '链接已保存' : '这个地址不像网页链接，没保存');
      } catch (e) { toast(e.message, 3000); }
    };
  });
}

async function saveApp() {
  const body = {
    company: $('#appCompany').value.trim(),
    role: $('#appRole').value.trim(),
    status: $('#appStatus').value,
    deadline: $('#appDeadline').value,
    note: $('#appNote').value.trim(),
    url: ($('#appUrl') ? $('#appUrl').value.trim() : ''),
    match: S.match ? S.match.score : 0,
  };
  if (!body.company) return toast('公司名必填');
  const d = await api('/api/apps', body);
  S.apps = d.items;
  hideModal(); renderTrack();
  toast('已加入台账');
}

/* ---------------------------------------------------------------- 模型接入（双轨）
   两条轨道必须让用户一眼看清区别，否则「我的 Key」填了却发现还在扣点，
   是那种一旦发生就再也解释不清的信任事故。所以这个弹窗的第一行永远是
   「你现在用的是哪条轨道」。 */
function paintEngine(st) {
  const badge = $('#engineBadge');
  if (!badge || !st) return;
  if (st.source === 'user') {
    badge.textContent = '我的 Key · ' + (st.model || '');
    badge.title = st.note;
    badge.classList.remove('rules');
  } else if (st.mode === 'llm') {
    badge.textContent = '已接 ' + (st.model || '');
    badge.title = st.note;
    badge.classList.remove('rules');
  } else {
    badge.textContent = '规则引擎（离线可用）';
    badge.title = st.note || '';
    badge.classList.add('rules');
  }
}

function openModel() {
  showModal('#modalModel');
  refreshModel();
}

async function refreshModel() {
  const now = $('#modelNow');
  if (now) now.textContent = '读取中…';
  try {
    const d = await api('/api/llm');
    S.llmInfo = d;
    S.llm = d.status;
    paintEngine(d.status);
    renderModel(d);
  } catch (e) {
    if (now) now.innerHTML = `<span class="err">${esc(e.message)}</span>`;
  }
}

function renderModel(d) {
  const st = d.status || {};
  const now = $('#modelNow');
  const plat = d.platform || {};
  const mine = d.mine || null;
  const w = d.wallet || {};

  if (now) {
    if (st.source === 'user') {
      now.innerHTML = `<b>正在用你自己的 Key</b><span>${esc(st.model || '')} · 调用不扣点</span>`;
      now.className = 'model-now is-own';
    } else if (st.mode === 'llm') {
      now.innerHTML = `<b>平台额度</b><span>${esc(st.model || '')} · 按量扣点</span>`;
      now.className = 'model-now is-platform';
    } else {
      now.innerHTML = `<b>规则引擎</b><span>没有接入大模型，诊断/改写/追问都可用，只是不针对你的 JD 定制</span>`;
      now.className = 'model-now is-rules';
    }
  }

  const ph = $('#modelPlatHint');
  if (ph) {
    ph.innerHTML = plat.available
      ? `平台已接入 <b>${esc(plat.model || '')}</b>。AI 深度能力按量扣点，1 点约等于一次完整分析。`
      : '平台还没接入大模型。切到上面「用我自己的 Key」自己接一个——功能完全一样，而且不扣点。';
  }
  const mw = $('#modelWallet');
  if (mw) {
    if (d.anonymous) {
      mw.innerHTML = `<span class="dim">还没登录。登录后才能记点数和保存 Key。</span>`;
    } else {
      mw.innerHTML = `
        <div class="mw-i"><b>${w.points || 0}</b><span>剩余点数</span></div>
        <div class="mw-i"><b>${w.member ? '会员' : '未开通'}</b><span>导出权限</span></div>`;
    }
  }

  // ---- 我的 Key
  const sel = $('#llmPreset');
  if (sel && !sel.dataset.done) {
    sel.dataset.done = '1';
    sel.innerHTML = (d.presets || []).map((p) => `<option value="${esc(p.key)}">${esc(p.label)}</option>`).join('');
  }
  if (sel) {
    sel.onchange = () => {
      const p = (d.presets || []).find((x) => x.key === sel.value);
      if (!p) return;
      if (p.base_url) $('#llmBase').value = p.base_url;
      if (p.model) $('#llmModel').value = p.model;
      if ($('#llmTip')) $('#llmTip').textContent = p.hint || '';
    };
  }
  if (mine) {
    if ($('#llmBase')) $('#llmBase').value = mine.base_url || '';
    if ($('#llmModel')) $('#llmModel').value = mine.model || '';
    if ($('#llmKey')) $('#llmKey').placeholder = '已保存 ····' + (mine.keyTail || '');
    if (sel && mine.provider) sel.value = mine.provider;
    if ($('#llmTip')) {
      $('#llmTip').className = 'hint' + (mine.lastErr ? ' err' : '');
      $('#llmTip').textContent = mine.lastErr
        ? '上次调用失败：' + mine.lastErr
        : (mine.lastOk ? '上次测试通过 · ' + new Date(mine.lastOk * 1000).toLocaleString() : '已保存，尚未测试过');
    }
  } else if ($('#llmKey')) {
    $('#llmKey').placeholder = 'sk-...';
    /* 没存过 Key 的时候，下拉框已经显示「DeepSeek」了，地址栏却是空的——
       这会让第一次用的人以为自己得去查接口文档。
       下拉框代表什么，就把那家的地址和模型名填进去；用户想改再改。 */
    const p0 = (d.presets || []).find((x) => sel && x.key === sel.value);
    if (p0) {
      if ($('#llmBase') && !$('#llmBase').value) $('#llmBase').value = p0.base_url || '';
      if ($('#llmModel') && !$('#llmModel').value) $('#llmModel').value = p0.model || '';
      if ($('#llmTip') && p0.hint) $('#llmTip').textContent = p0.hint;
    }
  }

  const test = $('#btnLlmTest');
  if (test) test.onclick = doLlmTest;
  const save = $('#btnLlmSave');
  if (save) save.onclick = doLlmSave;
  const clr = $('#btnLlmClear');
  if (clr) clr.onclick = doLlmClear;
  const pay = $('#btnModelPay');
  if (pay) pay.onclick = () => { hideModal(); openPay(); };
}

function modelTab(which) {
  $$('#modelTabs button').forEach((b) => b.classList.toggle('is-active', b.dataset.mtab === which));
  if ($('#modelPanePlatform')) $('#modelPanePlatform').hidden = which !== 'platform';
  if ($('#modelPaneOwn')) $('#modelPaneOwn').hidden = which !== 'own';
}

function llmCfgFromForm() {
  return {
    provider: ($('#llmPreset') || {}).value || 'custom',
    base_url: ($('#llmBase') || {}).value.trim(),
    model: ($('#llmModel') || {}).value.trim(),
    api_key: ($('#llmKey') || {}).value.trim(),
  };
}

async function doLlmTest() {
  const tip = $('#llmTip');
  const c = llmCfgFromForm();
  if (!c.base_url || !c.model) return toast('接口地址和模型名要填');
  if (tip) { tip.className = 'hint'; tip.textContent = '正在连…（大约几秒）'; }
  try {
    const d = await api('/api/llm/test', c);
    if (tip) {
      tip.className = 'hint' + (d.passed ? '' : ' err');
      tip.textContent = d.passed
        ? `通了 · ${d.model} · 回「${d.reply || '可用'}」· ${d.ms}ms · ${d.tokens || '?'} tokens`
        : '没通：' + (d.error || '未知错误') + (d.detail ? '（' + d.detail.slice(0, 80) + '）' : '');
    }
    if (!d.passed) toast('连通失败，看下面的原因', 3000);
  } catch (e) {
    if (tip) { tip.className = 'hint err'; tip.textContent = e.message; }
  }
}

async function doLlmSave() {
  const c = llmCfgFromForm();
  if (!c.base_url || !c.model) return toast('接口地址和模型名要填');
  if (!c.api_key && !(S.llmInfo && S.llmInfo.mine)) return toast('填一下 Key');
  try {
    const d = await api('/api/llm/key', c);
    S.llm = d.status;
    paintEngine(d.status);
    toast(d.msg || '已保存');
    refreshModel();
  } catch (e) {
    if (e.code === 'need_login') return openAuth(() => doLlmSave());
    toast(e.message, 3200);
  }
}

async function doLlmClear() {
  if (!window.confirm('删除已保存的 Key？之后 AI 调用会回到平台额度（按量扣点）。')) return;
  try {
    const d = await api('/api/llm/key/delete', {});
    S.llm = d.status;
    paintEngine(d.status);
    if ($('#llmKey')) $('#llmKey').value = '';
    toast(d.msg || '已删除');
    refreshModel();
  } catch (e) {
    if (e.code === 'need_login') return openAuth();
    toast(e.message, 3000);
  }
}

/* ---------------------------------------------------------------- 视图切换与事件 */
function switchView(v) {
  $$('.view').forEach((el) => el.classList.remove('is-active'));
  const el = $('#view-' + v);
  if (!el) return;
  el.classList.add('is-active');
  $$('#tabs button').forEach((b) => b.classList.toggle('is-active', b.dataset.view === v));
  if (location.hash.slice(1) !== v) history.replaceState(null, '', '#' + v);
  if (v === 'studio') setTimeout(fitPaper, 40);
  if (v === 'track') loadApps();
  if (v === 'drill') { renderDrill(); if (!S.qs) doInterview(true); }  // 新视图在 views2.js 里，用钩子接入，避免主文件继续膨胀
  if (window.JH2 && JH2.onView) JH2.onView(v);
}

function showModal(sel) { $('#mask').classList.add('show'); $(sel).classList.add('show'); }
function hideModal() {
  $('#mask').classList.remove('show');
  $$('.modal').forEach((m) => m.classList.remove('show'));
}

function interpret(text) {
  pushUser(text);
  const t = text;
  if (/导出|pdf|打印/i.test(t)) return exportPdf();
  if (/面试|追问|模拟/i.test(t)) return doInterview();
  if (/jd|岗位|匹配/i.test(t) && t.length < 30) return openJdModal();
  if (/诊断|打分|评分|怎么样/i.test(t)) return doLint();
  if (/改写|重写|优化|润色/i.test(t)) {
    if (S.lint) return rewriteWorst();
    return doLint(true).then(rewriteWorst);
  }
  if (/配色|颜色|换主题/i.test(t)) {
    const keys = THEMES.map((x) => x.key);
    const i = keys.indexOf(S.profile.preferences.theme);
    S.profile.preferences.theme = keys[(i + 1) % keys.length];
    renderPreview(); saveProfile();
    return pushBot(`换成「${S.profile.preferences.theme}」配色了。再点一次继续换。`);
  }
  if (/双栏|单栏|模板/i.test(t)) {
    S.profile.preferences.template = /双栏/.test(t) ? 'two' : 'single';
    renderPreview(); saveProfile();
    return pushBot(`已切到${/双栏/.test(t) ? '双栏' : '单栏'}模板。`);
  }
  // 兜底：给可执行的下一步，而不是装作听懂了
  pushBot(`<div>这句话我还接不住——目前我能确定的动作是这几件，你点一个就行：</div>
    <div style="margin-top:6px">
      <span class="tag blue">一键诊断</span><span class="tag blue">贴 JD 定向</span>
      <span class="tag blue">改写某一条</span><span class="tag blue">生成追问</span><span class="tag blue">导出 PDF</span>
    </div>
    <div style="margin-top:8px;font-size:12px;color:#8b8479">更细的自然语言指令（例如"帮我把第三段改得更像结果导向"）需要接入大模型才会更自然；现在你也可以直接点右侧每一条问题后面的「改写这条」。</div>`);
}

function init() {
  $('#tabs').onclick = (e) => { const b = e.target.closest('button'); if (b) switchView(b.dataset.view); };
  $('#sideTabs').onclick = (e) => { const b = e.target.closest('button'); if (b) { S.sideTab = b.dataset.stab; renderSide(); } };
  $('#chips').onclick = (e) => {
    const b = e.target.closest('[data-act]'); if (!b) return;
    const act = b.dataset.act;
    if (act === 'import') openImport();
    else if (act === 'jd') openJdModal();
    else if (act === 'lint') doLint();
    else if (act === 'rewrite-all') rewriteWorst();
    else if (act === 'interview') doInterview();
    else if (act === 'export') exportPdf();
  };
  $('#send').onclick = () => {
    const v = $('#input').value.trim();
    if (!v) return;
    $('#input').value = '';
    interpret(v);
  };
  $('#input').onkeydown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#send').click(); }
  };
  $('#btnExport').onclick = exportPdf;
  $('#btnEvidence').onclick = showEvidence;
  $('#btnProfile').onclick = openProfileModal;
  $('#btnReset').onclick = () => {
    localStorage.removeItem('jh_sid');
    location.reload();
  };
  $('#engineBadge').onclick = openModel;
  $('#btnDoParse').onclick = doParse;
  $('#btnDoJd').onclick = doJd;
  $('#btnSaveProfile').onclick = saveProfileModal;
  $('#modelTabs').onclick = (e) => {
    const b = e.target.closest('[data-mtab]');
    if (b) modelTab(b.dataset.mtab);
  };

  /* ---- 账户 / 付费 ---- */
  $('#btnAcct').onclick = () => {
    if (S.anonymous) return openAuth();
    pushBot(`<div><b>${esc(S.user.email)}</b> · 剩余 <b>${S.credits}</b> 次导出</div>
      <div style="margin-top:6px;font-size:12px;color:#8b8479">注册于 ${new Date(S.user.created_at * 1000).toLocaleDateString()}。想加额度点下面。</div>
      <div class="tag-row"><span class="tag blue" id="gotoPay">买额度</span><span class="tag blue" id="gotoLogout">退出登录</span></div>`);
    const p = $('#gotoPay'), l = $('#gotoLogout');
    if (p) p.onclick = openPay;
    if (l) l.onclick = async () => {
      await api('/api/auth/logout', {});
      location.reload();
    };
  };
  $('#btnAuthGo').onclick = doAuth;
  $('#btnAuthToggle').onclick = toggleAuth;
  $('#authPw').onkeydown = (e) => { if (e.key === 'Enter') doAuth(); };
  $('#btnRedeem').onclick = doRedeem;
  $('#codeInput').onkeydown = (e) => { if (e.key === 'Enter') doRedeem(); };
  $('#btnLead').onclick = doLead;
  $('#leadContact').onkeydown = (e) => { if (e.key === 'Enter') doLead(); };
  $('#plans').onclick = (e) => {
    const el = e.target.closest('[data-plan]');
    if (!el) return;
    $$('.plan').forEach((x) => x.classList.remove('picked'));
    el.classList.add('picked');
    $('#leadContact').focus();
    $('#leadTip').textContent = `选了「${el.querySelector('.plan-name').textContent}」。留个联系方式，我直接发你码。`;
  };

  $('#btnAddApp').onclick = () => {
    $('#appCompany').value = ''; $('#appRole').value = (S.profile.intent.targetRoles || [])[0] || '';
    // 上一轮填过的链接必须清掉，否则新记录会静默继承上一个岗位的 JD
    if ($('#appUrl')) $('#appUrl').value = '';
    showModal('#modalApp');
  };
  $('#btnSaveApp').onclick = saveApp;
  $('#btnGenQ').onclick = () => { S.qs = null; doInterview(true); };
  $('#fileInput').onchange = () => {
    const f = $('#fileInput').files[0];
    if (!f) return;
    if (/\.pdf$/i.test(f.name)) return toast('PDF 请手动复制文字粘贴，暂不支持直接解析', 3200);
    const r = new FileReader();
    r.onload = async () => {
      const b64 = String(r.result).split(',')[1];
      $('#importTip').textContent = '已读取：' + f.name + '，正在提取文字…';
      try {
        const d = await api('/api/parse', { name: f.name, b64 });
        $('#importText').value = d.profile ? '(文件已解析，点「开始解析」确认结构)' : '';
        $('#importTip').textContent = '文件解析成功，点「开始解析」';
        S._parsed = d.profile;
      } catch (e) {
        $('#importTip').textContent = e.message;
      }
    };
    r.readAsDataURL(f);
  };
  $$('.swatch').forEach((s, i) => { });
  $('#swatches').innerHTML = THEMES.map((t) => `<span class="swatch" data-theme="${t.key}" style="background:${t.hex}" title="${t.key}"></span>`).join('');
  $('#swatches').onclick = (e) => {
    const s = e.target.closest('[data-theme]'); if (!s) return;
    S.profile.preferences.theme = s.dataset.theme;
    renderPreview(); saveProfile();
  };
  $$('.tpl-btn').forEach((b) => {
    b.onclick = () => {
      $$('.tpl-btn').forEach((x) => x.classList.remove('is-active'));
      b.classList.add('is-active');
      S.profile.preferences.template = b.dataset.tpl;
      renderPreview(); saveProfile();
    };
  });
  $$('[data-font]').forEach((b) => {
    b.onclick = () => {
      const cur = S.profile.preferences.fontPct || 100;
      const next = Math.min(125, Math.max(85, cur + (+b.dataset.font)));
      S.profile.preferences.fontPct = next;
      $('#fontVal').textContent = next + '%';
      renderPreview(); saveProfile();
    };
  });
  $('#mask').onclick = hideModal;
  $$('[data-close]').forEach((b) => { b.onclick = hideModal; });
  window.addEventListener('resize', fitPaper);
  window.addEventListener('keydown', (e) => { if (e.key === 'Escape') hideModal(); });
  // 可直接分享的锚点：#auth 登录、#pay 价格 —— 也方便排查时直达
  const h0 = (location.hash || '').replace('#', '');
  if (h0 === 'pay') setTimeout(openPay, 500);
  if (h0 === 'auth') setTimeout(() => openAuth(), 500);
  boot();
}

init();
