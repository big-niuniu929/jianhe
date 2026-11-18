# -*- coding: utf-8 -*-
"""
LLM 网关 —— 只用标准库（urllib），不引入 openai SDK
==================================================
支持任何 OpenAI 兼容接口：DeepSeek / 豆包(方舟) / 通义 / Kimi / 本地 Ollama。

**双轨制（v1.5）**

轨道 A：平台额度。用服务端 config.json 里的 Key，按量扣用户的点。
轨道 B：用户自带 Key。用户填自己的 base_url + model + key，调用走他自己的额度，
        **一点不扣** —— 因为花的不是我们的钱。

这个产品卖的是"把简历改对"这件事，不是卖 token。用户愿意自己带 Key 是他的自由，
我们不该因为他不买点就锁功能。反过来，愿意用平台额度的，我们收的也是算力和
固定倍率，不是"解锁权"。这条线在 billing.py 里写死了，改这里之前先读那段注释。

没配 Key 时自动降级到规则引擎，产品依然可用——这一点对参赛演示很重要：
现场网络或额度出问题，功能不会整块消失。

配置优先级：用户自带 Key > 环境变量 > config.json > 空
"""
import base64
import hashlib
import json
import os
import secrets
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config.json"
SECRET_FILE = ROOT / "data" / ".llmsecret"

DEFAULTS = {
    "provider": "deepseek",
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-chat",
    "api_key": "",
    "timeout": 60,
}

# 给前端下拉用的预设。base_url 一律带 /v1，因为绝大多数兼容接口都是这个形状；
# ollama 例外，它自己就是 /v1。
PRESETS = [
    {"key": "deepseek", "label": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
     "model": "deepseek-chat", "hint": "便宜、中文好，默认推荐"},
    {"key": "dashscope", "label": "通义千问（阿里云）", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "model": "qwen-plus", "hint": "有免费额度期，兼容 OpenAI 格式"},
    {"key": "ark", "label": "豆包（火山方舟）", "base_url": "https://ark.cn-beijing.volces.com/api/v3",
     "model": "doubao-pro-32k", "hint": "model 要填你的接入点 ID（ep-xxx）"},
    {"key": "moonshot", "label": "Kimi（月之暗面）", "base_url": "https://api.moonshot.cn/v1",
     "model": "moonshot-v1-8k", "hint": "长文本稳"},
    {"key": "openai", "label": "OpenAI", "base_url": "https://api.openai.com/v1",
     "model": "gpt-4o-mini", "hint": "需要能访问的网络"},
    {"key": "ollama", "label": "本地 Ollama", "base_url": "http://127.0.0.1:11434/v1",
     "model": "qwen2.5:7b", "hint": "key 随便填一个非空值即可"},
    {"key": "custom", "label": "自定义（任何 OpenAI 兼容接口）", "base_url": "", "model": "",
     "hint": "填 /v1 结尾的地址"},
]


# ---------------------------------------------------------------- 配置
def load_config():
    """平台侧的配置（不含用户自带的 Key）。"""
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            cfg.update(data.get("llm") or {})
        except Exception:
            pass
    cfg["api_key"] = os.environ.get("JIANHE_LLM_KEY") or cfg.get("api_key") or ""
    cfg["base_url"] = os.environ.get("JIANHE_LLM_BASE") or cfg["base_url"]
    cfg["model"] = os.environ.get("JIANHE_LLM_MODEL") or cfg["model"]
    return cfg


# ---------------------------------------------------------------- Key 的存取
def _secret_bytes():
    """
    服务端主密钥。第一次用时生成，落在 data/.llmsecret。
    单机部署下这层保护等价于"别让用户的 key 在 sqlite 文件里裸奔"：
    有人拷走 jianhe.db 也读不出 key，但拿到整台机器的人仍然能读。
    这一点必须诚实写在这里，不能假装它是密码学意义上的加密。
    """
    if SECRET_FILE.exists():
        try:
            return base64.urlsafe_b64decode(SECRET_FILE.read_text().strip())
        except Exception:
            pass
    raw = secrets.token_bytes(32)
    SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    SECRET_FILE.write_text(base64.urlsafe_b64encode(raw).decode())
    try:
        os.chmod(SECRET_FILE, 0o600)
    except Exception:
        pass
    return raw


def _keystream(n, salt=b""):
    out = b""
    i = 0
    secret = _secret_bytes()
    while len(out) < n:
        out += hashlib.sha256(secret + salt + i.to_bytes(4, "big")).digest()
        i += 1
    return out[:n]


def encrypt_key(plain):
    plain = (plain or "").strip()
    if not plain:
        return ""
    raw = plain.encode("utf-8")
    ks = _keystream(len(raw))
    x = bytes(a ^ b for a, b in zip(raw, ks))
    return "v1:" + base64.urlsafe_b64encode(x).decode()


def decrypt_key(enc):
    enc = (enc or "").strip()
    if not enc.startswith("v1:"):
        return ""
    try:
        x = base64.urlsafe_b64decode(enc[3:])
    except Exception:
        return ""
    ks = _keystream(len(x))
    return bytes(a ^ b for a, b in zip(x, ks)).decode("utf-8", "ignore")


def _store():
    from . import store
    return store


