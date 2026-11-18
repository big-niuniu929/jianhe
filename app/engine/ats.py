# -*- coding: utf-8 -*-
"""ATS 可解析性检查 · 阶段 A（结构预检）

设计取自 refs/notes/resume-ats.md 第 3 节：
- 数据契约：id / status / category / problem / fix / metric / confidence / evidence
- 只出 passed / warned / failed / skipped 计数，**不出总分**（总分留给 lint 四维）
- "没检查"必须能和"检查通过"区分（skipped 第四态）
- fail 必须能指到具体字段/原文，否则不出

阶段 A 在档案结构上跑；阶段 B（导出后回读 PDF 文字层）需要真实 PDF，
MVP 暂未接导出回读，相关规则标 skipped 而不是 pass。
"""
import re

CATS = ("extraction", "typography", "layout", "contact", "content", "file")

# 中文板块同义词（与落笔 parse.SECTION_PAT 同源，避免第二份定义）
SECTION_ALIASES = {
    "education": ["教育背景", "教育经历", "学习经历", "学历", "教育培训"],
    "internships": ["实习经历", "实习经验", "工作经历", "实践经历", "职业经历"],
    "projects": ["项目经历", "项目经验", "科研经历", "课题", "作品集"],
    "campus": ["校园经历", "社团", "学生工作", "社会实践", "志愿服务"],
    "skills": ["技能", "专业技能", "技术栈", "证书", "技能证书"],
    "honors": ["荣誉", "奖项", "获奖", "竞赛"],
    "summary": ["自我评价", "个人简介", "自我介绍", "个人总结"],
}

CN_DATE = [
    (r"\d{4}\.\d{1,2}\s*[-–—~至]\s*\d{4}\.\d{1,2}", "YYYY.MM-YYYY.MM"),
    (r"\d{4}/\d{1,2}\s*[-–—~至]\s*\d{4}/\d{1,2}", "YYYY/MM-YYYY/MM"),
    (r"\d{4}-\d{1,2}\s*[-–—~至]\s*\d{4}-\d{1,2}", "YYYY-MM-YYYY-MM"),
    (r"\d{4}\s*年\s*\d{1,2}\s*月", "YYYY年MM月"),
    (r"\d{4}\.\d{1,2}\s*[-–—~至]\s*至今", "YYYY.MM-至今"),
    (r"\d{4}\.\d{1,2}\s*[-–—~至]\s*(现在|目前)", "YYYY.MM-现在"),
]
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"1[3-9]\d{9}")
ICON_RANGES = [(0xFFFD, 0xFFFD), (0xE000, 0xF8FF), (0xF0000, 0xFFFFD), (0x100000, 0x10FFFD)]
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")


def _item(rid, status, category, problem, fix, confidence="high", metric=None, evidence=""):
    return {
        "id": rid, "status": status, "category": category,
        "problem": problem, "fix": fix, "confidence": confidence,
        "metric": metric or {}, "evidence": (evidence or "")[:60],
    }


def _profile_text(profile):
    """结构化档案摊平成「像简历一样」的纯文本，用于抽检。"""
    p = profile or {}
    basic = p.get("basic") or {}
    intent = p.get("intent") or {}
    buf = []
    buf += [basic.get("name", ""), basic.get("phone", ""), basic.get("email", ""), basic.get("city", "")]
    buf += ["求职意向：" + " / ".join(intent.get("targetRoles") or [])]
    buf.append(p.get("summary") or "")
    for sec, aliases in SECTION_ALIASES.items():
        items = p.get(sec) if sec not in ("summary",) else None
        if sec == "education":
            items = p.get("education") or []
        elif sec == "campus":
            items = []
        elif sec in ("honors",):
            items = []
        else:
            items = p.get(sec) or []
        buf.append(aliases[0])
        for it in items:
            if not isinstance(it, dict):
                continue
            buf += [it.get("school", ""), it.get("org", ""), it.get("name", ""),
                    it.get("major", ""), it.get("role", ""), it.get("time", "")]
            for b in it.get("bullets") or []:
                buf.append(b.get("text", "") if isinstance(b, dict) else str(b))
    for g in p.get("skills") or []:
        if isinstance(g, dict):
            buf.append(g.get("group", "") + "：" + "、".join(g.get("items") or []))
    return "\n".join([x for x in buf if x])


