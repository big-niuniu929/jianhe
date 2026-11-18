# 落笔 / OfferFit · 把「编简历」变成「经得起追问的简历」

> 一句话：不是帮你把话说漂亮，是帮你把做过的事写成**可以被追问**的话。
>
> 两个阶段对应两个 tag：`v1.3-luobi`（落笔 · 求职前置，零依赖单文件服务，已跑通商业闭环）→ `v0.2-offerfit`（OfferFit，FastAPI 重写 + 面试陪练 + ATS 清单）。
> 案例页：**https://big-niuNiu929.github.io/work/jianhe.html**

---

## 为什么做这个

学生写简历的通病不是「不会遣词」，是两件事：

1. **写得比自己干的多**——「独立负责」「从 0 到 1」这类表述，面试第一轮就会被问穿；
2. **写不出细节**——「负责私域运营」后面答不出「企微还是个人微信、多少群、日活多少」。

所以这个产品的判断权留在**规则**里，大模型只负责改写：每条问题都能指到具体哪一句、错在哪、依据是什么。
「大模型只负责写，判断权在规则」是它敢承诺「每条都能追问」的原因。

## 引擎（真逻辑，不是演示文案）

| 能力 | 怎么做的 | 代码 |
|---|---|---|
| **导入解析** | 板块标题 + 时间区间 + bullet 三件事做启发式切分，切完给用户逐项改 | `app/engine/parse.py` |
| **HR 诊断** | 纯规则四维打分：首屏 6 秒 / 经历价值 / 语言专业度 / 排版结构；不出总分，只出「哪句有问题」 | `lint.py`、`lexicon.py` |
| **岗位定向** | JD 拆硬门槛（学历/届别/到岗天数）+ 关键词召回（165 词，长词优先去重）+ 隐性信号（「与算法团队协作」= 协作对象，不是技能要求） | `jd.py` |
| **逐条改写** | 规则版离线可用（去口语 → 动词升级 → 结果补位 → 三版侧重）；大模型版在规则版之上；**数字防新增校验**：出现原文没有的数字直接标红 | `rewrite.py` |
| **证据核验** | 抽数字回溯 evidence 字段，无证据的数字在预览标黄 | `store.py` |
| **追问预测** | 按「可攻击点」生成 8–12 题，每题带考察点 / STAR 框架 / 常见坑 / 排序 | `interview.py` |
| **ATS 可解析性** | 结构预检 + 渲染后文本层检查，只出 passed/warned/failed 计数（总分留给四维诊断） | `ats.py`、`render_pdf.py` |
| **面试陪练** | 答题后按「会被怎么追」出追问；配语音（TTS/STT）与嘴型动画 | `drill.py`、`app/stt.py` |
| **投递台账 / 一岗一版** | SQLite；每版绑一段 JD，版本差异逐条对比 | `store.py` |

## v1.3 落笔：零依赖单文件服务

`server.py`（65 KB）+ `core/`（20 个模块）+ `web/`（原生 JS，无构建），**零第三方依赖**，只要 Python 3.9+，`python server.py` 起在 `127.0.0.1:8848`。

商业机制在这一版就跑通了：邮箱 + PBKDF2 口令、匿名可用只在导出时拦、注册送 3 次额度、兑换码自助激活（MVP 收款 = 手工发码，零支付接口）、留资、七步漏斗后台 + 成本计量。

```bash
git checkout v1.3-luobi
python server.py                    # → http://127.0.0.1:8848
JH_ADMIN_KEY=你的密钥 python server.py   # 运营后台 /admin?key=...
```

## v0.2 OfferFit：FastAPI 重写

同一套引擎拆成 `app/engine/`（parse / lint / jd / rewrite / interview / drill / ats / lexicon / profile），外面套 FastAPI + 单页前端 + SQLite：

```bash
python -m pip install -r requirements.txt      # fastapi + uvicorn 等
python start.py                                 # 或双击 启动OfferFit.bat
```

主要接口：`/api/ats`、`/api/ats/render`、`/api/lint`、`/api/jd`、`/api/rewrite`、`/api/variants`、`/api/practice/*`、`/api/tts`、`/api/stt`、`/api/export/all`。

`prototype/` 是更早的可点原型（单文件 HTML，7 个视图），用于确认信息架构——**它里面标了哪些是模拟的**，别拿它当实现看。

## 脱敏说明

- `data/*.db`（用户库、种子库）、`refs/`（23 个克隆的参考仓库，约 1.2 GB）、`__pycache__`、导出物与语音缓存都没进仓库
- `app/data/llm.json` 的密钥清空、网关换成 `gateway.example.com`（配置项在「设置」页里填自己的）
- `sample_profile.json` 里的姓名/联系方式/公司名换成示例值，作为「导入一份简历」的演示数据保留
- 文档里的本机绝对路径改成相对路径

保留的是**引擎规则、评分口径、接口与 UI**——这些是产品的本体。

## 目录

```
app/            FastAPI 服务（main.py 路由、engine/ 规则引擎、store.py 存储、static/ 前端）
prototype/      单文件可点原型（7 视图，HTML，无依赖）
docs/           00 前版本盘点 · 01 待确认输入 · 02 计划与依赖 · 03 进度台账
                04 概念合并矩阵 · 05 产品概念 v2.0 · 06 技术方案 · 07 信息架构 · 08 测试清单
docs/legacy/    落笔 v1.3 的 README（历史）
tools/          环境自检 / 参考库脚本
```

## 许可

MIT（见 `LICENSE`）。作品集：https://big-niuNiu929.github.io/ · GitHub：[@big-niuNiu929](https://github.com/big-niuNiu929)
