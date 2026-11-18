"""OfferFit · GitHub 参考项目定位脚本（Search API 版）

只解决「概念文档只给了搜索词」的项目：
  用 GitHub Search API 找候选仓库，按星数排序，取前 5 个候选。
说明：
  - Core API 现在额度耗尽（60/60，共享出口 IP），所以本脚本不查已知仓库元数据。
    已知仓库的 license/star 会在 clone 后从 LICENSE / README 读取，或额度恢复后补。
  - Search API 未认证限制 10 次/分钟，脚本按 7s 间隔 + 增量落盘，可重复运行。
  - 输出：refs/notes/discovery.json（每完成一个词就写一次，中断不丢进度）
用法：
    python -u tools/discover-refs.py             # 只跑还没查过的词
    python -u tools/discover-refs.py --force     # 全部重查
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(ROOT, "refs", "notes", "discovery.json")
HEADERS = {"User-Agent": "OfferFit-refs-research", "Accept": "application/vnd.github+json"}

SEARCH_TERMS = [
    "RuiC-2D",
    "mesh-avatar-studio",
    "voice-agent-kit",
    "fastRTC-voice-agent",
    "interview-manager",
    "Find_the_Skills",
    "ai-job-search",
    "VectorTalker",
    "awesome-digital-human-live2d",
    "GHOST Live2D",
    "LiteAvatar",
]


def api_get(url, tries=3):
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=25) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in (403, 429):
                wait = 62
                print(f"  [rate-limit] {exc.code}，等待 {wait}s（{attempt + 1}/{tries}）", flush=True)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError(f"请求失败：{url} ({last})")


def load_out():
    if os.path.exists(OUT_PATH):
        with open(OUT_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    return {"generated_at": "", "search": {}}


def save_out(out):
    out["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)


def main():
    force = "--force" in sys.argv
    out = load_out()
    terms = [t for t in SEARCH_TERMS if force or t not in out["search"]]
    print(f"待查询 {len(terms)} 个词（已完成 {len(out['search'])}）", flush=True)

    for i, term in enumerate(terms, 1):
        try:
            q = urllib.parse.quote(term)
            d = api_get(
                f"https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page=5"
            )
            items = []
            for it in d.get("items", [])[:5]:
                items.append(
                    {
                        "full_name": it.get("full_name"),
                        "stars": it.get("stargazers_count"),
                        "license": (it.get("license") or {}).get("spdx_id"),
                        "pushed_at": it.get("pushed_at"),
                        "description": (it.get("description") or "")[:180],
                        "url": it.get("html_url"),
                    }
                )
            out["search"][term] = items
            save_out(out)
            names = ", ".join(f"{i['full_name']}({i['stars']}★)" for i in items[:3])
            print(f"[{i}/{len(terms)}] {term} -> {names or '无结果'}", flush=True)
        except Exception as exc:
            print(f"[{i}/{len(terms)}] FAIL {term}: {exc}", flush=True)
        time.sleep(7)

    print(f"完成 -> {OUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
