/* =================================================================
   落笔 · 移动端
   =================================================================
   只做一件事：把桌面三栏工作台，拆成"一次只干一件事"的手机版。
   不做的事：不改任何业务逻辑、不发请求、不碰档案数据。
   —— 手机上出的每个 bug，都必须能在桌面版复现才算逻辑问题；
      否则就是这里的适配问题，边界清楚，好排查。

   为什么不用纯 CSS 做面板切换：
   A4 纸在 360px 宽上按比例缩到 0.45，正文只有 6px。
   所以手机默认走「阅读模式」（真 14px 的响应式排版），
   需要看页数时再切回 A4 —— 这需要状态，纯 CSS 表达不了。
   ================================================================= */
(function () {
  'use strict';

  var MQ = window.matchMedia('(max-width: 860px)');
  var pane = 'chat';      // chat | paper | diag
  var readMode = true;    // 手机默认阅读模式
  var built = false;

  function q(sel) { return document.querySelector(sel); }

  function isMobile() { return MQ.matches; }

  function build() {
    if (built) return;
    built = true;

    var studio = q('#view-studio');
    if (studio && !q('#mobTabs')) {
      var bar = document.createElement('div');
      bar.className = 'mobtabs';
      bar.id = 'mobTabs';
      bar.innerHTML =
        '<button data-pane="chat" class="is-active">对话</button>' +
        '<button data-pane="paper">简历</button>' +
        '<button data-pane="diag">诊断</button>';
      bar.onclick = function (e) {
        var b = e.target.closest('[data-pane]');
        if (b) setPane(b.dataset.pane);
      };
      studio.appendChild(bar);
    }

    var bar2 = q('.pv-toolbar');
    if (bar2 && !q('#btnRead')) {
      var btn = document.createElement('button');
      btn.className = 'ghost mob-read-btn';
      btn.id = 'btnRead';
      btn.title = '手机上看不清 A4 缩略图时切到阅读排版';
      btn.onclick = function () {
        readMode = !readMode;
        applyRead();
        // A4 模式下要让 app.js 重新算一次缩放
        if (!readMode) window.dispatchEvent(new Event('resize'));
      };
      bar2.appendChild(btn);
    }
  }

  function setPane(p) {
    pane = p || 'chat';
    var studio = q('#view-studio');
    if (!studio) return;
    studio.classList.remove('m-chat', 'm-paper', 'm-diag');
    studio.classList.add('m-' + pane);
    Array.prototype.forEach.call(document.querySelectorAll('#mobTabs button'), function (b) {
      b.classList.toggle('is-active', b.dataset.pane === pane);
    });
    if (pane === 'paper') {
      applyRead();
      if (!readMode) setTimeout(function () { window.dispatchEvent(new Event('resize')); }, 30);
    }
  }

  function applyRead() {
    var scroll = q('#paperScroll');
    var btn = q('#btnRead');
    if (!scroll) return;
    if (!isMobile()) {
      scroll.classList.remove('read');
      if (btn) btn.textContent = '阅读模式';
      return;
    }
    scroll.classList.toggle('read', readMode);
    if (btn) btn.textContent = readMode ? 'A4 视图' : '阅读模式';
  }

  function onBreakpoint() {
    var studio = q('#view-studio');
    if (!studio) return;
    if (isMobile()) {
      if (!studio.classList.contains('m-chat') &&
          !studio.classList.contains('m-paper') &&
          !studio.classList.contains('m-diag')) {
        setPane(pane);
      }
      applyRead();
    } else {
      studio.classList.remove('m-chat', 'm-paper', 'm-diag');
      applyRead();
    }
  }

  function init() {
    build();
    onBreakpoint();
    if (MQ.addEventListener) MQ.addEventListener('change', onBreakpoint);
    else if (MQ.addListener) MQ.addListener(onBreakpoint);
    window.addEventListener('orientationchange', function () {
      setTimeout(function () { window.dispatchEvent(new Event('resize')); }, 260);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  /* 对外：导出时如果当前是手机阅读排版，先切回 A4 —— 否则打印出来的宽高会不对 */
  window.JHM = {
    isMobile: isMobile,
    toA4: function () {
      readMode = false;
      applyRead();
    },
    pane: function () { return pane; },
  };
})();