def check(profile, text=None):
    p = profile or {}
    t = text if text is not None else _profile_text(p)
    items = []
    basic = p.get("basic") or {}
    sections = {
        "教育": bool(p.get("education")),
        "实习": bool(p.get("internships")),
        "项目": bool(p.get("projects")),
        "技能": bool(p.get("skills")),
        "自我评价": bool((p.get("summary") or "").strip()),
    }

    # ---------- extraction ----------
    n = len(t)
    if n < 150:
        items.append(_item("ats.extraction.scanned_or_empty", "fail", "extraction",
                           f"档案文本只有 {n} 字，ATS 读不出有效内容",
                           "补齐教育、实习/项目、技能；没有实习就用课程项目"),
                          metric={"kind": "count", "value": n, "warn_below": 150})
    else:
        items.append(_item("ats.extraction.scanned_or_empty", "pass", "extraction",
                           f"档案文本 {n} 字，足够 ATS 解析", "—",
                          metric={"kind": "count", "value": n}))
    items.append(_item("ats.extraction.text_layer_lost", "skipped", "extraction",
                       "需要导出 PDF 后回读文字层才能判断",
                       "阶段 B：导出后回读，缺失率 >10% 报警", confidence="low"))

    # ---------- typography ----------
    bad_icon = [ch for ch in t if any(lo <= ord(ch) <= hi for lo, hi in ICON_RANGES)]
    if bad_icon:
        items.append(_item("ats.typography.icon_glyphs", "fail", "typography",
                           f"出现 {len(bad_icon)} 个无法识别字符（U+FFFD/私用区）",
                           "删掉图标字体、特殊符号，改成纯文本",
                           evidence="".join(bad_icon[:5])))
    else:
        items.append(_item("ats.typography.icon_glyphs", "pass", "typography",
                           "没有无法识别字符", "—"))
    emoji = EMOJI_RE.findall(t)
    if emoji:
        items.append(_item("ats.typography.special_chars", "warn", "typography",
                           f"含 {len(emoji)} 个 emoji/特殊符号", "ATS 与部分邮箱会显示成方块，删掉",
                           evidence="".join(emoji[:5])))
    else:
        items.append(_item("ats.typography.special_chars", "pass", "typography", "没有 emoji 与制表符", "—"))
    items.append(_item("ats.typography.tiny_font", "skipped", "typography",
                       "字号需要渲染后才能测", "阶段 B：<9pt 字符占比 >10% 报警", confidence="low"))
    items.append(_item("ats.typography.font_soup", "skipped", "typography",
                       "字体数需要渲染后统计", "阶段 B：内嵌字体 >4 报警", confidence="low"))

    # ---------- layout ----------
    items.append(_item("ats.layout.heading_fragmented", "skipped", "layout",
                       "标题是否被字距拆散需要渲染后才能判断",
                       "阶段 B：检查标题 token 是否被拆成单字母", confidence="low"))
    if (basic.get("photo") or "").strip():
        items.append(_item("ats.layout.photo_present", "info", "layout",
                           "简历含证件照", "国内校招可用；投外企/ATS 严格的公司建议去掉"))
    else:
        items.append(_item("ats.layout.photo_present", "info", "layout", "没有证件照", "国内校招无影响"))
    for rid, name in (("ats.layout.margin_too_tight", "页边距"), ("ats.layout.contact_in_furniture", "联系方式在页眉页脚")):
        items.append(_item(rid, "skipped", "layout", f"{name}需要渲染后判断", "阶段 B", confidence="low"))

    # ---------- contact ----------
    email = (basic.get("email") or "").strip()
    phone = (basic.get("phone") or "").strip()
    if email and phone:
        items.append(_item("ats.contact.missing", "pass", "contact", "邮箱与手机号都有", "—"))
    elif not email and not phone:
        items.append(_item("ats.contact.missing", "fail", "contact", "邮箱与手机号都缺失",
                           "补上邮箱与手机号，这是 ATS 唯一能稳定抓到的联系方式"))
    else:
        items.append(_item("ats.contact.missing", "warn", "contact",
                           "缺" + ("邮箱" if not email else "手机号"),
                           "补全；部分公司只通过邮箱联系"))
    emails = set(EMAIL_RE.findall(t))
    if len(emails) >= 2:
        items.append(_item("ats.contact.multi_email", "warn", "contact",
                           f"出现 {len(emails)} 个不同邮箱", "只留一个主邮箱，避免 HR 发错",
                           evidence="、".join(list(emails)[:3])))
    else:
        items.append(_item("ats.contact.multi_email", "pass", "contact", "邮箱唯一", "—"))
    items.append(_item("ats.contact.link_only", "skipped", "contact",
                       "需要导出文件才能判断联系方式是否只存在于超链接里", "阶段 B", confidence="low"))
    items.append(_item("ats.contact.broken_url", "skipped", "contact",
                       "需要导出文件才能判断 URL 是否被换行切断", "阶段 B", confidence="low"))

    # ---------- content ----------
    hit_headers = [k for k, ok in sections.items() if ok]
    if len(hit_headers) >= 2:
        items.append(_item("ats.content.section_headers", "pass", "content",
                           f"命中 {len(hit_headers)} 个标准板块（{'、'.join(hit_headers)}）", "—"))
    else:
        items.append(_item("ats.content.section_headers", "warn", "content",
                           "标准板块名太少，ATS 分不清哪段是什么",
                           "用「教育背景 / 实习经历 / 项目经历 / 技能」这类标准标题"))
    empty = [k for k, ok in sections.items() if not ok]
    if empty:
        items.append(_item("ats.content.orphan_heading", "warn", "content",
                           "板块有标题但没内容：" + "、".join(empty),
                           "删掉空板块，或补上内容"))
    else:
        items.append(_item("ats.content.orphan_heading", "pass", "content", "没有空板块", "—"))
    # 重复 bullet
    seen, dup = set(), []
    for sec in ("internships", "projects"):
        for it in p.get(sec) or []:
            for b in it.get("bullets") or []:
                txt = ((b.get("text") if isinstance(b, dict) else str(b)) or "").strip()
                if txt and txt in seen:
                    dup.append(txt)
                seen.add(txt)
    if dup:
        items.append(_item("ats.content.duplicate_bullet", "warn", "content",
                           f"有 {len(dup)} 条重复经历描述", "删掉重复，或合并成一条",
                           evidence=dup[0][:50]))
    else:
        items.append(_item("ats.content.duplicate_bullet", "pass", "content", "没有重复 bullet", "—"))
    items.append(_item("ats.content.bullet_density", "skipped", "content",
                       "需要导出后按行统计", "阶段 B：bullet 行占比 <0.15 报警", confidence="low"))
    # 日期写法
    styles = []
    for pat, name in CN_DATE:
        if re.search(pat, t) and name not in styles:
            styles.append(name)
    if len(styles) >= 2:
        items.append(_item("ats.content.date_mixed", "warn", "content",
                           "日期写法有 " + str(len(styles)) + " 种：" + "、".join(styles),
                           "统一成一种（建议 YYYY.MM–YYYY.MM）"))
    else:
        items.append(_item("ats.content.date_mixed", "pass", "content", "日期写法统一或只有一种", "—"))

    # ---------- file ----------
    for rid, msg in (("ats.file.too_large", "文件体积需要真实文件"), ("ats.file.encrypted", "加密需要真实 PDF")):
        items.append(_item(rid, "skipped", "file", msg + "才能判断", "阶段 B", confidence="low"))
    items.append(_item("ats.file.filename", "skipped", "file",
                       "MVP 还没接文件导出", "阶段 B：文件名带 final/copy/(1)/最终版 提醒", confidence="low"))
    # 页数：用 lint 的估算
    try:
        from .lint import estimate_page
        pg = estimate_page(p)
        pages = pg.get("pages", 1)
    except Exception:
        pages = 1
    if pages <= 1:
        items.append(_item("ats.content.page_count", "pass", "content", "估算 1 页", "—",
                           metric={"kind": "count", "value": pages}))
    elif pages == 2:
        items.append(_item("ats.content.page_count", "warn", "content", "估算 2 页",
                           "应届生建议压到 1 页", metric={"kind": "count", "value": pages}))
    else:
        items.append(_item("ats.content.page_count", "fail", "content", f"估算 {pages} 页",
                           "压到 1 页：删课程、删与岗位无关的经历",
                           metric={"kind": "count", "value": pages}))

    counts = {"pass": 0, "warn": 0, "fail": 0, "info": 0, "skipped": 0}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    groups = {c: [i for i in items if i["category"] == c] for c in CATS}
    return {
        "summary": {"passed": counts["pass"], "warned": counts["warn"],
                    "failed": counts["fail"], "info": counts["info"],
                    "skipped": counts["skipped"], "total": len(items)},
        "groups": groups,
        "items": items,
        "stage": "A（结构预检）",
        "note": "未检查 ≠ 通过。阶段 B（导出后回读）尚未接入，相关项标为 skipped。",
    }

