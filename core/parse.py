# -*- coding: utf-8 -*-
"""
parse_resume —— 把简历原文切成结构化档案
========================================
不依赖大模型：用「板块标题 + 时间区间 + bullet 符号」三件事做启发式切分。
中文简历的排版高度套路化，规则能覆盖 80%；剩下 20% 交给用户在「档案确认」里改，
比让模型猜、然后用户从头核对要快得多。
"""
import re

from . import lexicon as LX
from .profile import new_profile, normalize

SECTION_PAT = [
    ("education", r"(教育背景|教育经历|学习经历|学历|教育培训)"),
    ("internships", r"(实习经历|工作经历|实习|工作履历|职业经历|实践经历)"),
    ("projects", r"(项目经历|项目经验|科研经历|课题|作品集|个人项目)"),
    ("skills", r"(技能|专业技能|技能证书|技术栈|能力)"),
    ("honors", r"(荣誉|奖项|获奖|证书|竞赛)"),
    ("summary", r"(自我评价|个人简介|自我介绍|个人总结|求职意向之外的)"),
    ("campus", r"(校园经历|社团|学生工作|社会实践|志愿)"),
]

BULLET_PAT = re.compile(r"^\s*([•·▪◦\-—*※>●○◆■]|\d{1,2}[.、)]|[（(]\d{1,2}[)）])\s*")
PHONE_RE = re.compile(r"1[3-9]\d{9}")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
CITY_RE = re.compile(r"(北京|上海|广州|深圳|杭州|成都|武汉|南京|西安|长沙|重庆|苏州|天津|郑州|青岛|合肥|福州|厦门|南昌|昆明|南宁|贵阳|太原|石家庄|济南|沈阳|大连|哈尔滨|长春|兰州|乌鲁木齐|呼和浩特|海口|银川|西宁|拉萨|香港|澳门|台北)")
SCHOOL_RE = re.compile(r"([\u4e00-\u9fa5]{2,12}(?:大学|学院|学校|职业技术学院|师范学院))")
DEGREE_RE = re.compile(r"(博士|硕士|研究生|本科|学士|大专|专科)")


ROLE_TAIL_RE = re.compile(
    r"(实习生|工程师|专员|助理|主管|经理|总监|负责人|发起人|创始人|组长|队长|部长|主席|班长|"
    r"干事|成员|队员|志愿者|助教|运营|开发|设计师|分析师|顾问|合伙人|店长|业务员)$"
)


def _split_role(text):
    """'某 3C 品牌 电商运营实习生' → ('某 3C 品牌', '电商运营实习生')
    只在「最后一个空格片段看起来像职位」时才切，避免把公司名切坏。"""
    if "｜" in text or "|" in text:
        parts = [x.strip() for x in re.split(r"[｜|]", text) if x.strip()]
        if len(parts) >= 2:
            return " ".join(parts[:-1]), parts[-1]
    toks = text.split()
    if len(toks) >= 2 and ROLE_TAIL_RE.search(toks[-1]):
        return " ".join(toks[:-1]), toks[-1]
    return text, ""


def _clean_line(s):
    return re.sub(r"[ \t\u3000]+", " ", s.replace("\u00a0", " ")).strip()


