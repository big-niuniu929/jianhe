# -*- coding: utf-8 -*-
"""简历 → 可打印 HTML → PDF（Playwright + 本机 Edge）

用途：ATS 阶段 B 需要真实 PDF 文字层；同时给用户一个「服务端导出 PDF」。
复用上版打印契约：A4、单页、文字层保留。
"""
import html as _html


def _esc(s):
    return _html.escape(str(s or ""))


def build_resume_html(profile):
    p = profile or {}
    b = p.get("basic") or {}
    it = p.get("intent") or {}

    def bullets(sec):
        out = []
        for item in p.get(sec) or []:
            out.append(
                '<div class="item"><div class="item-h"><span>%s%s</span><span>%s</span></div><ul>%s</ul></div>'
                % (
                    _esc(item.get("org") or item.get("name") or ""),
                    (" · " + _esc(item.get("role"))) if item.get("role") else "",
                    _esc(item.get("time")),
                    "".join("<li>%s</li>" % _esc((x.get("text") if isinstance(x, dict) else x)) for x in (item.get("bullets") or [])),
                )
            )
        return "".join(out) or '<div class="muted">—</div>'

    edu = "".join(
        '<div class="item"><div class="item-h"><span>%s · %s（%s）</span><span>%s</span></div>'
        '<div class="item-s">%s%s</div></div>'
        % (
            _esc(e.get("school")), _esc(e.get("major")), _esc(e.get("degree")), _esc(e.get("time")),
            _esc(" · ".join(e.get("notes") or [])),
            (" · GPA " + _esc(e.get("gpa"))) if e.get("gpa") else "",
        )
        for e in p.get("education") or []
    ) or '<div class="muted">—</div>'

    skills = " ｜ ".join(
        ("%s：%s" % (_esc(g.get("group")), _esc("、".join(g.get("items") or [])))) if isinstance(g, dict) else _esc(g)
        for g in p.get("skills") or []
    ) or "—"

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>
  @page {{ size: A4; margin: 14mm 16mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans SC", sans-serif;
         font-size: 10.5pt; line-height: 1.65; color: #22201c; margin: 0; }}
  h1 {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; font-size: 22pt; margin: 0 0 2pt; letter-spacing: normal; }}
  .head {{ display: flex; justify-content: space-between; align-items: flex-start;
           border-bottom: 1.5pt solid #22201c; padding-bottom: 4pt; margin-bottom: 8pt; }}
  .contact {{ font-size: 9pt; color: #55504a; text-align: right; line-height: 1.7; }}
  .sub {{ font-size: 9.5pt; color: #55504a; }}
  .sec {{ margin-bottom: 8pt; page-break-inside: avoid; }}
  .sec-t {{ font-size: 10pt; letter-spacing: normal; color: #b23a2c; font-weight: 700;
            border-bottom: .75pt solid #ddd6c8; padding-bottom: 2pt; margin-bottom: 5pt; }}
  .item {{ margin-bottom: 5pt; }}
  .item-h {{ display: flex; justify-content: space-between; font-size: 10pt; font-weight: 700; }}
  .item-s {{ font-size: 9pt; color: #55504a; }}
  ul {{ margin: 2pt 0 0; padding-left: 14pt; }}
  li {{ margin: 0 0 1.5pt; }}
  .muted {{ color: #888; }}
</style></head><body>
  <div class="head">
    <div>
      <h1>{_esc(b.get('name') or '姓名')}</h1>
      <div class="sub">求职意向：{_esc(' / '.join(it.get('targetRoles') or []) or '—')}{(' · ' + _esc(it.get('available'))) if it.get('available') else ''}</div>
    </div>
    <div class="contact">{_esc(b.get('phone'))}<br>{_esc(b.get('email'))}<br>{_esc(b.get('city'))}</div>
  </div>
  <div class="sec"><div class="sec-t">教育背景</div>{edu}</div>
  <div class="sec"><div class="sec-t">实习经历</div>{bullets('internships')}</div>
  <div class="sec"><div class="sec-t">项目经历</div>{bullets('projects')}</div>
  <div class="sec"><div class="sec-t">技能</div><div style="font-size:9.5pt">{skills}</div></div>
</body></html>"""


def render_pdf(html):
    """用本机 Edge（channel=msedge）渲染 A4 PDF，返回 bytes。"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="load")
            pdf = page.pdf(format="A4", print_background=True, prefer_css_page_size=True)
        finally:
            browser.close()
    return pdf
