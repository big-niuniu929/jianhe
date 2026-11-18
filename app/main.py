# -*- coding: utf-8 -*-
"""OfferFit · FastAPI 后端（MVP v0.1）

只做一件事：把上版「落笔」验证过的规则引擎（诊断 / JD 匹配 / 改写 / 追问 / 陪练评分）
接成 HTTP API，给前端一个能真正跑起来的闭环。

运行：
    cd app
    python -m uvicorn main:app --host 127.0.0.1 --port 8848
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

from engine import jd as jd_mod  # noqa: E402
from engine import lint as lint_mod  # noqa: E402
from engine import interview as interview_mod  # noqa: E402
from engine import rewrite as rewrite_mod  # noqa: E402
from engine import drill as drill_mod  # noqa: E402
from engine import profile as profile_mod  # noqa: E402
from engine import ats as ats_mod  # noqa: E402
from engine import parse as parse_mod  # noqa: E402
from render_pdf import build_resume_html, render_pdf  # noqa: E402
import stt as stt_mod  # noqa: E402
import llm as llm_mod  # noqa: E402
from store import Store  # noqa: E402

app = FastAPI(title="OfferFit API", version="0.7.0")
DATA_DIR = APP_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
OLD_DB = DATA_DIR / "zhilian.db"
NEW_DB = DATA_DIR / "offerfit.db"
def _migrate_db(old_path, new_path):
    """用 SQLite 的 backup API 迁移（会把 WAL 里的数据一起带上，直接复制文件会丢 WAL）。"""
    import sqlite3
    src = sqlite3.connect(str(old_path))
    try:
        dst = sqlite3.connect(str(new_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


if (not NEW_DB.exists()) and OLD_DB.exists():
    _migrate_db(OLD_DB, NEW_DB)
store = Store(NEW_DB)
SAMPLE = json.loads((APP_DIR / "data" / "sample_profile.json").read_text(encoding="utf-8"))
STATIC = APP_DIR / "static"
TTS_DIR = APP_DIR / "data" / "tts"
TTS_DIR.mkdir(parents=True, exist_ok=True)


def current_profile() -> dict:
    p = store.get_profile()
    if not p:
        p = store.save_profile(SAMPLE)
    return profile_mod.normalize(p)


def find_bullet(profile: dict, bid: str):
    """bid 形如 internships.0.1 → 返回可修改的 bullet 字典；找不到返回 None。"""
    try:
        sect, i, j = bid.split(".")
        return profile[sect][int(i)]["bullets"][int(j)]
    except (ValueError, KeyError, IndexError, TypeError):
        return None


# ---------------- 请求模型 ----------------
class JDIn(BaseModel):
    jd: str = ""


class BulletIn(BaseModel):
    bid: str
    text: str
    evidence: Optional[str] = ""


class RewriteIn(BaseModel):
    text: str
    evidence: Optional[str] = ""
    jdWords: Optional[list[str]] = []


class PracticeIn(BaseModel):
    role: str = ""
    jd: str = ""
    mode: str = "text"
    persona: str = "senior"
    rounds: int = 5


class AnswerIn(BaseModel):
    sid: str
    answer: str


class SidIn(BaseModel):
    sid: str


class AppIn(BaseModel):
    company: str
    role: str
    stage: str = "已投"
    note: str = ""


# ---------------- 基础 ----------------
@app.get("/checklist")
def checklist() -> FileResponse:
    return FileResponse(str(APP_DIR.parent / "测试清单.html"), media_type="text/html")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "service": "offerfit", "version": "0.7.0", "runtime": sys.version.split()[0]}


@app.get("/api/bootstrap")
def bootstrap() -> dict:
    p = current_profile()
    store.ensure_base_variant(p)
    lint = lint_mod.lint(p)
    latest = store.latest_report()
    return {
        "profile": p,
        "completeness": profile_mod.completeness(p),
        "lint": lint,
        "ats": ats_mod.check(p),
        "apps": store.list_apps(),
        "sessions": store.list_sessions(5),
        "latest": latest,
        "activeVariant": store.active_variant_id(),
    }


# ---------------- 一岗一版 ----------------
class VariantIn(BaseModel):
    name: str
    jd: str = ""


class VariantActivateIn(BaseModel):
    id: str


def _variant_brief(v: dict) -> dict:
    try:
        prof = json.loads(v.get("profile") or "{}")
    except Exception:
        prof = {}
    l = lint_mod.lint(prof) if prof else {"score": 0, "grade": "—"}
    return {
        "id": v["id"], "name": v["name"], "jd": (v.get("jd") or "")[:120],
        "active": bool(v.get("is_active")), "score": l.get("score", 0), "grade": l.get("grade", "—"),
        "updatedAt": v.get("updated_at"),
    }


@app.get("/api/variants")
def api_variants() -> dict:
    p = current_profile()
    store.ensure_base_variant(p)
    return {"variants": [_variant_brief(v) for v in store.list_variants()],
            "active": store.active_variant_id()}


@app.post("/api/variants")
def api_create_variant(body: VariantIn) -> dict:
    p = current_profile()
    store.ensure_base_variant(p)
    cur = store.active_variant_id() or "base"
    store.save_variant_profile(cur, p)          # 先保存当前版本的改动
    vid = store.create_variant(body.name.strip() or "新版本", body.jd, p)
    return {"ok": True, "id": vid}


@app.post("/api/variants/activate")
def api_activate_variant(body: VariantActivateIn) -> dict:
    target = store.get_variant(body.id)
    if not target:
        return {"ok": False, "error": "找不到这个版本"}
    current = current_profile()
    cur_id = store.active_variant_id()
    if cur_id:
        store.save_variant_profile(cur_id, current)   # 切走前保存
    try:
        prof = json.loads(target["profile"])
    except Exception:
        prof = {}
    prof = profile_mod.normalize(prof)
    store.save_profile(prof)
    store.set_active_variant(body.id)
    return {"ok": True, "profile": prof, "jd": target.get("jd") or "",
            "lint": lint_mod.lint(prof), "ats": ats_mod.check(prof),
            "completeness": profile_mod.completeness(prof)}


@app.delete("/api/variants/{vid}")
def api_delete_variant(vid: str) -> dict:
    ok = store.delete_variant(vid)
    return {"ok": ok, "error": None if ok else "通用底版不可删除"}


def _bullet_texts(profile: dict):
    out = []
    for sec in ("internships", "projects"):
        for item in profile.get(sec) or []:
            for b in item.get("bullets") or []:
                t = (b.get("text") if isinstance(b, dict) else str(b)) or ""
                if t.strip():
                    out.append(t.strip())
    return out


@app.get("/api/variants/diff")
def api_variant_diff(vid: str = "base") -> dict:
    target = store.get_variant(vid)
    if not target:
        return {"ok": False, "error": "找不到对比版本"}
    try:
        base = profile_mod.normalize(json.loads(target["profile"]))
    except Exception:
        base = profile_mod.normalize({})
    cur = current_profile()
    cur_texts = set(_bullet_texts(cur))
    base_texts = set(_bullet_texts(base))
    added = len(cur_texts - base_texts)
    removed = len(base_texts - cur_texts)
    # 修改：同一位置（第 i 条）文本不同
    cur_list, base_list = _bullet_texts(cur), _bullet_texts(base)
    changed_pairs = []
    for a, b in zip(base_list, cur_list):
        if a != b:
            changed_pairs.append({"from": a, "to": b})
    added_list = sorted(cur_texts - base_texts)[:10]
    removed_list = sorted(base_texts - cur_texts)[:10]
    return {"ok": True, "baseName": target.get("name"), "added": added,
            "removed": removed, "changed": len(changed_pairs),
            "bulletsAdded": added_list, "bulletsRemoved": removed_list,
            "bulletsChanged": changed_pairs[:10],
            "summary": f"与「{target.get('name')}」相比：新增 {added} 条、删除 {removed} 条、修改 {len(changed_pairs)} 条"}


@app.post("/api/ats")
def api_ats() -> dict:
    return ats_mod.check(current_profile())


EXPORT_DIR = APP_DIR / "data" / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


@app.post("/api/ats/render")
def api_ats_render() -> dict:
    p = current_profile()
    report_a = ats_mod.check(p)
    try:
        pdf = render_pdf(build_resume_html(p))
    except Exception as exc:
        return {"ok": False, "error": f"渲染失败：{exc}"}
    path = EXPORT_DIR / "resume.pdf"
    path.write_bytes(pdf)
    result_b = ats_mod.check_pdf(p, pdf, self_rendered=True)
    merged = ats_mod.merge_stage_b(report_a, result_b)
    return {"ok": True, "ats": merged, "pdf": result_b["meta"], "pdf_url": "/exports/resume.pdf"}


# ---------------- 简历 ----------------
@app.post("/api/lint")
def api_lint() -> dict:
    return lint_mod.lint(current_profile())


@app.post("/api/jd")
def api_jd(body: JDIn) -> dict:
    p = current_profile()
    result = jd_mod.match(p, body.jd)
    return result


@app.post("/api/rewrite")
def api_rewrite(body: RewriteIn) -> dict:
    versions = rewrite_mod.rewrite_rules(body.text, body.jdWords or [], body.evidence or "")
    return {"versions": versions}


@app.post("/api/bullet")
def api_update_bullet(body: BulletIn) -> dict:
    p = current_profile()
    b = find_bullet(p, body.bid)
    if not b:
        return {"ok": False, "error": "找不到这条经历"}
    b["text"] = body.text
    if body.evidence is not None:
        b["evidence"] = body.evidence
    b["status"] = "verified" if (b.get("evidence") or "").strip() else "missing"
    store.save_profile(p)
    return {"ok": True, "profile": p,
            "lint": lint_mod.lint(p), "ats": ats_mod.check(p)}


class ProfileIn(BaseModel):
    profile: dict


def _extract_text(filename: str, content: bytes) -> str:
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        import io
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    if name.endswith(".docx"):
        import io
        from docx import Document
        doc = Document(io.BytesIO(content))
        parts = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        return "\n".join(parts)
    return content.decode("utf-8", errors="ignore")


@app.post("/api/parse-upload")
async def api_parse_upload(file: UploadFile = File(...)) -> dict:
    raw_bytes = await file.read()
    if len(raw_bytes) > 5 * 1024 * 1024:
        return {"ok": False, "error": "文件超过 5MB"}
    text = _extract_text(file.filename or "", raw_bytes)
    if not text.strip():
        return {"ok": False, "error": "没读到文字（可能是扫描件/空文件）"}
    parsed_raw = parse_mod.parse_text(text)
    if isinstance(parsed_raw, dict) and "profile" in parsed_raw:
        parsed = profile_mod.normalize(parsed_raw.get("profile"))
        warnings = parsed_raw.get("warnings") or []
    else:
        parsed = profile_mod.normalize(parsed_raw)
        warnings = []
    skills_preview = []
    for g in parsed.get("skills") or []:
        if isinstance(g, dict):
            skills_preview += [str(x) for x in (g.get("items") or [])]
        else:
            skills_preview.append(str(g))
    return {
        "ok": True,
        "filename": file.filename,
        "chars": len(text),
        "warnings": warnings,
        "profile": parsed,
        "completeness": profile_mod.completeness(parsed),
        "lint": lint_mod.lint(parsed),
        "preview": [{
            "section": "教育",
            "text": " / ".join(f"{e.get('school','')} {e.get('major','')}".strip() for e in parsed.get("education") or []) or "—",
        }, {
            "section": "实习",
            "text": " / ".join(f"{i.get('org','')} · {i.get('role','')}".strip() for i in parsed.get("internships") or []) or "—",
        }, {
            "section": "项目",
            "text": " / ".join((i.get("name") or "").strip() for i in parsed.get("projects") or []) or "—",
        }, {
            "section": "技能",
            "text": "、".join(skills_preview[:12]) or "—",
        }],
    }


@app.post("/api/profile/import")
def api_profile_import(body: ProfileIn) -> dict:
    p = profile_mod.normalize(body.profile)
    store.save_profile(p)
    return {"ok": True, "profile": p,
            "lint": lint_mod.lint(p),
            "ats": ats_mod.check(p),
            "completeness": profile_mod.completeness(p)}


# ---------------- LLM（配置 / 测试 / 出题 / 追问） ----------------
class LLMConfigIn(BaseModel):
    provider: str = "deepseek"
    base_url: str = ""
    model: str = ""
    api_key: str = ""


@app.get("/api/llm/config")
def api_llm_config() -> dict:
    return llm_mod.masked_config()


@app.post("/api/llm/config")
def api_llm_save(body: LLMConfigIn) -> dict:
    current = llm_mod.load_config()
    if body.api_key == "__clear__":
        llm_mod.save_config(body.provider or "deepseek",
                            body.base_url or "https://api.deepseek.com",
                            body.model or "deepseek-chat", "")
    else:
        key = body.api_key.strip() or current.get("api_key", "")
        llm_mod.save_config(body.provider, body.base_url, body.model, key)
    return {"ok": True, "config": llm_mod.masked_config()}


@app.post("/api/llm/test")
def api_llm_test() -> dict:
    return llm_mod.test_connection()


LLM_KINDS = ["数字口径", "角色边界", "失败复盘", "JD 缺口", "岗位动机", "自我认知", "团队协作", "时间管理"]


def _profile_digest(p: dict) -> str:
    b = p.get("basic") or {}
    it = p.get("intent") or {}
    lines = [f"姓名：{b.get('name','')}", f"求职意向：{' / '.join(it.get('targetRoles') or [])}"]
    for e in p.get("education") or []:
        lines.append(f"教育：{e.get('school','')} {e.get('major','')} {e.get('degree','')} {e.get('time','')}")
    for sec, label in (("internships", "实习"), ("projects", "项目")):
        for item in p.get(sec) or []:
            lines.append(f"{label}：{item.get('org') or item.get('name','')} · {item.get('role','')} {item.get('time','')}")
            for bl in (item.get("bullets") or [])[:4]:
                lines.append("  - " + ((bl.get("text") if isinstance(bl, dict) else str(bl)) or ""))
    for g in p.get("skills") or []:
        if isinstance(g, dict):
            lines.append("技能：" + "、".join(g.get("items") or []))
    return "\n".join(lines)[:2200]


def _llm_questions(p, jd, n):
    system = (
        "你是资深面试官，正在面试一个大学生实习生候选人。只输出 JSON，不要任何解释。"
        'JSON 格式：{"questions":[{"question":"问题原文","kind":"数字口径|角色边界|失败复盘|JD 缺口|岗位动机|自我认知|团队协作|时间管理","point":"考察点"}]}'
    )
    user = (
        f"候选人简历摘要：\n{_profile_digest(p)}\n\n"
        f"目标岗位 JD：\n{(jd or '（未提供，按简历推断岗位）')[:1500]}\n\n"
        f"请出 {n} 道面试题，要求：\n"
        "1. 至少一半必须引用简历里的具体内容（项目名、数字、职责），不要问泛泛的自我介绍；\n"
        "2. 如果简历里有数字，必须有一道追问数字口径；\n"
        "3. 不要问隐私（婚育、家庭、身份证）；\n"
        "4. 每题 15–45 字，像真人说话，不要用「请描述」这种模板腔。"
    )
    ok, data = llm_mod.chat_json(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.6, max_tokens=1200,
    )
    if not ok or not isinstance(data, dict):
        return None
    out = []
    for item in (data.get("questions") or [])[:n]:
        if not isinstance(item, dict):
            continue
        q = (item.get("question") or "").strip()
        if not q:
            continue
        kind = item.get("kind") or ""
        if kind not in LLM_KINDS:
            kind = "岗位动机"
        out.append({"q": q[:180], "kind": kind, "point": (item.get("point") or "考察表达与事实")[:40],
                    "frame": "", "trap": ""})
    return out or None


def _llm_followup(question, answer, history):
    system = (
        "你是面试官，判断是否需要追问。只输出 JSON，不要解释。"
        'JSON 格式：{"followup":"一句追问，或 null","reason":"为什么"}。'
        "规则：追问必须引用候选人原话里的具体点（数字、做法、结果）；"
        "回答已经足够具体、或追问会变成重复问题时，输出 null。"
    )
    user = f"历史问答：\n{history}\n\n当前题目：{question}\n候选人回答：{answer[:900]}"
    ok, data = llm_mod.chat_json(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.3, max_tokens=300,
    )
    if not ok or not isinstance(data, dict):
        return None
    fq = data.get("followup")
    if not isinstance(fq, str):
        return None
    fq = fq.strip()
    if not fq or fq.lower() in ("null", "none", "无"):
        return None
    return fq[:120]


# ---------------- 面试陪练 ----------------
def _pick_followup(d: dict) -> Optional[str]:
    asks = d.get("asks") or []
    if not asks:
        return None
    return asks[0].get("q")


@app.post("/api/practice/start")
def practice_start(body: PracticeIn) -> dict:
    p = current_profile()
    missing = []
    if body.jd.strip():
        m = jd_mod.match(p, body.jd)
        missing = m.get("keywords", {}).get("missing", [])
    n = max(1, int(body.rounds))
    questions = []
    source = "rules"
    llm_error = ""

    if llm_mod.configured():
        gen = _llm_questions(p, body.jd, n)
        if gen:
            source = "llm"
            for q in gen[:n]:
                questions.append({
                    "q": q["q"], "kind": q.get("kind", ""), "point": q.get("point", ""),
                    "frame": "", "trap": "",
                    "answer": None, "score": None, "dims": None, "fix": None, "grade": None,
                    "followed": False, "followDepth": 0,
                })
        else:
            llm_error = "LLM 出题失败，已回退规则引擎"

    if not questions:
        bank = interview_mod.generate(p, body.jd, missing, limit=max(8, n + 3))
        for q in bank.get("questions", [])[:n]:
            questions.append({
                "q": q["q"], "kind": q.get("kind", ""), "point": q.get("point", ""),
                "frame": q.get("frame", ""), "trap": q.get("trap", ""),
                "answer": None, "score": None, "dims": None, "fix": None, "grade": None,
                "followed": False, "followDepth": 0,
            })

    data = {
        "role": body.role or "目标岗位",
        "jd": body.jd,
        "mode": body.mode,
        "persona": body.persona,
        "rounds": body.rounds,
        "questions": questions,
        "cursor": 0,
        "status": "running",
        "report": None,
        "source": source,
    }
    sid = store.create_session(data)
    return {
        "sid": sid,
        "question": questions[0] if questions else None,
        "index": 0,
        "total": len(questions),
        "role": data["role"],
        "source": source,
        "llmError": llm_error,
    }


@app.post("/api/practice/answer")
def practice_answer(body: AnswerIn) -> dict:
    s = store.get_session(body.sid)
    if not s:
        return {"ok": False, "error": "找不到这场练习"}
    if s.get("status") == "finished":
        return {"ok": False, "error": "这场已经结束了", "report": s.get("report")}

    idx = int(s.get("cursor") or 0)
    qs = s.get("questions") or []
    if idx >= len(qs):
        return {"ok": False, "error": "没有待答的题"}
    q = qs[idx]

    d = drill_mod.analyze(body.answer, q["q"])
    total25 = sum(x["score"] for x in d["dims"])
    score10 = round(total25 / 100 * 10, 1)
    q["answer"] = body.answer
    q["score"] = score10
    q["dims"] = d["dims"]
    q["fix"] = d["fix"]
    q["grade"] = d["grade"]

    # 追问：优先 LLM（引用原话），失败/未配置则回落到 drill 的最弱检查点；只追一层
    follow = None
    follow_source = ""
    if not q.get("followed"):
        fq = None
        if llm_mod.configured():
            history = "".join(
                f"Q：{x.get('q','')[:60]}\nA：{(x.get('answer') or '')[:140]}\n"
                for x in qs[:idx] if x.get("answer")
            )
            fq = _llm_followup(q["q"], body.answer, history)
            if fq:
                follow_source = "llm"
        if not fq and d.get("worst") and (d.get("nbad") or 0) > 0:
            fq = _pick_followup(d)
            if fq:
                follow_source = "rules"
        if fq:
            follow = {
                "q": fq, "kind": "追问", "point": "追问 · " + q.get("kind", ""),
                "frame": q.get("frame", ""), "trap": "", "answer": None, "score": None,
                "dims": None, "fix": None, "grade": None, "followed": True,
                "followDepth": (q.get("followDepth") or 0) + 1,
                "source": follow_source,
            }
            q["followed"] = True
            qs.insert(idx + 1, follow)
            s["questions"] = qs

    s["cursor"] = idx + 1
    if s["cursor"] >= len(s["questions"]):
        s["status"] = "finished"
        s["report"] = _build_report(s)
        store.save_session(body.sid, s)
        return {
            "ok": True, "done": True, "report": s["report"], "sid": body.sid,
            "feedback": {"score": score10, "grade": d["grade"], "fix": d["fix"],
                         "dims": d["dims"], "worst": d.get("worst")},
        }

    store.save_session(body.sid, s)
    return {
        "ok": True, "done": False, "sid": body.sid,
        "question": s["questions"][s["cursor"]],
        "index": s["cursor"], "total": len(s["questions"]),
        "feedback": {"score": score10, "grade": d["grade"], "fix": d["fix"],
                     "dims": d["dims"], "worst": d.get("worst")},
        "followUp": bool(follow),
    }


def _build_report(s: dict) -> dict:
    qs = [q for q in s.get("questions", []) if q.get("answer")]
    scores = [q.get("score") or 0 for q in qs]
    total = round(sum(scores) / len(scores) * 10) if scores else 0
    dim_keys = [("spec", "具体"), ("quant", "可核查"), ("own", "角色边界"), ("result", "结果")]
    dims = []
    for key, label in dim_keys:
        vals = []
        for q in qs:
            for d in (q.get("dims") or []):
                if d.get("key") == key:
                    vals.append(d.get("score", 0))
        dims.append({"key": key, "label": label,
                     "score": round(sum(vals) / len(vals) / 25 * 100) if vals else 0})
    weakest = [q for q in qs if (q.get("score") or 0) <= 5]
    weak_points = [
        {"q": q["q"], "score": q.get("score"), "fix": q.get("fix"), "kind": q.get("kind")}
        for q in weakest
    ]
    return {
        "total": total,
        "dims": dims,
        "stats": {"answered": len(qs), "questions": len(s.get("questions", [])),
                  "avg": round(sum(scores) / len(scores), 1) if scores else 0},
        "questions": [
            {"q": q["q"], "kind": q.get("kind"), "answer": q.get("answer"),
             "score": q.get("score"), "grade": q.get("grade"), "dims": q.get("dims"),
             "fix": q.get("fix"), "frame": q.get("frame"), "trap": q.get("trap")}
            for q in qs
        ],
        "weakPoints": weak_points,
        "disclaimer": "评分只用于练习参考，不代表录用结论。",
    }


@app.post("/api/practice/finish")
def practice_finish(body: SidIn) -> dict:
    s = store.get_session(body.sid)
    if not s:
        return {"ok": False, "error": "找不到这场练习"}
    if not s.get("report"):
        s["report"] = _build_report(s)
        s["status"] = "finished"
        store.save_session(body.sid, s)
    return {"ok": True, "report": s["report"]}


@app.get("/api/practice/latest")
def practice_latest() -> dict:
    latest = store.latest_report()
    return latest or {"report": None}


@app.get("/api/sessions")
def sessions() -> dict:
    return {"sessions": store.list_sessions(10)}


@app.get("/api/sessions/{sid}")
def session_detail(sid: str) -> dict:
    s = store.get_session(sid)
    if not s:
        return {"ok": False, "error": "找不到这场练习"}
    return {
        "ok": True,
        "session": {k: s.get(k) for k in ("id", "role", "mode", "persona", "createdAt", "status", "source")},
        "report": s.get("report"),
    }


# ---------------- 数据导出 / 重置 ----------------
@app.get("/api/export/all")
def export_all() -> Response:
    p = current_profile()
    variants = []
    for v in store.list_variants():
        item = {k: v.get(k) for k in ("id", "name", "jd", "is_active", "created_at", "updated_at")}
        try:
            item["profile"] = json.loads(v.get("profile") or "{}")
        except Exception:
            item["profile"] = {}
        variants.append(item)
    data = {
        "exportedAt": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "profile": p,
        "variants": variants,
        "sessions": [store.get_session(s["id"]) for s in store.list_sessions(1000)],
        "applications": store.list_apps(),
    }
    content = json.dumps(data, ensure_ascii=False, indent=2)
    return Response(content, media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="offerfit-export.json"'})


class ResetIn(BaseModel):
    confirm: str = ""


@app.post("/api/data/reset")
def reset_data(body: ResetIn) -> dict:
    if body.confirm != "DELETE":
        return {"ok": False, "error": "确认词不对（需要输入 DELETE）"}
    store.reset_all()
    p = store.save_profile(profile_mod.normalize(SAMPLE))
    store.ensure_base_variant(p)
    return {"ok": True}


# ---------------- 语音合成（TTS） ----------------
class TTSIn(BaseModel):
    text: str
    voice: str = "zh-CN-XiaoxiaoNeural"


@app.post("/api/tts")
async def api_tts(body: TTSIn) -> dict:
    text = (body.text or "").strip()
    if not text:
        return {"ok": False, "error": "空文本"}
    text = text[:300]
    key = hashlib.sha1((body.voice + "|" + text).encode("utf-8")).hexdigest()[:16]
    path = TTS_DIR / f"{key}.mp3"
    if not path.exists():
        try:
            import edge_tts
            await edge_tts.Communicate(text, body.voice).save(str(path))
        except Exception as exc:  # 网络/依赖问题 → 前端降级浏览器 TTS
            return {"ok": False, "error": f"TTS 不可用：{exc}"}
    return {"ok": True, "url": f"/audio/{key}.mp3", "bytes": path.stat().st_size}


def build_hotwords(profile: dict) -> str:
    p = profile or {}
    words = []
    b = p.get("basic") or {}
    words += [b.get("city", "")]
    for it in p.get("internships") or []:
        words += [it.get("org", ""), it.get("role", "")]
    for it in p.get("projects") or []:
        words += [it.get("name", ""), it.get("role", "")]
    for e in p.get("education") or []:
        words += [e.get("school", ""), e.get("major", "")]
    for g in p.get("skills") or []:
        if isinstance(g, dict):
            words += list(g.get("items") or [])
        else:
            words.append(str(g))
    words += ["SKU", "JD", "AI", "Excel", "SQL"]
    clean = []
    for w in words:
        w = (w or "").strip()
        if w and w not in clean and len(w) <= 24:
            clean.append(w)
    return " ".join(clean[:40])


@app.post("/api/stt")
async def api_stt(file: UploadFile = File(...)) -> dict:
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        return {"ok": False, "error": "录音超过 15MB"}
    try:
        import asyncio

        hotwords = build_hotwords(current_profile())
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, stt_mod.transcribe_wav, data, hotwords)
        result["hotwords"] = hotwords
        return result
    except Exception as exc:
        return {"ok": False, "error": f"转写失败：{exc}"}


# ---------------- 投递台账 ----------------
@app.get("/api/apps")
def list_apps() -> dict:
    return {"apps": store.list_apps()}


@app.post("/api/apps")
def add_app(body: AppIn) -> dict:
    return {"apps": store.add_app(body.company, body.role, body.stage, body.note)}


class AppUpdateIn(BaseModel):
    stage: Optional[str] = None
    note: Optional[str] = None


@app.patch("/api/apps/{app_id}")
def update_app(app_id: int, body: AppUpdateIn) -> dict:
    store.update_app(app_id, body.stage, body.note)
    return {"apps": store.list_apps()}


@app.delete("/api/apps/{app_id}")
def delete_app(app_id: int) -> dict:
    store.delete_app(app_id)
    return {"apps": store.list_apps()}


# ---------------- 静态页面 ----------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/audio", StaticFiles(directory=TTS_DIR), name="audio")
app.mount("/exports", StaticFiles(directory=EXPORT_DIR), name="exports")
