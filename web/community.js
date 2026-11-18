/* =========================================================
   落笔 · 互助会（社区）
   ---------------------------------------------------------
   单独一个文件，理由和 views2.js 一样：app.js 已经够长，
   再塞一个完整的社区（列表 / 发帖 / 点赞 / AI 总结）会让它变成一个
   没人敢改的文件。这里通过 window.JHCom.onView 挂到 switchView 上。

   三条与产品定位直接相关的取舍：

   1. **读不用登录，写才用。** 社区的价值在"看得到别人的经验"，
      把阅读挡在登录后面等于把大部分人挡在门外。

   2. **AI 总结是对正文的批注，不是代替正文。**
      所以它默认折叠、位置在正文之后、字号小于正文。
      如果总结放在正文前面，人们就不会读原文了，而原文里的细节才是这条帖子值钱的地方。

   3. **不做好评率、不做热搜。** 这个社区的目的是让人少踩坑，
      不是让人涨粉。点赞只做"这条对我有用"，一个数字，不做排名。
   ========================================================= */
(function () {
  'use strict';

  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const STAGE = {
    find: '找实习时', onboard: '入职前后', work: '工作中',
    convert: '转正环节', leave: '结束时',
  };

  const S = {
    items: [],
    stage: '',
    q: '',
    open: {},       // 展开的帖子 id → true
    sums: {},       // id → summary 对象
    busy: {},       // id → true（正在生成总结）
    loaded: false,
    bound: false,
    anonymous: true,
    stats: null,
  };

  /* ------------------------------------------------------------ 工具 */
  function ago(ts) {
    if (!ts) return '';
    const d = Math.floor(Date.now() / 1000) - ts;
    if (d < 60) return '刚刚';
    if (d < 3600) return Math.floor(d / 60) + ' 分钟前';
    if (d < 86400) return Math.floor(d / 3600) + ' 小时前';
    if (d < 86400 * 7) return Math.floor(d / 86400) + ' 天前';
    const t = new Date(ts * 1000);
    return (t.getMonth() + 1) + ' 月 ' + t.getDate() + ' 日';
  }

  function pad(n) {
    return n < 10 ? '0' + n : String(n);
  }

  /* ------------------------------------------------------------ 列表 */
  async function load(force) {
    if (S.loaded && !force && !S.stage && !S.q) return;
    const body = $('#comBody');
    const q = new URLSearchParams();
    if (S.stage) q.set('stage', S.stage);
    if (S.q) q.set('q', S.q);
    try {
      const d = await api('/api/community?' + q.toString());
      S.items = d.items || [];
      S.stats = d.stats || null;
      S.anonymous = d.anonymous !== false;
      S.loaded = true;
      renderStat();
      renderList();
    } catch (e) {
      body.innerHTML = `<div class="empty-state err">${esc(e.message)}</div>`;
    }
  }

  function renderStat() {
    const el = $('#comStat');
    if (!el) return;
    const st = S.stats || { posts: 0, authors: 0 };
    if (!st.posts) {
      el.innerHTML = '还没有人写过。第一条由你开始，也不算太晚。';
      return;
    }
    el.innerHTML = `<b>${st.posts}</b> 条帖子 · <b>${st.authors}</b> 位作者在写 `
      + `<span style="color:var(--ink3)">· 每条都是一个人真遇到过的事</span>`;
  }

  function renderList() {
    const body = $('#comBody');
    if (!S.items.length) {
      body.innerHTML = `<div class="empty-state">${S.q || S.stage
        ? '这个筛选下还没有帖子。换个词，或把阶段的「全部」打开。'
        : '还没有人写。你可以是第一个——写清楚一件真事，比写十条感想有用。'}</div>`;
      return;
    }
    body.innerHTML = S.items.map((p, i) => postHTML(p, i)).join('');
    bindList();
  }

  function postHTML(p, i) {
    const open = !!S.open[p.id];
    const sum = S.sums[p.id];
    const stage = STAGE[p.stage] || '';
    const meta = [
      esc(p.author || '匿名同学'),
      p.role ? esc(p.role) : '',
      stage,
      ago(p.created_at),
    ].filter(Boolean).join(' · ');

    const tags = (p.tags || []).length
      ? `<span style="color:var(--ink3);letter-spacing:.04em">${p.tags.map((t) => '#' + esc(t)).join(' ')}</span>`
      : '';

    return `
      <article class="post" data-id="${p.id}">
        <div class="post-n">${pad(i + 1)}</div>
        <div>
          <h4>${esc(p.title)}</h4>
          <div class="post-meta">${meta}</div>
          <div class="post-text ${open ? '' : 'is-clamp'}">${esc(p.body)}</div>
          ${sum ? summaryHTML(sum) : ''}
          <div class="post-acts">
            <button data-act="sum" data-id="${p.id}" ${S.busy[p.id] ? 'disabled' : ''}>
              ${S.busy[p.id] ? '正在总结…' : (sum ? '重新总结' : (p.hasSummary ? '看 AI 总结' : 'AI 总结'))}
            </button>
            <button data-act="more" data-id="${p.id}">${open ? '收起' : '全文'}</button>
            <button data-act="like" data-id="${p.id}" class="${p.liked ? 'on' : ''}">
              有用${p.likes ? ' ' + p.likes : ''}
            </button>
            ${tags}
            ${p.mine ? `<button class="post-del gap" data-act="del" data-id="${p.id}">删除</button>` : ''}
          </div>
        </div>
      </article>`;
  }

  function summaryHTML(s) {
    const li = (arr) => (arr && arr.length
      ? `<ul>${arr.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : '');
    return `
      <div class="psum">
        <div class="psum-h">AI 总结 · 只复述帖子里写过的</div>
        <div class="psum-1">${esc(s.headline || '')}</div>
        ${s.signals && s.signals.length ? `<div class="psum-row"><span>早期信号</span><div>${li(s.signals)}</div></div>` : ''}
        ${s.avoid && s.avoid.length ? `<div class="psum-row"><span>能拿走的</span><div>${li(s.avoid)}</div></div>` : ''}
        ${s.whoFits ? `<div class="psum-row"><span>谁该看</span><div>${esc(s.whoFits)}</div></div>` : ''}
        ${s.evidence ? `<div class="psum-row"><span>可靠程度</span><div>${esc(s.evidence)}</div></div>` : ''}
        ${s.missing && s.missing.length ? `<div class="psum-row"><span>帖子里没说</span><div>${esc(s.missing.join('；'))}</div></div>` : ''}
        <div class="psum-foot">总结由模型生成，只复述原帖内容，不补充常识。判断请以原帖为准。</div>
      </div>`;
  }

  function bindList() {
    const body = $('#comBody');
    if (!body || body.dataset.bound === '1') return;
    body.dataset.bound = '1';
    body.addEventListener('click', async (e) => {
      const b = e.target.closest('button[data-act]');
      if (!b) return;
      const id = parseInt(b.dataset.id, 10);
      const act = b.dataset.act;
      if (act === 'more') {
        S.open[id] = !S.open[id];
        tailRender(id);
      } else if (act === 'like') {
        await doLike(id);
      } else if (act === 'sum') {
        await doSummary(id);
      } else if (act === 'del') {
        await doDelete(id, b);
      }
    });
  }

  /* 只重画一条：整表重画会让用户正在读的那段跳走 */
  function tailRender(id) {
    const idx = S.items.findIndex((x) => x.id === id);
    if (idx < 0) return;
    const el = $(`.post[data-id="${id}"]`);
    if (!el) return;
    const tmp = document.createElement('div');
    tmp.innerHTML = postHTML(S.items[idx], idx);
    const fresh = tmp.firstElementChild;
    el.replaceWith(fresh);
  }

  async function doLike(id) {
    const p = S.items.find((x) => x.id === id);
    if (!p) return;
    const on = !p.liked;
    // 先改本地：点赞必须是零延迟的，等接口回来再变色会让人以为没点上
    p.liked = on;
    p.likes = Math.max(0, (p.likes || 0) + (on ? 1 : -1));
    tailRender(id);
    try {
      const d = await api('/api/community/like', { id, on });
      if (typeof d.likes === 'number') {
        p.likes = d.likes;
        tailRender(id);
      }
    } catch (e) {
      p.liked = !on;
      p.likes = Math.max(0, (p.likes || 0) + (on ? -1 : 1));
      tailRender(id);
      toast('没点上，可能是网络问题', 2000);
    }
  }

  async function doSummary(id) {
    if (S.busy[id]) return;
    const p = S.items.find((x) => x.id === id);
    if (!p) return;

    // 已经生成过的（帖子上有缓存）先直接取，别浪费一次调用
    S.busy[id] = true;
    tailRender(id);
    try {
      const d = await api('/api/community/summary', { id });
      if (d.ok) {
        S.sums[id] = d.summary;
        if (!d.cached) track('post_summary', { id });
      } else {
        // 不是"请求失败"，是"这次没法总结"——用一行说明，不弹错误框
        S.sums[id] = null;
        toast(d.error || '这条暂时总结不了', 3600);
        if (d.code === 'need_login' && typeof openAuth === 'function') {
          setTimeout(() => openAuth(), 600);
        } else if (d.code === 'no_llm' && typeof openModel === 'function') {
          setTimeout(() => openModel(), 600);
        }
      }
    } catch (e) {
      toast(e.message, 3000);
    } finally {
      S.busy[id] = false;
      tailRender(id);
    }
  }

  async function doDelete(id, btn) {
    if (!window.confirm('删掉这条？删了就是真的没了，别人正在看的也会消失。')) return;
    btn.disabled = true;
    try {
      await api('/api/community/delete', { id });
      S.items = S.items.filter((x) => x.id !== id);
      delete S.open[id];
      delete S.sums[id];
      load(true);
      toast('已删除', 1800);
    } catch (e) {
      btn.disabled = false;
      toast(e.message, 3000);
    }
  }

  /* ------------------------------------------------------------ 发帖 */
  function openPost() {
    const sel = $('#postStage');
    if (sel && !sel.options.length) {
      sel.innerHTML = Object.keys(STAGE).map(
        (k) => `<option value="${k}"${k === 'work' ? ' selected' : ''}>${STAGE[k]}</option>`).join('');
    }
    if (S.anonymous) {
      toast('发帖要登录，10 秒就好', 2400);
      if (typeof openAuth === 'function') openAuth();
      return;
    }
    const err = $('#postErr');
    if (err) err.textContent = '';
    // 从避坑页正在看的岗位带过来，用户少填一个字段
    const pr = $('#pitRole');
    const r = $('#postRole');
    if (r && !r.value && pr && pr.value) r.value = pr.value;
    showModal('#modalPost');
  }

  async function submitPost() {
    const err = $('#postErr');
    const title = ($('#postTitle').value || '').trim();
    const body = ($('#postBody').value || '').trim();
    if (title.length < 4) { err.textContent = '标题至少 4 个字'; return; }
    if (body.length < 20) { err.textContent = '正文至少 20 个字，写清楚发生了什么'; return; }
    err.textContent = '';
    const btn = $('#btnPostGo');
    btn.disabled = true;
    try {
      await api('/api/community/post', {
        title, body,
        role: ($('#postRole').value || '').trim(),
        stage: $('#postStage').value || 'work',
        tags: ($('#postTags').value || '').trim(),
      });
      hideModal();
      $('#postTitle').value = '';
      $('#postBody').value = '';
      $('#postTags').value = '';
      S.stage = '';
      S.q = '';
      const cs = $('#comSearch');
      if (cs) cs.value = '';
      $$('#comStage button').forEach((b) => b.classList.toggle('is-active', !b.dataset.stage));
      await load(true);
      toast('发出去了。谢谢你写下来。', 2600);
      track('post_create', {});
    } catch (e) {
      err.textContent = e.message;
      btn.disabled = false;
    }
  }

  /* ------------------------------------------------------------ 绑定 */
  function bind() {
    if (S.bound) return;
    S.bound = true;

    const n = $('#comNew');
    if (n) n.onclick = openPost;
    const go = $('#btnPostGo');
    if (go) go.onclick = submitPost;

    const cs = $('#comSearch');
    if (cs) {
      let t = null;
      cs.oninput = () => {
        clearTimeout(t);
        // 压到 320ms：输入就请求会把每个字都变成一次查询
        t = setTimeout(() => { S.q = cs.value.trim(); load(true); }, 320);
      };
    }

    const st = $('#comStage');
    if (st) {
      st.onclick = (e) => {
        const b = e.target.closest('button');
        if (!b) return;
        S.stage = b.dataset.stage || '';
        $$('#comStage button').forEach((x) => x.classList.toggle('is-active', x === b));
        load(true);
      };
    }
  }

  window.JHCom = {
    onView(v) {
      if (v !== 'pitfall') return;
      bind();
      load(false);
    },
    reload() { S.loaded = false; load(true); },
  };
})();
