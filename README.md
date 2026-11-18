# 落笔 · 求职前置 v1.3

简历的攻击面分析器：不帮你写漂亮话，只帮你把做过的事写成经得起追问的话。

零第三方依赖，只要 Python 3.9+：

```bash
python server.py                        # http://127.0.0.1:8848
JH_ADMIN_KEY=你的密钥 python server.py    # 运营后台 /admin?key=...
```

这一版是**零依赖单文件服务**的形态：`server.py` + `core/`（规则引擎）+ `web/`（原生 JS，无构建）。最新形态见 `v0.2-offerfit` tag（FastAPI 重写），仓库主页 README 有两条版本线的完整说明。

详细功能与设计说明见 [`docs/README-落笔v1.3.md`](docs/README-落笔v1.3.md)。

`data/*.db` 未入库；`data/sample_profile.json` 是脱敏后的演示档案。
