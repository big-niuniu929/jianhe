/* =================================================================
   落笔 · 一岗一版
   =================================================================
   为什么单独一个文件：版本条是"跨视图"的东西——它要读写工坊的档案、
   要触发诊断和 JD 匹配、还要开除自己的弹窗。塞进 app.js 会让那个
   已经 1300 行的文件变成没人敢动的东西，所以这里用 JHVer 独立挂载。

   三条设计约束：
   1. 切换版本必须是**一次点击**。求职时用户一天切十几次，
      任何"先保存再加载"的二段式操作都会让人放弃。
   2. 每个版本必须能一眼看出状态：诊断分 / 匹配分 / 投过没有。
   3. AI 定制失败不阻断：规则版永远能用，只是话没那么准。
   ================================================================= */
(function () {
  'use strict';

  var V = {
    items: [],
    activeId: null,
    gap: null,
    bound: false,
    busy: false,
    mode: 'new',   // new | manage | diff
    editId: 0,
  };

  /* app.js 的 S 是 const，不挂 window，只能靠作用域访问 */
  function st() {
    try { return S; } catch (e) { return {}; }
  }
  function q(sel) { return document.querySelector(sel); }

  function active() {
    for (var i = 0; i < V.items.length; i++) {
      if (V.items[i].id === V.activeId) return V.items[i];
    }
    return null;
  }

  /* ------------------------------------------------------------ 渲染版本条 */
  function render() {
    var list = q('#verList');
    if (!list) return;
    if (!V.items.length) {
      list.innerHTML = '<span class="ver-empty">'
        + (window.matchMedia('(max-width: 860px)').matches
            ? '还没有版本，点「＋」新建'
            : '还没有版本。点右边「+ 新版本」，把当前简历复制一版、绑到某个岗位上——之后改动只影响那一版。')
        + '</span>';
      syncActs();
      return;
    }
    list.innerHTML = V.items.map(function (v) {
      var on = v.id === V.activeId;
      var score = v.jd ? ('匹配 ' + (v.match || 0)) : ('诊断 ' + (v.score || 0));
      return '<button class="ver-chip' + (on ? ' is-active' : '') + '" data-ver="' + v.id + '"'
        + ' title="' + att(v.name) + (v.jd ? ' · 已绑 JD' : ' · 未绑 JD') + '">'
        + '<span class="nm">' + esc(v.name) + '</span>'
        + '<span class="sc">' + score + '</span>'
        + (v.exports > 0 ? '<span class="sent">已投 ' + v.exports + '</span>' : '')
        + '</button>';
    }).join('');
    syncActs();
  }

  function att(s) {
    return String(s == null ? '' : s).replace(/"/g, '&quot;').replace(/</g, '&lt;');
  }

  function syncActs() {
    var has = !!active();
    var t = q('#btnVerTailor'), d = q('#btnVerDiff'), m = q('#btnVerMore');
    if (t) t.disabled = !has;
    if (d) d.disabled = !has || V.items.length < 2;
    if (m) m.disabled = !has;
    if (t) t.classList.toggle('hot', !!(has && active().jd));
  }

  /* ------------------------------------------------------------ 状态应用 */
  function applyProfile(d, opts) {
    var S0 = st();
    if (d.profile) S0.profile = d.profile;
    if (d.lint) S0.lint = d.lint;
    if ('match' in d) S0.match = d.match || null;
    if (opts && opts.jd !== undefined) S0.jd = opts.jd || '';
    S0.qs = null;
    S0.rewritten = 0;
    S0.exported = false;
    if (typeof renderAll === 'function') renderAll();
    if (typeof renderSteps === 'function') renderSteps();
  }

  function loadFromBoot() {
    var S0 = st();
    V.items = (S0.variants || []).slice();
    V.activeId = null;
    for (var i = 0; i < V.items.length; i++) {
      if (V.items[i].active) V.activeId = V.items[i].id;
    }
    render();
  }

  /* ------------------------------------------------------------ 切换 */
  function switchTo(id) {
    if (id === V.activeId || V.busy) return;
    var v = null;
    for (var i = 0; i < V.items.length; i++) if (V.items[i].id === id) v = V.items[i];
    if (!v) return;
    V.busy = true;
    api('/api/variants/activate', { id: id }).then(function (d) {
      V.items = d.items || V.items;
      V.activeId = d.activeId;
      V.gap = d.gap;
      applyProfile(d, { jd: (v.jd || '') });
      render();
      toast('已切到「' + v.name + '」，档案只改了这版', 2400);
    }).catch(function (e) {
      toast('切换失败：' + e.message, 3000);
    }).then(function () { V.busy = false; });
  }

  /* ------------------------------------------------------------ 新建 */
  function openNew() {
    V.mode = 'new';
    V.editId = 0;
    var S0 = st();
    var roles = ((S0.profile || {}).intent || {}).targetRoles || [];
    q('#verTitle').textContent = '新建版本';
    q('#verBody').innerHTML =
      '<p class="hint">以<b>当前这一版</b>为底复制一份，绑到一个岗位上。'
      + '之后在这版里的任何修改都只影响这版，切回去不串。</p>'
      + '<div class="vf">'
      + '  <label>公司<input id="nvCompany" placeholder="例如：字节跳动"></label>'
      + '  <label>岗位<input id="nvRole" placeholder="例如：AI 产品运营" value="' + att(roles[0] || '') + '"></label>'
      + '  <label class="full">JD（粘进来才能算匹配、才能定制）'
      + '    <textarea id="nvJd" rows="7" placeholder="把岗位描述整段粘进来。没有也行，之后可以在「管理」里补。"></textarea></label>'
      + '</div>'
      + '<p class="hint" id="nvTip" style="margin-top:10px">名字会按「公司 · 岗位」自动生成，重复了会自动带序号。</p>';
    q('#verFoot').innerHTML =
      '<button class="ghost" data-close>取消</button>'
      + '<button class="primary" id="nvGo">创建并切过去</button>';
    q('#nvGo').onclick = doCreate;
    showModal('#modalVer');
    setTimeout(function () { var el = q('#nvCompany'); if (el) el.focus(); }, 80);
  }

  function doCreate() {
    var company = (q('#nvCompany').value || '').trim();
    var role = (q('#nvRole').value || '').trim();
    var jd = (q('#nvJd').value || '').trim();
    if (!company && !role && !jd) { q('#nvTip').textContent = '至少填公司、岗位或粘一段 JD 中的一个'; return; }
    var btn = q('#nvGo');
    btn.disabled = true;
    btn.textContent = '创建中…';
    api('/api/variants', {
      company: company, role: role, jd: jd,
      profile: (st().profile) || null,
    }).then(function (d) {
      V.items = d.items || [];
      V.activeId = d.activeId;
      V.gap = d.gap;
      applyProfile(d, { jd: jd });
      hideModal();
      render();
      var v = active();
      pushBot('<div>建好了：<b>' + esc(v ? v.name : '新版本') + '</b>。'
        + (jd ? '已经绑上 JD，匹配度 <b>' + ((d.match && d.match.score) || 0) + '</b>。'
               + '下一步点「按 JD 定制」，我告诉你该动哪几句。'
               : '还没绑 JD。点「管理」把 JD 贴进去，才能算匹配和定制。')
        + '</div>');
      track('variant_new', { has_jd: !!jd });
    }).catch(function (e) {
      q('#nvTip').textContent = '创建失败：' + e.message;
    }).then(function () {
      btn.disabled = false;
      btn.textContent = '创建并切过去';
    });
  }

  /* ------------------------------------------------------------ 管理 */
  function openManage() {
    V.mode = 'manage';
    q('#verTitle').textContent = '管理版本';
    var cur = active();
    q('#verBody').innerHTML =
      '<p class="hint">版本之间互不影响。删掉一版不会动其他版；删当前版会自动切到最近改过的那版。</p>'
      + '<div class="vm-list">' + V.items.map(function (v) {
        var on = v.id === V.activeId;
        return '<div class="vm-row' + (on ? ' on' : '') + '">'
          + '<div class="vm-main">'
          + '  <div class="vm-nm">' + esc(v.name)
          + (on ? '<span class="tag blue">当前</span>' : '')
          + (v.exports > 0 ? '<span class="tag green">投过 ' + v.exports + ' 次</span>' : '')
          + '</div>'
          + '<div class="vm-sub">' + (v.jd ? '已绑 JD · 匹配 ' + (v.match || 0) : '未绑 JD · 诊断 ' + (v.score || 0))
          + (v.role ? ' · ' + esc(v.role) : '') + '</div>'
          + '</div>'
          + '<div class="vm-act">'
          + (on ? '' : '<button class="mini" data-vgo="' + v.id + '">切过去</button>')
          + '<button class="mini" data-vedit="' + v.id + '">改</button>'
          + '<button class="mini" data-vdel="' + v.id + '">删</button>'
          + '</div></div>';
      }).join('') + '</div>';
    q('#verFoot').innerHTML = '<span class="hint" style="margin-right:auto;font-size:11px;color:#8b8479">'
      + '当前共 ' + V.items.length + ' 版</span>'
      + '<button class="ghost" data-close>关闭</button>'
      + '<button class="primary" id="vmNew">+ 新版本</button>';
    q('#vmNew').onclick = openNew;

    Array.prototype.forEach.call(q('#verBody').querySelectorAll('[data-vgo]'), function (b) {
      b.onclick = function () { hideModal(); switchTo(+b.dataset.vgo); };
    });
    Array.prototype.forEach.call(q('#verBody').querySelectorAll('[data-vedit]'), function (b) {
      b.onclick = function () { openEdit(+b.dataset.vedit); };
    });
    Array.prototype.forEach.call(q('#verBody').querySelectorAll('[data-vdel]'), function (b) {
      b.onclick = function () { delVersion(+b.dataset.vdel); };
    });
    showModal('#modalVer');
  }

  function openEdit(id) {
    var v = null;
    for (var i = 0; i < V.items.length; i++) if (V.items[i].id === id) v = V.items[i];
    if (!v) return;
    V.mode = 'edit';
    V.editId = id;
    q('#verTitle').textContent = '改版本信息';
    q('#verBody').innerHTML =
      '<div class="vf">'
      + '  <label>名字<input id="evName" value="' + att(v.name) + '"></label>'
      + '  <label>岗位<input id="evRole" value="' + att(v.role || '') + '"></label>'
      + '  <label>公司<input id="evCompany" value="' + att(v.company || '') + '"></label>'
      + '  <label>备注<input id="evNote" value="' + att(v.note || '') + '" placeholder="内推码 / 投递渠道"></label>'
      + '  <label class="full">JD<textarea id="evJd" rows="7">' + esc(v.jd || '') + '</textarea></label>'
      + '</div>'
      + '<p class="hint" style="margin-top:10px">换 JD 会重算匹配度，不会改简历内容。</p>';
    q('#verFoot').innerHTML = '<button class="ghost" data-close>取消</button>'
      + '<button class="primary" id="evGo">保存</button>';
    q('#evGo').onclick = function () {
      var btn = q('#evGo');
      btn.disabled = true;
      api('/api/variants/update', {
        id: id,
        name: (q('#evName').value || '').trim(),
        role: (q('#evRole').value || '').trim(),
        company: (q('#evCompany').value || '').trim(),
        note: (q('#evNote').value || '').trim(),
        jd: (q('#evJd').value || '').trim(),
      }).then(function (d) {
        V.items = d.items || V.items;
        hideModal();
        render();
        // 改的是当前版 → JD 变了，匹配度要跟着重算
        if (id === V.activeId) refreshCurrent();
        toast('已保存');
      }).catch(function (e) {
        toast('保存失败：' + e.message);
      }).then(function () { btn.disabled = false; });
    };
    showModal('#modalVer');
  }

  function delVersion(id) {
    var v = null;
    for (var i = 0; i < V.items.length; i++) if (V.items[i].id === id) v = V.items[i];
    if (!v) return;
    if (!confirm('删掉「' + v.name + '」？这一版的改动会一起消失，其他版本不受影响。')) return;
    api('/api/variants/delete', { id: id }).then(function (d) {
      V.items = d.items || [];
      V.activeId = d.activeId;
      if (d.profile) applyProfile(d, { jd: (active() || {}).jd || '' });
      render();
      if (q('#modalVer').classList.contains('show')) openManage();
      toast('已删除');
    }).catch(function (e) { toast('删除失败：' + e.message); });
  }

  /* 当前版 JD 变了之后，拉一次匹配，别让右侧还挂着旧结论 */
  function refreshCurrent() {
    var v = active();
    var S0 = st();
    if (!v || !v.jd) {
      S0.match = null;
      if (typeof renderSide === 'function') renderSide();
      return;
    }
    api('/api/jd', { profile: S0.profile, jd: v.jd }).then(function (d) {
      S0.match = d.result;
      S0.jd = v.jd;
      if (typeof renderSide === 'function') renderSide();
      if (typeof renderSteps === 'function') renderSteps();
    }).catch(function () {});
  }

  /* ------------------------------------------------------------ 差异对比 */
  function openDiff() {
    V.mode = 'diff';
    var v = active();
    if (!v) return;
    q('#verTitle').textContent = '版本差异';
    q('#verBody').innerHTML = '<div class="dempty">正在比对…</div>';
    q('#verFoot').innerHTML = '<button class="ghost" data-close>关闭</button>';
    showModal('#modalVer');
    api('/api/variants/diff', { id: v.id }).then(function (d) {
      q('#verBody').innerHTML = diffHtml(d, v);
      track('variant_diff', {});
    }).catch(function (e) {
      q('#verBody').innerHTML = '<div class="dempty">比对失败：' + esc(e.message) + '</div>';
    });
  }

  function diffHtml(d, v) {
    var df = d.diff || { changed: [], added: [], removed: [], meta: [], stats: {} };
    var base = d.base ? d.base.name : '（没有其他版本可对比）';
    var head = '<div class="diff-side"><b>' + esc(v.name) + '</b>'
      + '<span>对比基准：' + esc(base) + '</span>'
      + '<span>改动 ' + (df.stats.changed || 0) + ' 条 / 新增 ' + (df.stats.added || 0)
      + ' / 删除 ' + (df.stats.removed || 0) + '</span></div>';

    if (df.same) {
      return head + '<div class="dempty">这版和基准<b>完全一样</b>——也就是说，它还没有为这个岗位做任何调整。<br>'
        + '点「按 JD 定制」，先看该动哪几句。</div>';
    }

    var html = head;
    if (df.meta.length) {
      html += '<div class="dgroup"><div class="dgroup-h">整体改动</div>'
        + df.meta.map(function (m) {
          return '<div class="drow meta"><div class="dl">' + esc(m.what) + '</div>'
            + '<div class="dv"><span class="old">' + (esc(m.before) || '（空）') + '</span>'
            + '<div class="new">' + (esc(m.after) || '（空）') + '</div></div></div>';
        }).join('') + '</div>';
    }
    if (df.changed.length) {
      html += '<div class="dgroup"><div class="dgroup-h">改了哪几句</div>'
        + df.changed.map(function (c) {
          return '<div class="drow"><div class="dl">' + esc(c.label) + ' · 第 '
            + (parseInt(c.bid.split('.')[2], 10) + 1) + ' 条</div>'
            + '<div class="dv"><span class="old">' + esc(c.before || '（空）') + '</span>'
            + '<div class="new">' + esc(c.after) + '</div></div></div>';
        }).join('') + '</div>';
    }
    if (df.added.length) {
      html += '<div class="dgroup"><div class="dgroup-h">这版新增</div>'
        + df.added.map(function (a) {
          return '<div class="drow added"><div class="dl">' + esc(a.label) + '</div>'
            + '<div class="dv">' + esc(a.text) + '</div></div>';
        }).join('') + '</div>';
    }
    if (df.removed.length) {
      html += '<div class="dgroup"><div class="dgroup-h">这版删掉</div>'
        + df.removed.map(function (r) {
          return '<div class="drow removed"><div class="dl">' + esc(r.label) + '</div>'
            + '<div class="dv">' + esc(r.text) + '</div></div>';
        }).join('') + '</div>';
    }
    if (d.gap && d.gap.gap && d.gap.gap.hasJd) {
      html += '<div class="dgroup"><div class="dgroup-h">这版还差什么</div>'
        + '<div class="drow"><div class="dv">匹配 <b>' + d.gap.gap.score + '</b> 分，'
        + (d.gap.gap.missing.length ? '还差 ' + d.gap.gap.missing.length + ' 个关键词：'
           + d.gap.gap.missing.slice(0, 8).map(function (w) { return '<span class="tag orange">' + esc(w) + '</span>'; }).join('')
           : '关键词已全覆盖')
        + '</div></div></div>';
    }
    return html;
  }

  /* ------------------------------------------------------------ 按 JD 定制 */
  function tailor() {
    var v = active();
    var S0 = st();
    if (!v) return toast('先建一版，才有 JD 可对齐');
    if (!v.jd) {
      toast('这一版还没绑 JD', 2400);
      return openEdit(v.id);
    }
    if (V.busy) return;
    V.busy = true;
    pushUser('按这个 JD，告诉我该改哪几句');
    var load = pushBot('<span class="typing"><i></i><i></i><i></i></span> 正在对着 JD 读你的简历…');
    api('/api/tailor', {
      profile: S0.profile, jd: v.jd, role: v.role, company: v.company,
    }).then(function (d) {
      load.remove();
      if (d.meter && d.meter.left != null) {
        S0.wallet = Object.assign({}, S0.wallet || {}, { points: d.meter.left });
        if (typeof renderAcct === 'function') renderAcct();
      }
      pushBot(tailorCard(d, v));
      track('tailor', { mode: d.mode });
    }).catch(function (e) {
      load.remove();
      pushBot('<span style="color:#b5341f">定制失败：' + esc(e.message) + '</span>');
    }).then(function () { V.busy = false; });
  }

  function tailorCard(d, v) {
    var r = d.result || {};
    var moves = r.moves || [];
    var head = d.mode === 'llm'
      ? 'AI 版 · 已扣 <b>' + ((d.meter && d.meter.points) || 1) + '</b> 点'
      : (d.mode === 'fallback' ? 'AI 这次没成功，已降级为规则版（未扣点）' : '规则版 · 免费');

    var body = '';
    if (r.verdict) {
      body += '<div style="font-size:12.5px;margin-bottom:2px">' + esc(r.verdict) + '</div>';
    }
    if (!moves.length) {
      body += '<div class="dempty" style="margin-top:8px">这一版没有需要动的地方：'
        + '可能每条都已经有数字和结果，也可能简历里根本没东西可改。<br>先点「一键诊断」看是不是缺经历。</div>';
    }
    body += moves.map(function (m) {
      var bad = m.guard && m.guard.ok === false;
      return '<div class="tmove">'
        + '<div class="tm-h"><span class="tag blue">' + esc(m.label || '经历') + '</span>'
        + (m.covers && m.covers.length ? m.covers.map(function (w) {
            return '<span class="tag green">覆盖 ' + esc(w) + '</span>';
          }).join('') : '<span style="color:#8b8479">未新增关键词</span>')
        + (bad ? '<span class="tag red">疑似新增数字：' + esc((m.guard.newNumbers || []).join(' / ')) + '</span>' : '')
        + '</div>'
        + '<div class="tm-b">' + esc(m.before) + '</div>'
        + '<div class="tm-a">' + esc(m.after) + '</div>'
        + (m.why ? '<div class="tm-w">' + esc(m.why) + '</div>' : '')
        + (m.verify ? '<div class="tm-v">采用前先确认：' + esc(m.verify) + '</div>' : '')
        + '<div class="tm-f"><button class="mini primary" data-tv="' + esc(m.bid) + '"'
        + ' data-tvt="' + att(m.after) + '">采用这版</button>'
        + '<span style="font-size:10.5px;color:#8b8479">只改这一句，不会动别的</span></div>'
        + '</div>';
    }).join('');

    if (r.notClaimable && r.notClaimable.length) {
      body += '<div class="tno"><div class="tno-h">这些别写（写了面试必崩）</div><ul>'
        + r.notClaimable.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('')
        + '</ul></div>';
    }
    if (r.opening) {
      body += '<div class="topen"><b>自我介绍第一句</b><br>' + esc(r.opening) + '</div>';
    }
    if (d.note) {
      body += '<div style="font-size:11px;color:#8b8479;margin-top:8px;line-height:1.6">' + esc(d.note) + '</div>';
    }
    if (d.mode !== 'llm') {
      body += '<div style="margin-top:8px"><button class="mini" data-tv-login="1">'
        + (st().anonymous ? '注册后用 AI 版（送 3 次导出 + 会员赠 20 点）' : '看点数怎么充')
        + '</button></div>';
    }

    return '<div>' + esc(v.name) + ' · 对这段 JD 的定制方案</div>'
      + '<div class="cc"><div class="cc-head">按 JD 定制<span class="sub">' + head + '</span></div>'
      + '<div class="cc-body">' + body + '</div>'
      + '<div class="cc-foot"><button class="mini" data-tv-diff="1">看这版改了什么</button>'
      + '<span class="hint">采用后会自动重新打分</span></div></div>';
  }

  /* 采用一条定制建议 */
  function adopt(bid, text, btn) {
    var S0 = st();
    var p = S0.profile;
    if (!p) return;
    var parts = String(bid).split('.');
    var item = (p[parts[0]] || [])[+parts[1]];
    if (!item) return toast('这条经历已经不存在了');
    var b = (item.bullets || [])[+parts[2]];
    if (!b) return toast('这条经历已经不存在了');
    b.text = text;
    S0.rewritten = (S0.rewritten || 0) + 1;
    if (typeof renderAll === 'function') renderAll();
    if (typeof saveProfile === 'function') saveProfile();
    if (btn) { btn.textContent = '已采用 ✓'; btn.disabled = true; btn.classList.remove('primary'); }
    toast('已替换，重新打分中…');
    if (typeof doLint === 'function') doLint(true);
  }

  /* ------------------------------------------------------------ 绑定 */
  function bind() {
    if (V.bound) return;
    V.bound = true;

    q('#btnVerNew').onclick = openNew;
    q('#btnVerMore').onclick = openManage;
    q('#btnVerDiff').onclick = openDiff;
    q('#btnVerTailor').onclick = tailor;

    q('#verList').onclick = function (e) {
      var b = e.target.closest('[data-ver]');
      if (b) switchTo(+b.dataset.ver);
    };

    /* 定制卡里的按钮走事件委托：卡片是后插进对话的 */
    document.addEventListener('click', function (e) {
      var ado = e.target.closest('[data-tv]');
      if (ado) return adopt(ado.dataset.tv, ado.dataset.tvt, ado);
      if (e.target.closest('[data-tv-diff]')) return openDiff();
      if (e.target.closest('[data-tv-login]')) {
        return st().anonymous ? openAuth() : openPay();
      }
    });
  }

  /* ------------------------------------------------------------ 对外 */
  window.JHVer = {
    boot: function () { bind(); loadFromBoot(); },
    /* 账号变动后重新拉一次：匿名版本的 owner 会迁移到账号下 */
    refresh: function () {
      return api('/api/variants').then(function (d) {
        V.items = d.items || [];
        V.activeId = d.activeId;
        V.gap = d.gap;
        render();
      }).catch(function () {});
    },
    openNew: openNew,
  };
})();