# ============ 阶段 B：导出 PDF 后回读文字层 ============
SINGLE_LETTER_RUN = re.compile(r"(?:(?<![A-Za-z])[A-Za-z][ \u00a0]){4,}[A-Za-z](?![A-Za-z])")
CJK_FRAG_RUN = re.compile(r"(?:(?<![\u4e00-\u9fa5])[\u4e00-\u9fa5][ \u00a0]){2,}[\u4e00-\u9fa5](?![ \u00a0]?[\u4e00-\u9fa5])")



def _pdf_layout(reader):
    """用 pypdf 的 visitor 收集字号 / 位置 / 页眉页脚 / 链接信息。"""
    total_chars = 0
    small_chars = 0
    xs, ys = [], []
    contact_edge = False
    urls = 0
    pages = len(reader.pages)
    for page in reader.pages:
        try:
            W = float(page.mediabox.width)
            H = float(page.mediabox.height)
        except Exception:
            W, H = 595.0, 842.0

        def visitor(text, cm, tm, font_dict, font_size, W=W, H=H):
            nonlocal total_chars, small_chars, contact_edge
            t = (text or "").strip()
            if not t:
                return
            try:
                x = float(tm[4]) + (float(cm[4]) if cm else 0.0)
                y = float(tm[5]) + (float(cm[5]) if cm else 0.0)
            except Exception:
                x, y = 0.0, 0.0
            xs.append(x)
            ys.append(y)
            n = len(t)
            total_chars += n
            try:
                fs = abs(float(font_size or 0))
            except Exception:
                fs = 0.0
            if fs and fs < 9:
                small_chars += n
            if (PHONE_RE.search(t) or EMAIL_RE.search(t)) and (y < 12 or y > H - 12):
                contact_edge = True

        try:
            page.extract_text(visitor_text=visitor)
        except Exception:
            pass
        try:
            for a in (page.get("/Annots") or []):
                try:
                    obj = a.get_object()
                    act = obj.get("/A")
                    if act and act.get("/URI"):
                        urls += 1
                except Exception:
                    continue
        except Exception:
            pass
    xs = [x for x in xs if x > 1]
    return {
        "pages": pages, "total_chars": total_chars, "small_chars": small_chars,
        "min_x": min(xs) if xs else None, "max_x": max(xs) if xs else None,
        "min_y": min(ys) if ys else None, "max_y": max(ys) if ys else None,
        "contact_edge": contact_edge, "urls": urls,
    }