def _user_cfg(owner):
    """取用户自己存的 Key 配置；没有或不完整就返回 None。"""
    if not owner:
        return None
    row = _store().get_llm_key(owner)
    if not row:
        return None
    key = decrypt_key(row.get("key_enc"))
    if not key or not row.get("base_url") or not row.get("model"):
        return None
    return {
        "provider": row.get("provider") or "custom",
        "base_url": row["base_url"],
        "model": row["model"],
        "api_key": key,
        "timeout": DEFAULTS["timeout"],
    }


def cfg_for(owner=None):
    """
    返回 (cfg, source)。source ∈ {'user', 'platform', None}
    source 决定要不要扣点：user 不扣，platform 扣。
    """
    u = _user_cfg(owner)
    if u:
        return u, "user"
    p = load_config()
    if p.get("api_key"):
        return p, "platform"
    return None, None


def status(owner=None):
    cfg, source = cfg_for(owner)
    if not cfg:
        return {
            "enabled": False, "provider": None, "model": None, "mode": "rules",
            "source": None,
            "note": "未配置 API Key，当前为规则引擎模式（功能可用，改写偏模板化）",
        }
    if source == "user":
        return {
            "enabled": True, "provider": cfg.get("provider"), "model": cfg.get("model"),
            "mode": "llm", "source": "user",
            "note": "正在用你自己的 Key（不扣点）",
        }
    return {
        "enabled": True, "provider": cfg.get("provider"), "model": cfg.get("model"),
        "mode": "llm", "source": "platform",
        "note": "已接入平台大模型（按量扣点）",
    }


def mine(owner):
    """给前端回显用：绝不返回明文 key，只回后 4 位。"""
    row = _store().get_llm_key(owner) if owner else None
    if not row:
        return None
    return {
        "provider": row.get("provider"),
        "base_url": row.get("base_url"),
        "model": row.get("model"),
        "keyTail": row.get("key_tail") or "",
        "lastOk": row.get("last_ok"),
        "lastErr": row.get("last_err") or "",
        "createdAt": row.get("created_at"),
    }


def save_key(owner, provider, base_url, model, api_key):
    base_url = (base_url or "").strip().rstrip("/")
    model = (model or "").strip()
    api_key = (api_key or "").strip()
    if not base_url or not model or not api_key:
        return False, "三个都要填：接口地址、模型名、Key"
    if not (base_url.startswith("http://") or base_url.startswith("https://")):
        return False, "接口地址要以 http:// 或 https:// 开头"
    _store().set_llm_key(owner, base_url, model, provider or "custom",
                         encrypt_key(api_key), api_key[-4:])
    return True, ""


def clear_key(owner):
    _store().delete_llm_key(owner)


# ---------------------------------------------------------------- 调用
def _post(cfg, prompt, system, temperature, timeout=None):
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + cfg["api_key"],
    })
    with urllib.request.urlopen(req, timeout=timeout or cfg.get("timeout", 60)) as resp:
        return json.loads(resp.read().decode("utf-8"))


def chat(prompt, system="你是落笔，一个克制、专业的求职材料助手。", temperature=0.3, cfg=None, owner=None):
    return chat_usage(prompt, system, temperature, cfg=cfg, owner=owner)[0]


def chat_usage(prompt, system="你是落笔，一个克制、专业的求职材料助手。",
               temperature=0.3, cfg=None, owner=None):
    """
    返回 (content, usage)。
    为什么要返回 usage：按量计费的产品必须知道自己花了多少钱。
    不传 cfg 时按 owner 解析（用户 Key 优先）；传了 cfg 就用它，
    测试连通性时走这条路。
    """
    if cfg is None:
        cfg, _src = cfg_for(owner)
    if not cfg or not cfg.get("api_key"):
        raise RuntimeError("LLM 未配置")
    data = _post(cfg, prompt, system, temperature)
    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage") or {}
    return content, {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }


def callable_or_none(owner=None):
    """给 rewrite/interview 用：有 Key 就返回 chat，没有就返回 None。"""
    cfg, _src = cfg_for(owner)
    if not cfg:
        return None
    return lambda prompt, system="你是落笔，一个克制、专业的求职材料助手。", temperature=0.3: \
        chat(prompt, system, temperature, cfg=cfg)


def test_cfg(cfg):
    """
    连通性测试。刻意要一句短回答，避免用户点一下「测试」就烧掉几千 token。
    错误信息做成人能看懂的：401 是 key 错，404 多半是 base_url 少了 /v1。
    """
    import time as _t
    t0 = _t.time()
    try:
        content, usage = chat_usage("回复两个字：可用", "你是一个连通性测试，只需要极简回复。",
                                    temperature=0, cfg=cfg)
        return {
            "ok": True,
            "ms": int((_t.time() - t0) * 1000),
            "reply": (content or "").strip()[:40],
            "model": cfg.get("model"),
            "tokens": usage.get("total_tokens"),
        }
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "ignore")[:200]
        except Exception:
            pass
        msg = {
            401: "Key 不对，或没有这个模型的权限（401）",
            403: "Key 没有权限（403）",
            404: "接口地址找不到（404）—— 多数是 base_url 少了 /v1 或多了斜杠",
            429: "额度用完或触发限流（429）",
        }.get(e.code, f"HTTP {e.code}")
        return {"ok": False, "error": msg, "detail": detail}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"连不上这个地址：{e.reason}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