def parse_text(raw):
    warnings = []
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [_clean_line(x) for x in text.split("\n")]
    lines = [x for x in lines if x]

    p = new_profile()
    p["skills"] = []

    if not lines:
        return {"profile": p, "warnings": ["内容为空，请粘贴简历原文或上传 .docx/.txt"], "stats": {}}

    # ---------- 基本信息 ----------
    joined = "\n".join(lines[:40])
    m = PHONE_RE.search(joined)
    if m:
        p["basic"]["phone"] = m.group(0)
    m = EMAIL_RE.search(joined)
    if m:
        p["basic"]["email"] = m.group(0)
    m = CITY_RE.search(joined)
    if m:
        p["basic"]["city"] = m.group(1)
    for ln in lines[:6]:
        mm = re.match(r"^(?:姓名|名字)\s*[:：]\s*([\u4e00-\u9fa5]{2,4})", ln)
        if mm:
            p["basic"]["name"] = mm.group(1)
            break
    if not p["basic"]["name"]:
        for ln in lines[:4]:
            cand = re.sub(r"[\s|,，/].*$", "", ln)
            if re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", cand):
                p["basic"]["name"] = cand
                break
    roles = re.findall(r"(?:求职意向|意向岗位|目标岗位|应聘岗位)\s*[:：]\s*([^\n]{2,30})", joined)
    if roles:
        p["intent"]["targetRoles"] = [r.strip() for r in re.split(r"[、,，/|]", roles[0]) if r.strip()]
    avail = re.search(r"(每周\s*\d\s*[天日][^\n，,。]{0,20})", joined)
    if avail:
        p["intent"]["available"] = avail.group(1).strip()

    # ---------- 分块 ----------
    blocks = []           # (section, [lines])
    cur = None
    for ln in lines:
        hit = None
        if len(ln) <= 18 and not BULLET_PAT.match(ln):
            for key, pat in SECTION_PAT:
                if re.search(pat, ln) and not DATE_ANY(ln):
                    hit = key
                    break
        if hit:
            cur = hit
            rest = re.sub(r"^[^\u4e00-\u9fa5A-Za-z]*", "", ln)
            rest = re.sub(r"(教育背景|教育经历|学习经历|学历|实习经历|工作经历|实习|工作履历|职业经历|实践经历|项目经历|项目经验|科研经历|课题|作品集|个人项目|专业技能|技能证书|技术栈|荣誉|奖项|获奖|证书|竞赛|自我评价|个人简介|自我介绍|个人总结|校园经历|社团|学生工作|社会实践|志愿|技能|能力)[:：]?", "", rest).strip()
            blocks.append((hit, [rest] if rest else []))
            continue
        if cur is None:
            cur = "header"
            blocks.append((cur, []))
        blocks[-1][1].append(ln)

    def sec_lines(name):
        out = []
        for s, ls in blocks:
            if s == name:
                out += ls
        return out

    # 教育
    for ln in sec_lines("education"):
        if DATE_ANY(ln) or SCHOOL_RE.search(ln) or DEGREE_RE.search(ln):
            sch = SCHOOL_RE.search(ln)
            deg = DEGREE_RE.search(ln)
            dt = DATE_ANY(ln)
            plain = ln[:dt.start()] + " " + ln[dt.end():] if dt else ln
            plain = SCHOOL_RE.sub(" ", plain)
            plain = DEGREE_RE.sub(" ", plain)
            plain = re.sub(r"(全日制|统招|在读|应届|GPA|绩点)[^\s]*", " ", plain)
            rest = [x for x in re.split(r"[\s|｜/、,，]+", plain) if x and len(x) >= 2]
            major = rest[0] if rest else ""
            if major and major.endswith("专业"):
                major = major[:-2]
            p["education"].append({
                "school": sch.group(1) if sch else "",
                "major": major,
                "degree": deg.group(1) if deg else "",
                "time": dt.group(0) if dt else "",
                "gpa": None,
                "notes": [],
            })
    # 荣誉里提到 GPA / 排名 时补进教育
    for ln in sec_lines("honors") + sec_lines("education"):
        g = re.search(r"(GPA|绩点)\s*[:：]?\s*([\d.]+(?:\s*/\s*[\d.]+)?)", ln, re.I)
        if g and p["education"]:
            p["education"][0]["gpa"] = g.group(2)
        r = re.search(r"(专业|年级)?排名\s*[:：]?\s*(\d+\s*/\s*\d+|\d+%)", ln)
        if r and p["education"]:
            p["education"][0]["notes"].append("排名 " + r.group(2))

    # 实习 / 项目 / 校园
    def build_items(section, key_name):
        items = []
        cur_item = None
        for ln in sec_lines(section):
            is_head = bool(DATE_ANY(ln)) and not BULLET_PAT.match(ln)
            if is_head or (cur_item is None and not BULLET_PAT.match(ln) and len(ln) <= 40):
                dt = DATE_ANY(ln)
                name = ln
                if dt:
                    name = (ln[:dt.start()] + " " + ln[dt.end():])
                name = re.sub(r"^[\s|｜·,，、]+|[\s|｜·,，、]+$", "", name)
                name, role = _split_role(name)
                cur_item = {"time": dt.group(0) if dt else "", "bullets": []}
                if section == "projects":
                    cur_item["name"] = name
                    cur_item["role"] = role
                else:
                    cur_item["org"] = name
                    cur_item["role"] = role
                items.append(cur_item)
                continue
            if cur_item is None:
                cur_item = {"org" if section != "projects" else "name": "未分类经历", "role": "", "time": "", "bullets": []}
                items.append(cur_item)
            body = BULLET_PAT.sub("", ln).strip()
            if len(body) >= 6:
                cur_item["bullets"].append({"text": body, "evidence": "", "metrics": [], "status": "missing"})
        return items

    p["internships"] = build_items("internships", "org")
    p["projects"] = build_items("projects", "name")
    if not p["projects"]:
        campus = build_items("campus", "name")
        for it in campus:
            it.setdefault("name", it.pop("org", "校园经历"))
        p["projects"] = [c for c in campus if c.get("bullets")]

    # 技能
    skill_lines = sec_lines("skills") + sec_lines("honors")
    groups = []
    for ln in skill_lines:
        body = BULLET_PAT.sub("", ln).strip()
        if not body:
            continue
        if re.match(r"^(工具|业务|语言|证书|技术|其他|专业)", body) and re.search(r"[:：]", body):
            g, rest = re.split(r"[:：]", body, maxsplit=1)
            items = [x.strip() for x in re.split(r"[、,，/|；;]", rest) if x.strip()]
            groups.append({"group": g.strip(), "items": items})
        else:
            items = [x.strip() for x in re.split(r"[、,，/|；;]", body) if x.strip()]
            if items:
                groups.append({"group": "技能", "items": items})
    if groups:
        # 合并同名分组
        merged = {}
        for g in groups:
            merged.setdefault(g["group"], []).extend(g["items"])
        p["skills"] = [{"group": k, "items": list(dict.fromkeys(v))} for k, v in merged.items()]

    # 自我评价
    summ = [BULLET_PAT.sub("", x).strip() for x in sec_lines("summary")]
    summ = [x for x in summ if len(x) > 8]
    if summ:
        p["summary"] = "".join(summ)[:400]

    # ---------- 质量反馈 ----------
    total_bullets = sum(len(i.get("bullets") or []) for i in (p["internships"] + p["projects"]))
    if not p["basic"]["name"]:
        warnings.append("没识别到姓名，请在左侧档案里补上")
    if not p["basic"]["phone"] and not p["basic"]["email"]:
        warnings.append("没识别到手机号/邮箱")
    if not p["education"]:
        warnings.append("没识别到教育背景（学校名通常包含「大学/学院」）")
    if total_bullets == 0:
        warnings.append("没识别到经历条目：请确认原文每条经历都用 - / • / 1. 开头，或每行一条")
    if total_bullets and total_bullets < 4:
        warnings.append(f"只切出 {total_bullets} 条经历，可能是原文一行里塞了多条，建议在档案里拆开")

    stats = {
        "lines": len(lines),
        "bullets": total_bullets,
        "internships": len(p["internships"]),
        "projects": len(p["projects"]),
        "education": len(p["education"]),
    }
    p = normalize(p)
    return {"profile": p, "warnings": warnings, "stats": stats}


def DATE_ANY(line, strip=False):
    m = LX.DATE_RANGE_RE.search(line)
    if m:
        return m
    m2 = re.search(r"((?:19|20)\d{2})\s*[./年\-]?\s*(\d{1,2})?\s*月?\s*[-—–~至到]\s*"
                   r"((?:19|20)\d{2}|至今|现在|今)\s*[./年\-]?\s*(\d{1,2})?", line)
    if m2:
        return m2
    if strip:
        return None
    return None