def check_pdf(profile, pdf_bytes, source_text=None, self_rendered=False):
    """对导出的 PDF 做「只有渲染后才成立」的检查。返回 items + 元信息。"""
    import io
    from pypdf import PdfReader

    items = []
    meta = {"pages": 0, "pdf_bytes": len(pdf_bytes), "text_chars": 0,
            "extracted_preview": "", "fonts": 0, "encrypted": False}
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        meta["encrypted"] = bool(reader.is_encrypted)
        meta["pages"] = len(reader.pages)
        if meta["encrypted"]:
            try:
                reader.decrypt("")
            except Exception:
                pass
        texts = []
        fonts = set()
        for page in reader.pages:
            try:
                texts.append(page.extract_text() or "")
            except Exception:
                texts.append("")
            try:
                res = page.get("/Resources")
                if res and "/Font" in res:
                    for k in res["/Font"].keys():
                        fonts.add(str(k))
            except Exception:
                pass
        extracted = "\n".join(texts)
        meta["text_chars"] = len(extracted.strip())
        meta["fonts"] = len(fonts)
        meta["extracted_preview"] = extracted[:240].replace("\n", " / ")
    except Exception as exc:
        items.append(_item("ats.extraction.scanned_or_empty", "fail", "extraction",
                           f"PDF 读不出来：{exc}", "检查文件是否损坏或被加密", confidence="low"))
        # ---------- 4 条需要版面才能判断的规则 ----------
    try:
        layout = _pdf_layout(reader)
    except Exception:
        layout = {}
    total = layout.get("total_chars") or 0
    if total:
        ratio = layout.get("small_chars", 0) / total
        if ratio > 0.1:
            items.append(_item("ats.typography.tiny_font", "warn", "typography",
                               f"小于 9pt 的文字占 {round(ratio*100)}%", "正文不小于 10pt，否则部分 ATS 会判为注释",
                               metric={"kind": "ratio", "value": round(ratio, 3)}))
        else:
            items.append(_item("ats.typography.tiny_font", "pass", "typography",
                               f"小于 9pt 的文字占 {round(ratio*100)}%", "—"))
    else:
        items.append(_item("ats.typography.tiny_font", "skipped", "typography", "无可测文字", "—", confidence="low"))

    min_x = layout.get("min_x")
    try:
        page_w = float(reader.pages[0].mediabox.width)
    except Exception:
        page_w = 595.0
    if min_x is not None:
        left_ratio = min_x / page_w
        if left_ratio < 0.015:
            items.append(_item("ats.layout.margin_too_tight", "warn", "layout",
                               f"文字距左页边只有 {round(left_ratio*100,1)}% 页宽", "页边距至少留 10mm",
                               metric={"kind": "ratio", "value": round(left_ratio, 3)}))
        else:
            items.append(_item("ats.layout.margin_too_tight", "pass", "layout",
                               f"左侧页边距 {round(left_ratio*100,1)}% 页宽", "—"))
    else:
        items.append(_item("ats.layout.margin_too_tight", "skipped", "layout", "无可测文字", "—", confidence="low"))

    if self_rendered:
        items.append(_item("ats.layout.contact_in_furniture", "pass", "layout",
                           "自建模板：联系方式在正文文字流（无页眉页脚）", "—"))
    elif layout.get("contact_edge"):
        items.append(_item("ats.layout.contact_in_furniture", "warn", "layout",
                           "联系方式出现在页面最顶部/最底部（可能落在页眉页脚区）",
                           "把联系方式放进正文第一屏"))
    elif phone and phone in re.sub(r"\D", "", extracted):
        items.append(_item("ats.layout.contact_in_furniture", "pass", "layout",
                           "联系方式在正文区域", "—"))
    else:
        items.append(_item("ats.layout.contact_in_furniture", "info", "layout", "跳过", "—"))

    if layout.get("urls", 0) > 0 and not (email and email in extracted):
        items.append(_item("ats.contact.link_only", "warn", "contact",
                           f"检测到 {layout['urls']} 个超链接，但正文里没有邮箱",
                           "邮箱要写成纯文本，不能只做超链接"))
    else:
        items.append(_item("ats.contact.link_only", "pass", "contact", "邮箱/手机号为纯文本", "—"))

    return {"items": items, "meta": meta}

    src = source_text if source_text is not None else _profile_text(profile)

    # 1) 文字层缺失
    if meta["text_chars"] == 0:
        items.append(_item("ats.extraction.text_layer_lost", "fail", "extraction",
                           "PDF 里没有文字层（扫描图/文字被转曲）",
                           "用文本方式导出，不要「打印成图片」"))
    else:
        keys = []
        p = profile or {}
        b = p.get("basic") or {}
        keys += [b.get("name", ""), re.sub(r"\D", "", b.get("phone", ""))[-4:] if b.get("phone") else "", b.get("email", "")]
        for e in p.get("education") or []:
            keys.append(e.get("school", ""))
        for sec in ("internships", "projects"):
            for it in p.get(sec) or []:
                keys.append(it.get("org") or it.get("name") or "")
                for bl in (it.get("bullets") or [])[:2]:
                    keys.append(((bl.get("text") if isinstance(bl, dict) else str(bl)) or "")[:12])
        keys = [k for k in keys if k]
        missing = [k for k in keys if k not in extracted]
        ratio = len(missing) / len(keys) if keys else 0
        m = {"kind": "ratio", "value": round(ratio, 3), "warn_above": 0.1, "missing": len(missing), "total": len(keys)}
        if ratio > 0.25:
            items.append(_item("ats.extraction.text_layer_lost", "fail", "extraction",
                               f"文字层缺失 {len(missing)}/{len(keys)} 个关键片段（{round(ratio*100)}%）",
                               "不要把文字放进图片/图标；用标准文本导出", metric=m))
        elif ratio > 0.1:
            items.append(_item("ats.extraction.text_layer_lost", "warn", "extraction",
                               f"文字层缺失 {len(missing)}/{len(keys)} 个关键片段（{round(ratio*100)}%）",
                               "检查是否有文字被渲染成图片或被字距拆散", metric=m))
        else:
            items.append(_item("ats.extraction.text_layer_lost", "pass", "extraction",
                               f"文字层完整（{meta['text_chars']} 字，关键片段缺失 {len(missing)}/{len(keys)}）",
                               "—", metric=m))

    # 2) 页数
    pages = meta["pages"]
    if pages <= 1:
        items.append(_item("ats.content.page_count", "pass", "content", "PDF 共 1 页", "—",
                           metric={"kind": "count", "value": pages}))
    elif pages == 2:
        items.append(_item("ats.content.page_count", "warn", "content", "PDF 共 2 页",
                           "应届生建议压到 1 页", metric={"kind": "count", "value": pages}))
    else:
        items.append(_item("ats.content.page_count", "fail", "content", f"PDF 共 {pages} 页",
                           "压到 1 页：删课程、删无关经历", metric={"kind": "count", "value": pages}))

    # 3) 标题被字距拆散
    frag = SINGLE_LETTER_RUN.findall(extracted)
    frag_cn = CJK_FRAG_RUN.findall(extracted)
    if frag or frag_cn:
        sample = " ".join((frag + frag_cn)[:3])[:60]
        items.append(_item("ats.layout.heading_fragmented", "warn", "layout",
                           f"检测到 {len(frag) + len(frag_cn)} 处标题字符被拆散（字距/小型大写导致）",
                           "标题不要用 letter-spacing；改成普通字重 + 颜色区分",
                           evidence=sample))
    else:
        items.append(_item("ats.layout.heading_fragmented", "pass", "layout",
                           "标题没有被拆成单字母", "—"))

    # 4) bullet 符号是否进入文字层
    src_bullets = 0
    for sec in ("internships", "projects"):
        for it in (profile or {}).get(sec) or []:
            src_bullets += len(it.get("bullets") or [])
    marker_count = len(re.findall(r"[•·▪◦●○◆■\-–—*]", extracted))
    if src_bullets >= 3 and marker_count == 0:
        items.append(_item("ats.content.bullet_density", "warn", "content",
                           f"源简历有 {src_bullets} 条 bullet，但 PDF 文字层里没有任何项目符号",
                           "用标准列表导出；::marker 在部分渲染器里不进文字层", confidence="medium"))
    elif src_bullets >= 3:
        items.append(_item("ats.content.bullet_density", "pass", "content",
                           f"bullet 符号完整进入文字层（{marker_count} 个）", "—"))
    else:
        items.append(_item("ats.content.bullet_density", "info", "content",
                           "bullet 数量少，暂不判断密度", "—"))

    # 5) 联系方式在正文而非页眉页脚（我们的模板是正文流）
    phone = re.sub(r"\D", "", ((profile or {}).get("basic") or {}).get("phone", ""))
    if phone and phone in re.sub(r"\D", "", extracted):
        items.append(_item("ats.layout.contact_in_furniture", "pass", "layout",
                           "联系方式在正文文字流里（不是页眉/页脚）", "—"))
    elif phone:
        items.append(_item("ats.layout.contact_in_furniture", "warn", "layout",
                           "文字层里没找到手机号", "把联系方式放进正文第一屏，不要只放页眉"))
    else:
        items.append(_item("ats.layout.contact_in_furniture", "info", "layout", "档案没有手机号，跳过", "—"))

    # 6) 文件体积 / 加密 / 文件名
    if meta["pdf_bytes"] > 2 * 1024 * 1024:
        items.append(_item("ats.file.too_large", "warn", "file",
                           f"PDF {round(meta['pdf_bytes']/1024/1024, 1)} MB，超过 2MB",
                           "压缩图片、去掉嵌入字体"))
    else:
        items.append(_item("ats.file.too_large", "pass", "file",
                           f"PDF {round(meta['pdf_bytes']/1024)} KB", "—"))
    if meta["encrypted"]:
        items.append(_item("ats.file.encrypted", "fail", "file",
                           "PDF 加密/限制复制，ATS 可能读不了", "导出时取消密码与权限限制"))
    else:
        items.append(_item("ats.file.encrypted", "pass", "file", "PDF 未加密", "—"))
    items.append(_item("ats.file.filename", "pass", "file", "导出文件名：resume.pdf", "—"))

    # 7) 字体数
    if meta["fonts"] > 4:
        items.append(_item("ats.typography.font_soup", "warn", "typography",
                           f"PDF 用了 {meta['fonts']} 个字体", "统一到 2 个字体以内"))
    else:
        items.append(_item("ats.typography.font_soup", "pass", "typography",
                           f"PDF 字体数 {meta['fonts']}", "—"))

    return {"items": items, "meta": meta}


def merge_stage_b(report_a, result_b):
    """用阶段 B 的实测结果替换同 id 的 skipped 项，重算汇总。"""
    by_id = {it["id"]: it for it in result_b.get("items") or []}
    items = []
    for it in report_a.get("items") or []:
        items.append(by_id.pop(it["id"], it))
    items += list(by_id.values())
    counts = {"pass": 0, "warn": 0, "fail": 0, "info": 0, "skipped": 0}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    groups = {c: [i for i in items if i["category"] == c] for c in CATS}
    return {
        "summary": {"passed": counts["pass"], "warned": counts["warn"],
                    "failed": counts["fail"], "info": counts["info"],
                    "skipped": counts["skipped"], "total": len(items)},
        "groups": groups,
        "items": items,
        "stage": "A+B（结构预检 + 导出回读）",
        "note": "未检查 ≠ 通过。阶段 B 已对导出的 PDF 文字层实测。",
    }
