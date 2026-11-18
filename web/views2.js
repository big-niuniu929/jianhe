/* =================================================================
   落笔 · 岗位地图 / 实习避坑
   =================================================================
   为什么单独一个文件：app.js 已经管了工坊、台账、面试三块，
   再塞两块会让它变成一个谁都不敢改的文件。这里通过 window.JH2.onView
   被 switchView 调用，共用 app.js 的 S / api / 渲染工具，不重复实现。

   两个视图的共同设计原则：
   1. 首屏就给答案，不要让用户先填三个字段。点常用岗位即可出结果。
   2. 长内容用 <details> 折叠，默认只露出「结论 + 追问风险」。
   3. 所有 AI 按钮都明确标注「⚡ 扣点」，且失败时自动降级到内置知识库，
      不让用户看到一个空白页。
   ================================================================= */
(function () {
  'use strict';

  const S2 = {
    role: '',
    jd: '',
    ind: null,
    pit: null,
    pitStage: '',
    pitQ: '',
    pitRole: '',
    con: null,
    conCat: '',
    conFit: '',
    conQ: '',
    bound: false,
    busy: false,
  };

  /**
   * 取 app.js 里的全局状态 S。
   * 不能写 window.S —— app.js 用的是 const S，const 声明不会挂到 window 上，
   * 只能靠脚本作用域访问。这里用函数包一层，顺便兜住 app.js 没加载成功的情况。
   */
  function st() {
    try { return S; } catch (e) { return {}; }
  }

  const SEV_CLASS = { '高': 'sev-high', '中': 'sev-mid', '低': 'sev-low' };
  const STAGE_LABEL = {
    find: '找实习时', onboard: '入职前后', work: '工作中',
    convert: '转正环节', leave: '结束时',
  };

  /* ------------------------------------------------------------ 工具 */
  function famLabel(key) {
    const f = (st().families || []).find((x) => x.key === key);
    return f ? f.label : '';
  }

  /** AI 失败时统一分流：点数不够去付费，未登录去注册，其它只提示 */
  function handleAiError(e) {
    if (e.code === 'need_points') {
      if (st()) S.wallet = e.data && e.data.wallet ? e.data.wallet : S.wallet;
      renderAcct();
      toast('点数用完了，充一点就能用 AI 深度能力', 3000);
      openPay();
      return true;
    }
    if (e.code === 'need_login') {
      openAuth();
      return true;
    }
    if (e.code === 'no_llm') {
      toast('服务端还没接大模型，先用内置知识库版本', 4000);
      return true;
    }
    return false;
  }

  /* ============================================================
     视图 1 · 岗位地图
     ============================================================ */
  async function loadIndustry(useAi) {
    const role = ($('#indRole').value || '').trim();
    const jd = ($('#indJd').value || '').trim();
    if (!role && !jd) {
      toast('先填个岗位名，或粘一段 JD', 2400);
      return;
    }
    S2.role = role;
    S2.jd = jd;
    const body = $('#indBody');
    body.innerHTML = `<div class="empty-state">${useAi ? '正在读你的 JD…' : '正在翻知识库…'}</div>`;

    try {
      let r, note = '';
      if (useAi) {
        const d = await api('/api/ai/industry', { role, jd });
        r = d.result;
        note = d.note || '';
        if (d.meter) applyMe({ wallet: null, credits: null });
        if (d.meter && d.meter.left != null) {
          S.wallet = Object.assign({}, S.wallet || {}, { points: d.meter.left });
          renderAcct();
        }
        if (d.mode === 'fallback') toast(d.note || 'AI 这次没成功，已降级到内置知识库', 4000);
      } else {
        const q = new URLSearchParams({ role, jd });
        const d = await api('/api/industry?' + q.toString());
        r = d.result;
        note = d.note || '';
      }
      S2.ind = r;
      $('#indMode').textContent = useAi ? 'AI 定制' : '内置知识库';
      $('#indMode').classList.toggle('ai', !!useAi);
      renderIndustry(r, note);
      track('industry', { family: r.family, ai: !!useAi });
      if (!useAi && r.canFill) tryFill(role, jd);
    } catch (e) {
      if (handleAiError(e)) { body.innerHTML = ''; loadIndustry(false); return; }
      body.innerHTML = `<div class="empty-state err">${esc(e.message)}</div>`;
    }
  }

  /**
   * 地图外岗位的后台补齐。
   *
   * 为什么不放在 /api/industry 里同步做：那个接口现在是 30 毫秒出结果。
   * 为一个不认识的岗位把所有人的首屏拖到 8 秒，不值。
   * 所以顺序是——先把内置的通用分析渲染出来（用户马上有东西可看），
   * 再在后台补，补完换掉正文。补失败就留一行说明，页面不空。
   */
  async function tryFill(role, jd) {
    const info = st().llmInfo || {};
    const ready = !!(info.mine || (info.status && info.status.enabled));
    if (!ready) {
      fillSlot(`<div class="fill-line">这个岗位不在内置地图里。
        <button class="fill-btn" id="fillToModel">接上模型</button>
        后可以自动补一份，补过之后全站共用，不扣点。</div>`);
      const b = $('#fillToModel');
      if (b) b.onclick = () => { if (typeof openModel === 'function') openModel(); };
      return;
    }
    fillSlot(`<div class="fill-line">这个岗位不在内置地图里 · 正在让模型补一份…</div>`);
    try {
      const d = await api('/api/industry/fill', { role, jd });
      if (!d.ok) {
        fillSlot(`<div class="fill-line">${esc(d.error || '这次没补上')}</div>`);
        return;
      }
      if (!d.result || d.result.family === 'generic') {
        fillSlot(`<div class="fill-line">模型也没能给这个岗位定性，先用通用方法看。</div>`);
        return;
      }
      S2.ind = d.result;
      renderIndustry(d.result, d.note || '');
      track('industry_fill', { family: d.result.family, cached: !!d.cached });
    } catch (e) {
      fillSlot(`<div class="fill-line">${esc(e.message)}</div>`);
    }
  }

  function fillSlot(html) {
    const el = $('#indFill');
    if (el) el.innerHTML = html;
  }

  function renderIndustry(r, note) {
    const kpi = (r.kpi || []).map((k) => `<div class="kpi-i">${esc(k)}</div>`).join('');
    const rhythm = (r.rhythm || []).map((x) =>
      `<div class="rhythm-row"><span class="rhythm-when">${esc(x.when)}</span><span>${esc(x.what)}</span></div>`).join('');

    const ind = r.industry || {};
    const org = r.org || {};

    const chain = `
      <div class="chain-col">
        <div class="chain-h">上游 · 给你输入</div>
        ${(ind['上游'] || []).map((x) => `<div class="chain-item">${esc(x)}</div>`).join('') || '<div class="chain-item dim">—</div>'}
      </div>
      <div class="chain-col self">
        <div class="chain-h">你所在的位置</div>
        <div class="chain-self">${esc(ind['自身'] || r.oneLiner)}</div>
      </div>
      <div class="chain-col">
        <div class="chain-h">下游 · 拿走你的产出</div>
        ${(ind['下游'] || []).map((x) => `<div class="chain-item">${esc(x)}</div>`).join('') || '<div class="chain-item dim">—</div>'}
      </div>`;

    const orgRows = [
      ['组织内 · 上游给你', org['上游给你']],
      ['组织内 · 你交给谁', org['你交给谁']],
      ['平级协作', org['平级协作']],
    ].map(([k, v]) => `
      <div class="org-row">
        <span class="org-k">${k}</span>
        <span class="org-v">${(v || []).map((x) => `<i>${esc(x)}</i>`).join('') || '—'}</span>
      </div>`).join('');

    const vocab = (r.vocab || []).map((v, i) => `
      <details class="vitem" ${v.inJd ? 'open' : ''}>
        <summary>
          <span class="vword">${esc(v.w)}</span>
          ${v.inJd ? '<span class="vflag">JD 要求</span>' : ''}
          ${v.level ? `<span class="vlevel">${esc(v.level)}</span>` : ''}
          <span class="vdef">${esc(v.def)}</span>
        </summary>
        <div class="vbody">
          ${v.why ? `<p><b>为什么这么叫</b>${esc(v.why)}</p>` : ''}
          ${v.use ? `<p class="good"><b>面试怎么说加分</b>${esc(v.use)}</p>` : ''}
          ${v.trap ? `<p class="bad"><b>用错会怎样</b>${esc(v.trap)}</p>` : ''}
        </div>
      </details>`).join('');

    const levels = (r.levels || []).map((l) => `
      <div class="lvl">
        <div class="lvl-t">${esc(l.tier)}</div>
        <div class="lvl-ok">${esc(l.ok)}</div>
        <div class="lvl-no">${esc(l.notOk)}</div>
      </div>`).join('');

    const probes = (r.probes || []).map((p, i) => `
      <div class="probe">
        <div class="probe-q"><b>${i + 1}</b>${esc(p.q)}</div>
        <div class="probe-a"><span>过关的回答</span>${esc(p.expect)}</div>
        <div class="probe-w"><span>他在验什么</span>${esc(p.why)}</div>
      </div>`).join('');

    const tools = (r.tools || []).map((t) => `<span class="tag">${esc(t)}</span>`).join('');

    $('#indBody').innerHTML = `
      <div class="card ind-hero">
        <div class="hero-top">
          <div class="hero-main">
            <div class="hero-label">${esc(r.familyLabel)}</div>
            <h3>${esc(r.oneLiner)}</h3>
            <div class="rhythm">${rhythm}</div>
          </div>
          <div class="hero-meta">
            <div><span>汇报</span>${esc(r.reportTo || '—')}</div>
            <div><span>被考核</span>${kpi || '—'}</div>
          </div>
        </div>
        ${note ? `<div class="note-line">${esc(note)}</div>` : ''}
        ${(r.uncertain && r.uncertain.length) ? `<div class="note-line warn">不确定的部分：${r.uncertain.map(esc).join('；')}</div>` : ''}
        <div id="indFill"></div>
      </div>

      <div class="card">
        <div class="card-h">行业上下游 <span class="sub">这个岗位靠什么活着</span></div>
        <div class="chain">${chain}</div>
        <div class="pos-note">${esc(ind.position || '')}</div>
        <div class="org-block">${orgRows}</div>
        ${org['现实'] ? `<div class="pos-note soft">${esc(org['现实'])}</div>` : ''}
      </div>

      <div class="card">
        <div class="card-h">专业词汇 <span class="sub">${(r.vocab || []).length} 条 · 点开看释义与翻车方式</span></div>
        <div class="vocab-list">${vocab}</div>
      </div>

      <div class="card">
        <div class="card-h">诚实边界 <span class="sub">这个阶段能写什么，不能写什么</span></div>
        <div class="levels">${levels}</div>
      </div>

      <div class="card">
        <div class="card-h">会被问穿的细节题 <span class="sub">真做过能秒答，编的人必卡</span></div>
        <div class="probes">${probes}</div>
      </div>

      <div class="card">
        <div class="card-h">乱编检测 <span class="sub">贴你写的句子，看哪句会被追问到崩</span></div>
        <textarea id="auditText" rows="3" placeholder="例：独立负责电商平台自营店铺从 0 到 1 搭建私域体系，转化率提升 320%，单月新增 50 万用户"></textarea>
        <div class="audit-actions">
          <button class="primary" id="btnAudit">检测</button>
          <span class="hint">规则判断，不扣点。它只标出风险，不判定你在造假。</span>
        </div>
        <div id="auditOut"></div>
      </div>

      <div class="card">
        <div class="card-h">工具清单 <span class="sub">这个岗位人手上真会开的软件</span></div>
        <div class="tag-row">${tools || '<span class="dim">—</span>'}</div>
      </div>`;

    const btn = $('#btnAudit');
    if (btn) btn.onclick = doAudit;
    const ta = $('#auditText');
    if (ta) ta.onkeydown = (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) doAudit(); };
  }

  async function doAudit() {
    const text = ($('#auditText').value || '').trim();
    if (!text) return;
    const out = $('#auditOut');
    out.innerHTML = '<div class="dim" style="padding:8px 0">正在过规则…</div>';
    try {
      const d = await api('/api/audit', { text, family: (S2.ind && S2.ind.family) || '', role: S2.role });
      const r = d.result;
      if (!r.riskCount) {
        out.innerHTML = `<div class="audit-ok">没命中高风险表述。但仍建议对着上面的细节题自测一遍。</div>`;
        return;
      }
      out.innerHTML = `
        <div class="audit-sum">命中 <b>${r.riskCount}</b> 处高风险表述</div>
        ${r.risks.map((x) => `
          <div class="risk">
            <div class="risk-p">${esc(x.problem)}</div>
            ${x.quote ? `<div class="risk-q">「${esc(x.quote)}」</div>` : ''}
            <div class="risk-w"><b>会被怎么问</b>${esc(x.why)}</div>
            <div class="risk-f"><b>怎么改</b>${esc(x.fix)}</div>
          </div>`).join('')}
        <div class="audit-note">${esc(r.note)}</div>`;
    } catch (e) {
      out.innerHTML = `<div class="audit-ok err">${esc(e.message)}</div>`;
    }
  }

  /* ============================================================
     视图 2 · 实习避坑
     ============================================================ */
  async function loadPitfalls(useAi) {
    const role = ($('#pitRole').value || '').trim();
    S2.pitRole = role || S2.pitRole;
    const body = $('#pitBody');
    body.innerHTML = `<div class="empty-state">${useAi ? '正在找这个岗位特有的坑…' : '正在整理…'}</div>`;

    try {
      let d;
      if (useAi) {
        if (!role) { toast('AI 补充需要先填岗位', 2600); body.innerHTML = ''; return loadPitfalls(false); }
        d = await api('/api/ai/pitfalls', { role });
        if (d.meter && d.meter.left != null) {
          S.wallet = Object.assign({}, S.wallet || {}, { points: d.meter.left });
          renderAcct();
        }
        if (d.mode === 'fallback') toast(d.note || 'AI 这次没成功，已降级到内置坑库', 4000);
      } else {
        const q = new URLSearchParams({ role });
        d = await api('/api/pitfalls?' + q.toString());
      }
      S2.pit = d;
      $('#pitMode').textContent = useAi ? 'AI 补充' : '内置坑库';
      $('#pitMode').classList.toggle('ai', !!useAi);
      renderPitfalls(d, role);
      track('pitfall', { family: d.family, ai: !!useAi });
    } catch (e) {
      if (handleAiError(e)) { body.innerHTML = ''; loadPitfalls(false); return; }
      body.innerHTML = `<div class="empty-state err">${esc(e.message)}</div>`;
    }
  }

  function renderPitfalls(d, role) {
    const items = d.items || [];
    const filtered = items.filter((x) => {
      if (S2.pitStage && x.stage !== S2.pitStage) return false;
      const kw = (S2.pitQ || '').trim().toLowerCase();
      if (!kw) return true;
      const blob = [x.title, x.scene, x.signal, x.avoid, x.fix, x.write, x.scope]
        .join(' ').toLowerCase();
      return kw.split(/\s+/).filter(Boolean).every((w) => blob.includes(w));
    });

    const checklist = (d.checklist || []).map((c) => `
      <div class="ck">
        <div class="ck-q">☐ ${esc(c.q)}</div>
        <div class="ck-w">${esc(c.why)}</div>
      </div>`).join('');

    const cards = filtered.map((x) => `
      <details class="pit ${SEV_CLASS[x.severity] || ''}">
        <summary>
          <span class="pit-sev">${esc(x.severity)}</span>
          <span class="pit-title">${esc(x.title)}</span>
          <span class="pit-stage">${esc(STAGE_LABEL[x.stage] || x.stage)}</span>
        </summary>
        <div class="pit-body">
          ${x.scope ? `<div class="pit-scope">${esc(x.scope)}</div>` : ''}
          <div class="pit-b"><b>什么场景</b>${esc(x.scene)}</div>
          <div class="pit-b sig"><b>早期信号</b>${esc(x.signal)}</div>
          <div class="pit-b ok"><b>事前怎么避</b>${esc(x.avoid)}</div>
          <div class="pit-b fix"><b>已经踩了怎么救</b>${esc(x.fix)}</div>
          <div class="pit-b wr"><b>这段经历简历上怎么写</b>${esc(x.write)}</div>
        </div>
      </details>`).join('');

    $('#pitBody').innerHTML = `
      <div class="card ck-card">
        <div class="card-h">入职前必问的 ${(d.checklist || []).length} 句话 <span class="sub">问完这几句，一半的坑就没了</span></div>
        <div class="cks">${checklist}</div>
      </div>
      <div class="card">
        <div class="card-h">
          ${role ? `<span class="pit-for">${esc(role)}</span> 相关的坑` : '常见坑'}
          <span class="sub">${filtered.length} 条 · 点开看怎么救</span>
        </div>
        <div class="pits">${cards || '<div class="empty-state">这个筛选下没有结果，换个关键词试试。</div>'}</div>
      </div>`;
  }

  /* ============================================================
     视图 3 · 竞赛汇总
     ============================================================ */
  const CON_STATE_CLASS = {
    open: 'open', soon: 'soon', closed: 'closed', offyear: 'off',
  };

  async function loadContests() {
    const body = $('#conBody');
    if (!body) return;
    if (!S2.con) body.innerHTML = '<div class="empty-state">正在整理…</div>';
    try {
      const q = new URLSearchParams({
        cat: S2.conCat || '', fit: S2.conFit || '', q: S2.conQ || '',
      });
      const d = await api('/api/contests?' + q.toString());
      S2.con = d;
      renderContests(d);
    } catch (e) {
      body.innerHTML = `<div class="empty-state err">${esc(e.message)}</div>`;
    }
  }

  function renderContests(d) {
    const items = d.items || [];
    const meta = d.meta || {};
    const m = $('#conMeta');
    if (m) m.textContent = meta.openNow + ' / ' + meta.total + ' 项正在报名';

    const cats = $('#conCats');
    if (cats && !cats.dataset.done) {
      cats.dataset.done = '1';
      cats.innerHTML = `<button class="fam-chip on" data-cat="">全部类别</button>`
        + (meta.cats || []).map((c) => `<button class="fam-chip" data-cat="${esc(c)}">${esc(c)}</button>`).join('');
      cats.onclick = (e) => {
        const b = e.target.closest('[data-cat]');
        if (!b) return;
        S2.conCat = b.dataset.cat;
        Array.from(cats.children).forEach((x) => x.classList.toggle('on', x === b));
        loadContests();
      };
    }
    const fits = $('#conFits');
    if (fits && !fits.dataset.done) {
      fits.dataset.done = '1';
      const fl = meta.fits || {};
      fits.innerHTML = `<button class="fam-chip on" data-fit="">全部方向</button>`
        + Object.keys(fl).map((k) => `<button class="fam-chip" data-fit="${esc(k)}">${esc(fl[k])}</button>`).join('');
      fits.onclick = (e) => {
        const b = e.target.closest('[data-fit]');
        if (!b) return;
        S2.conFit = b.dataset.fit;
        Array.from(fits.children).forEach((x) => x.classList.toggle('on', x === b));
        loadContests();
      };
    }
    const note = $('#conNote');
    if (note) note.textContent = (meta.note || '') + ' ' + (meta.source || '');

    $('#conBody').innerHTML = items.length ? items.map((c) => {
      const st = c.status || {};
      const win = (c.months || []).join('–');
      const link = c.link
        ? `<a class="con-link" href="${esc(c.link)}" target="_blank" rel="noopener noreferrer">官网 ↗</a>`
        : `<span class="con-nolink">官网请在教务处通知里搜赛事全名</span>`;
      return `<article class="con ${CON_STATE_CLASS[st.state] || ''}">
        <div class="con-top">
          <span class="con-state">${esc(st.label || '')}</span>
          <span class="con-hint">${esc(st.hint || '')}</span>
          <span class="con-cat">${esc(c.cat)}</span>
          ${c.checked ? '<span class="con-checked" title="官网链接已人工核对">已核</span>' : ''}
        </div>
        <h3>${esc(c.name)}</h3>
        ${c.aka ? `<div class="con-aka">${esc(c.aka)}</div>` : ''}
        <dl class="con-meta">
          <div><dt>主办</dt><dd>${esc(c.org)}</dd></div>
          <div><dt>报名</dt><dd>${esc(win)} 月（往年规律）</dd></div>
          <div><dt>赛程</dt><dd>${esc(c.runAt)}</dd></div>
        </dl>
        <p class="con-value">${esc(c.value)}</p>
        <p class="con-tip"><b>怎么打</b>${esc(c.tip)}</p>
        <div class="con-foot">
          <span class="con-fit">${(c.fitLabels || []).map((f) => `<i>${esc(f)}</i>`).join('')}</span>
          ${link}
        </div>
      </article>`;
    }).join('') : '<div class="empty-state">这个筛选下没有赛事，换个词试试。</div>';
  }

  /* ============================================================
     绑定与接入
     ============================================================ */
  function renderFamChips() {
    const box = $('#famChips');
    if (!box) return;
    const fams = (st().families) || [];
    box.innerHTML = fams.map((f) =>
      `<button class="fam-chip" data-role="${esc(f.label)}">${esc(f.label)}</button>`).join('');
    box.onclick = (e) => {
      const b = e.target.closest('.fam-chip');
      if (!b) return;
      $('#indRole').value = b.dataset.role;
      loadIndustry(false);
    };
  }

  function bind() {
    if (S2.bound) return;
    S2.bound = true;

    const go = $('#btnIndGo');
    if (go) go.onclick = () => loadIndustry(false);
    const ai = $('#btnIndAi');
    if (ai) ai.onclick = () => loadIndustry(true);
    const role = $('#indRole');
    if (role) role.onkeydown = (e) => { if (e.key === 'Enter') loadIndustry(false); };

    const pgo = $('#btnPitGo');
    if (pgo) pgo.onclick = () => loadPitfalls(false);
    const pai = $('#btnPitAi');
    if (pai) pai.onclick = () => loadPitfalls(true);
    const prole = $('#pitRole');
    if (prole) prole.onkeydown = (e) => { if (e.key === 'Enter') loadPitfalls(false); };

    const st = $('#stageTabs');
    if (st) st.onclick = (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      S2.pitStage = b.dataset.stage || '';
      Array.from(st.children).forEach((x) => x.classList.toggle('is-active', x === b));
      if (S2.pit) renderPitfalls(S2.pit, S2.pitRole);
    };

    const ps = $('#pitSearch');
    if (ps) ps.oninput = () => {
      S2.pitQ = ps.value;
      if (S2.pit) renderPitfalls(S2.pit, S2.pitRole);
    };

    const cs = $('#conSearch');
    if (cs) {
      let t = null;
      cs.oninput = () => {
        // 输入就请求会把每个字都变成一次 /api/contests；压到 300ms
        clearTimeout(t);
        t = setTimeout(() => { S2.conQ = cs.value.trim(); loadContests(); }, 300);
      };
      cs.onkeydown = (e) => { if (e.key === 'Enter') { S2.conQ = cs.value.trim(); loadContests(); } };
    }
    const cr = $('#btnConReset');
    if (cr) cr.onclick = () => {
      S2.conCat = ''; S2.conFit = ''; S2.conQ = '';
      if ($('#conSearch')) $('#conSearch').value = '';
      const c1 = $('#conCats'); if (c1) { delete c1.dataset.done; c1.innerHTML = ''; }
      const c2 = $('#conFits'); if (c2) { delete c2.dataset.done; c2.innerHTML = ''; }
      loadContests();
    };
  }

  window.JH2 = {
    onView(v) {
      bind();
      if (v === 'industry') {
        renderFamChips();
        // 从简历工坊带过来的 JD 直接复用，省一次粘贴
        if (!S2.ind) {
          const jd = st().jd || '';
          if (jd) $('#indJd').value = jd;
          const tgt = (st().profile && S.profile.intent && S.profile.intent.targetRoles) || [];
          if (tgt.length && !$('#indRole').value) $('#indRole').value = tgt[0];
          if ($('#indRole').value || jd) loadIndustry(false);
          else $('#indBody').innerHTML = '<div class="empty-state">上面填个岗位名，或点下面的常用岗位。不用登录，不扣点。</div>';
        }
      }
      if (v === 'pitfall') {
        if (!S2.pit) {
          const tgt = (st().profile && S.profile.intent && S.profile.intent.targetRoles) || [];
          if (tgt.length && !$('#pitRole').value) $('#pitRole').value = tgt[0];
          loadPitfalls(false);
        }
        // 社区是独立文件（community.js），挂在这里而不是 app.js：
        // app.js 已经够长，社区有自己完整的列表 / 发帖 / 总结三套状态
        if (window.JHCom) JHCom.onView(v);
      }
      if (v === 'contests') loadContests();
    },
  };
})();
