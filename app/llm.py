# -*- coding: utf-8 -*-
"""LLM 网关（OpenAI 兼容）

- 配置存 app/data/llm.json（本地文件，不进 git）；也支持环境变量：
  ZHILIAN_LLM_BASE / ZHILIAN_LLM_KEY / ZHILIAN_LLM_MODEL
- 只做「出题 / 追问」这类生成任务；评分仍走规则引擎（可解释、稳定）。
- 未配置或调用失败时，调用方必须回退规则引擎，不阻塞练习。
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "data" / "llm.json"

PRESETS = {
    "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-chat", "label": "DeepSeek"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini", "label": "OpenAI"},
    "custom": {"base_url": "", "model": "", "label": "自定义（OpenAI 兼容）"},
}


def _env_config():
    base = os.environ.get("ZHILIAN_LLM_BASE", "")
    key = os.environ.get("ZHILIAN_LLM_KEY", "")
    model = os.environ.get("ZHILIAN_LLM_MODEL", "")
    if base and key and model:
        return {"provider": "custom", "base_url": base, "model": model, "api_key": key}
    return None


def load_config():
    cfg = _env_config()
    if cfg:
        return cfg
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"provider": "deepseek", "base_url": PRESETS["deepseek"]["base_url"],
                "model": PRESETS["deepseek"]["model"], "api_key": ""}


def save_config(provider, base_url, model, api_key):
    cfg = {"provider": provider or "deepseek",
           "base_url": (base_url or "").strip().rstrip("/"),
           "model": (model or "").strip(),
           "api_key": (api_key or "").strip()}
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def masked_config():
    cfg = load_config()
    key = cfg.get("api_key") or ""
    return {
        "provider": cfg.get("provider", "deepseek"),
        "base_url": cfg.get("base_url", ""),
        "model": cfg.get("model", ""),
        "configured": bool(key and cfg.get("base_url") and cfg.get("model")),
        "key_tail": key[-4:] if len(key) >= 4 else "",
        "presets": {k: {"label": v["label"], "base_url": v["base_url"], "model": v["model"]} for k, v in PRESETS.items()},
    }


def configured():
    cfg = load_config()
    return bool(cfg.get("api_key") and cfg.get("base_url") and cfg.get("model"))


def chat(messages, temperature=0.5, max_tokens=1200, timeout=60):
    """返回 (ok, text_or_error)。"""
    cfg = load_config()
    if not configured():
        return False, "未配置 API Key"
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + cfg["api_key"],
        },
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return True, text.strip()
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "ignore")[:300]
        except Exception:
            pass
        return False, f"HTTP {exc.code}（{round(time.time()-t0,1)}s）：{body}"
    except Exception as exc:
        return False, f"调用失败：{exc}"


def chat_json(messages, temperature=0.4, max_tokens=1400, timeout=60):
    ok, text = chat(messages, temperature=temperature, max_tokens=max_tokens, timeout=timeout)
    if not ok:
        return False, text
    return True, _extract_json(text)


def _extract_json(text):
    """三级兜底：直接解析 → 去围栏 → 括号配对扫描。"""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except Exception:
            pass
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except Exception:
                        break
    return None


def test_connection():
    t0 = time.time()
    ok, text = chat([{"role": "user", "content": "只回复两个字符：OK"}], temperature=0, max_tokens=10, timeout=30)
    return {"ok": ok, "latency": round(time.time() - t0, 2),
            "reply": (text or "")[:40], "error": None if ok else text}
