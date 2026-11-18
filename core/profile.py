# -*- coding: utf-8 -*-
"""
档案（ResumeProfile）—— 单一事实源
=================================
所有工具的输入输出都围绕这一份结构；对话不靠长上下文记忆，而是每轮 patch 档案。
"""
import copy
import uuid
from datetime import datetime

EMPTY_PROFILE = {
    "id": "",
    "updatedAt": "",
    "basic": {"name": "", "phone": "", "email": "", "city": "", "photo": ""},
    "intent": {"targetRoles": [], "available": "", "expectedCity": []},
    "education": [],
    "internships": [],
    "projects": [],
    "skills": [],
    "summary": "",
    "evidence": [],
    "preferences": {"theme": "blue", "fontPct": 100, "template": "single", "tone": "克制专业"},
    "versions": [],
}


def new_profile():
    p = copy.deepcopy(EMPTY_PROFILE)
    p["id"] = "u_" + uuid.uuid4().hex[:10]
    p["updatedAt"] = datetime.now().isoformat(timespec="seconds")
    return p


def normalize(profile):
    """把前端传来的档案补全成完整结构，防止缺字段导致各工具报错。"""
    p = copy.deepcopy(EMPTY_PROFILE)
    if not isinstance(profile, dict):
        p["id"] = "u_" + uuid.uuid4().hex[:10]
        p["updatedAt"] = datetime.now().isoformat(timespec="seconds")
        return p

    def merge(dst, src):
        for k, v in (src or {}).items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                merge(dst[k], v)
            elif v is not None:
                dst[k] = v

    merge(p, profile)
    if not p.get("id"):
        p["id"] = "u_" + uuid.uuid4().hex[:10]
    p["updatedAt"] = datetime.now().isoformat(timespec="seconds")

    for sec in ("education", "internships", "projects"):
        if not isinstance(p.get(section_ok := sec), list):
            p[section_ok] = []
    for sec in ("internships", "projects"):
        for item in p[sec]:
            if not isinstance(item, dict):
                continue
            bl = item.get("bullets")
            if not isinstance(bl, list):
                item["bullets"] = []
            fixed = []
            for b in item["bullets"]:
                if isinstance(b, str):
                    fixed.append({"text": b, "evidence": "", "metrics": [], "status": "missing"})
                elif isinstance(b, dict):
                    b.setdefault("text", "")
                    b.setdefault("evidence", "")
                    b.setdefault("metrics", [])
                    b.setdefault("status", "verified" if b.get("evidence") else "missing")
                    fixed.append(b)
            item["bullets"] = fixed
    return p


def iter_bullets(profile):
    """遍历所有 bullet，产出 (bulletId, 段落类型, 段落名, bullet)。bulletId 稳定可定位。"""
    for sect, key in (("internships", "internships"), ("projects", "projects")):
        for i, item in enumerate(profile.get(sect) or []):
            title = item.get("org") or item.get("name") or f"{sect}[{i}]"
            for j, b in enumerate(item.get("bullets") or []):
                yield f"{sect}.{i}.{j}", sect, title, b


def completeness(profile):
    """档案完整度：给出每个板块的得分与待补项，用于右侧进度条。"""
    checks = [
        ("基础信息", bool(profile["basic"].get("name")) and bool(profile["basic"].get("phone")),
         "姓名与手机号是最低要求（导出 PDF 必备）"),
        ("求职意向", bool(profile["intent"].get("targetRoles")), "没有目标岗位，改写就没有方向"),
        ("教育背景", len(profile.get("education") or []) > 0, "至少一段教育经历"),
        ("实习/工作", len(profile.get("internships") or []) > 0, "实习是 HR 最看重的板块"),
        ("项目经历", len(profile.get("projects") or []) > 0, "无实习时，项目就是主战场"),
        ("技能清单", len(profile.get("skills") or []) > 0, "按「工具/业务/语言」分组"),
        ("自我评价", bool((profile.get("summary") or "").strip()), "200 字内，且不要形容词堆砌"),
        ("证据链", len(profile.get("evidence") or []) > 0, "每个数字最好挂一条证据"),
    ]
    total = len(checks)
    done = sum(1 for _, ok, _ in checks if ok)
    return {
        "score": round(done / total * 100),
        "done": done,
        "total": total,
        "items": [{"name": n, "ok": bool(ok), "hint": h} for n, ok, h in checks],
    }


def all_text(profile):
    """把档案摊平成纯文本，用于关键词匹配与相关性计算。"""
    buf = []
    b = profile.get("basic") or {}
    buf += [b.get("name", ""), b.get("city", "")]
    buf += profile.get("intent", {}).get("targetRoles") or []
    buf.append(profile.get("summary") or "")
    for e in profile.get("education") or []:
        buf += [e.get("school", ""), e.get("major", ""), e.get("degree", "")]
        buf += e.get("notes") or []
    for sect in ("internships", "projects"):
        for item in profile.get(sect) or []:
            buf += [item.get("org", ""), item.get("name", ""), item.get("role", ""), item.get("time", "")]
            for bl in item.get("bullets") or []:
                buf.append(bl.get("text", ""))
    for g in profile.get("skills") or []:
        if isinstance(g, dict):
            buf += [g.get("group", "")] + list(g.get("items") or [])
        else:
            buf.append(str(g))
    return "\n".join([x for x in buf if x])
