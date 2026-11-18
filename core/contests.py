# -*- coding: utf-8 -*-
"""
竞赛汇总 —— 第三方整理的清单，不是官方报名入口
================================================
这个模块只做三件事：
  1. 一份人工核对过的清单（赛名、谁办的、什么时候报、适合谁、值不值得打）
  2. 按「现在能不能报名」算状态 —— 这是学生真正需要的信息
  3. 按方向筛选（算法 / 后端 / 产品 / 运营 / 设计 / 数据 / 商科 / 语言 / 全专业）

两个诚实原则：

**时间窗只写规律，不写承诺。** 每条的 months 是往年报名窗口，不是今年的确
定日期；学校教务处的通知永远可能提前截止（校内选拔通常在官方截止前 1-2 个
月）。所以 UI 上必须同时出现「以当年通知为准」。

**链接只放核对过的。** 编一个看起来像官网的域名，比不给链接危害大得多——
学生照着打不开、或者打开到山寨报名页被骗报名费，这个产品就废了。
没核过的条目 link 留空，UI 会显示「官网请在教务处通知里搜」。

cycle 字段解决"两年一届"的问题：挑战杯大挑奇数年、小挑偶数年，电子设计竞
赛奇数年。不处理这个，用户会在空年份反复来找。
"""
import time

# 方向标签：与 industry.FAMILIES 的 key 对齐，另加几个非技术方向
FIT_LABELS = {
    "algo": "算法", "backend": "后端", "frontend": "前端", "data": "数据",
    "product": "产品", "growth": "运营", "ecom": "电商", "content": "内容",
    "design": "设计", "hardware": "硬件", "biz": "商科", "lang": "语言",
    "all": "全专业",
}

CATS = ["创新创业", "数学建模", "程序设计", "设计创意", "商业案例", "语言", "电子硬件"]

# months = 往年报名/提交窗口（含端点，1-12）
# cycle = yearly / odd（奇数年）/ even（偶数年）
CONTESTS = [
    {
        "key": "innovation-cn",
        "name": "中国国际大学生创新大赛",
        "aka": "原「互联网+」大学生创新创业大赛",
        "cat": "创新创业",
        "level": "国家级",
        "org": "教育部等 12 部委",
        "months": [4, 7],
        "runAt": "校赛 5–6 月 / 省赛 7–8 月 / 总决赛 9–10 月",
        "cycle": "yearly",
        "fit": ["product", "growth", "backend", "design", "biz", "all"],
        "value": "国内覆盖面最大的双创赛事，保研加分与简历权重都硬。赛道多，做产品的也能找到位置。",
        "tip": "别报「创意组」凑数——评委看的是有没有真用户和真数据。有实习项目就把它包装成商业计划，比空想一个点子强十倍。",
        "link": "https://cy.ncss.cn",
        "checked": True,
    },
    {
        "key": "tzb-academic",
        "name": "「挑战杯」全国大学生课外学术科技作品竞赛",
        "aka": "大挑",
        "cat": "创新创业",
        "level": "国家级",
        "org": "共青团中央、中国科协、教育部、中国社科院",
        "months": [3, 5],
        "runAt": "校赛 3–5 月 / 省赛 6–7 月 / 国赛 10–11 月",
        "cycle": "odd",
        "fit": ["algo", "backend", "data", "biz", "all"],
        "value": "学术类最高规格之一，作品要能写出方法和结论，不是 PPT。",
        "tip": "需要指导老师挂名和学院推荐，元旦后就该去找老师，别等通知下发。",
        "link": "https://www.tiaozhanbei.net",
        "checked": False,
    },
    {
        "key": "tzb-venture",
        "name": "「挑战杯」中国大学生创业计划竞赛",
        "aka": "小挑",
        "cat": "创新创业",
        "level": "国家级",
        "org": "共青团中央、教育部等",
        "months": [3, 6],
        "runAt": "校赛 3–5 月 / 省赛 6–8 月 / 国赛 10–11 月",
        "cycle": "even",
        "fit": ["product", "growth", "biz", "design"],
        "value": "偏商业计划书与落地进展，比大挑更看「有没有做成」。",
        "tip": "财务报表和用户增长数据是分水岭。没有真实运营数据，就写清楚验证路径与下一步实验。",
        "link": "",
        "checked": False,
    },
    {
        "key": "cumcm",
        "name": "全国大学生数学建模竞赛",
        "aka": "国赛 / CUMCM",
        "cat": "数学建模",
        "level": "国家级",
        "org": "中国工业与应用数学学会",
        "months": [5, 9],
        "runAt": "9 月中旬连续 3 天（2026 年为 9/10 18:00 – 9/13 20:00）",
        "cycle": "yearly",
        "fit": ["algo", "data", "backend", "all"],
        "value": "世界上规模最大的数学建模竞赛，2025 年 20 多万人参赛。三天三夜的完整交付能力，面试时很好讲。",
        "tip": "校内报名通常在 6–8 月就截止（比官方报名早），队伍 3 人要有明确分工：建模 / 编程 / 写作，缺写作必定崩。",
        "link": "https://www.mcm.edu.cn",
        "checked": True,
    },
    {
        "key": "mcm-icm",
        "name": "美国大学生数学建模竞赛",
        "aka": "美赛 / MCM/ICM",
        "cat": "数学建模",
        "level": "国际级",
        "org": "COMAP（美国数学及其应用联合会）",
        "months": [12, 2],
        "runAt": "2 月初连续 4 天",
        "cycle": "yearly",
        "fit": ["algo", "data", "all"],
        "value": "英文写作 + 建模，出国与保研两头都用得上。获奖率比国赛高，但 H 奖以上才有分量。",
        "tip": "报名在 12 月到次年 1 月，正好在寒假前。三个人里必须有一个英文能写通顺的，否则摘要就丢分。",
        "link": "https://www.comap.com",
        "checked": False,
    },
    {
        "key": "lanqiao",
        "name": "蓝桥杯全国软件和信息技术专业人才大赛",
        "aka": "蓝桥杯",
        "cat": "程序设计",
        "level": "国家级",
        "org": "工业和信息化部人才交流中心",
        "months": [10, 3],
        "runAt": "省赛 4 月 / 国赛 6 月",
        "cycle": "yearly",
        "fit": ["algo", "backend", "frontend"],
        "value": "题目偏工程与算法基础，比 ACM 好上手，是「简历上没有竞赛」时最快能补上的一个。",
        "tip": "10 月到次年 3 月都能报，越早报名费越低。刷历年真题比刷 LeetCode 更对症，题型重复率高。",
        "link": "https://dasai.lanqiao.cn",
        "checked": False,
    },
    {
        "key": "acm",
        "name": "ICPC 国际大学生程序设计竞赛 / CCPC",
        "aka": "ACM / 区域赛",
        "cat": "程序设计",
        "level": "国际级",
        "org": "ICPC Foundation / 中国大学生程序设计竞赛组委会",
        "months": [8, 11],
        "runAt": "网络赛 9–10 月 / 区域赛 10–11 月",
        "cycle": "yearly",
        "fit": ["algo", "backend"],
        "value": "算法岗简历上最硬的一块。区域赛铜以上在技术面里几乎等于免试手撕算法。",
        "tip": "需要队伍长期训练，临赛抱佛脚没用。大一就开始打网络赛攒经验，别等大三。",
        "link": "https://icpc.global",
        "checked": False,
    },
    {
        "key": "4c",
        "name": "中国大学生计算机设计大赛",
        "aka": "4C / 计设赛",
        "cat": "程序设计",
        "level": "国家级",
        "org": "教育部高校计算机类专业教学指导委员会等",
        "months": [3, 5],
        "runAt": "省赛 5 月 / 国赛 7–8 月",
        "cycle": "yearly",
        "fit": ["frontend", "backend", "design", "product"],
        "value": "作品赛道多（软件应用、数媒、AI 应用等），适合把课程项目或实习产物做成参赛作品。",
        "tip": "它评的是「作品完整度」，不是算法难度。把已有项目补上文档、演示视频和用户反馈，比从零写新项目划算。",
        "link": "",
        "checked": False,
    },
    {
        "key": "nuedc",
        "name": "全国大学生电子设计竞赛",
        "aka": "电赛 / NUEDC",
        "cat": "电子硬件",
        "level": "国家级",
        "org": "教育部、工业和信息化部",
        "months": [5, 8],
        "runAt": "8 月连续 4 天封闭赛",
        "cycle": "odd",
        "fit": ["hardware", "algo"],
        "value": "硬件方向含金量最高的国赛，四天三夜做出一台能跑的作品。",
        "tip": "两年一届，奇数年才有。需要实验室场地和器材支持，提前找指导老师占位。",
        "link": "",
        "checked": False,
    },
    {
        "key": "tianchi",
        "name": "微信小程序应用开发赛（中国高校计算机大赛）",
        "aka": "小程序赛 / 天梯赛同系列",
        "cat": "程序设计",
        "level": "国家级",
        "org": "教育部高校计算机类专业教学指导委员会、腾讯",
        "months": [3, 6],
        "runAt": "初赛 4–5 月 / 决赛 6–7 月",
        "cycle": "yearly",
        "fit": ["frontend", "product", "design"],
        "value": "产出直接是一个能扫码用的小程序，面试时能当场演示，比 PPT 有说服力。",
        "tip": "选题别做大而全的「校园服务」，做一个小到一周能做完、但真的有人用的功能。",
        "link": "",
        "checked": False,
    },
    {
        "key": "ada",
        "name": "全国大学生广告艺术大赛",
        "aka": "大广赛",
        "cat": "设计创意",
        "level": "国家级",
        "org": "全国高校广告教育研究会",
        "months": [3, 6],
        "runAt": "6 月截稿 / 7–8 月评审 / 11 月颁奖",
        "cycle": "yearly",
        "fit": ["design", "content", "growth"],
        "value": "作品集里最好用的一类奖项，命题来自真实品牌，做出来就是商业案例。",
        "tip": "命题每年 3 月公布，先挑一个有真实预算的品牌命题——评委看得出你有没有研究过它的投放。",
        "link": "",
        "checked": False,
    },
    {
        "key": "fwwb",
        "name": "中国大学生服务外包创新创业大赛",
        "aka": "服创赛",
        "cat": "创新创业",
        "level": "国家级",
        "org": "教育部、商务部",
        "months": [3, 5],
        "runAt": "初赛 5 月 / 决赛 6–8 月",
        "cycle": "yearly",
        "fit": ["backend", "product", "biz"],
        "value": "企业命题制，做的是真实业务需求，简历上可以直接写「为某企业交付了 X」。",
        "tip": "选题看命题企业的真实痛点，别选技术炫技类。决赛答辩会问商业价值，提前准备单位经济模型。",
        "link": "",
        "checked": False,
    },
    {
        "key": "market-research",
        "name": "全国大学生市场调查与分析大赛",
        "aka": "市调赛",
        "cat": "商业案例",
        "level": "国家级",
        "org": "中国商业统计学会",
        "months": [10, 3],
        "runAt": "校赛 3 月 / 省赛 4 月 / 国赛 5–6 月",
        "cycle": "yearly",
        "fit": ["data", "growth", "biz", "product"],
        "value": "唯一一个「必须真的发问卷、跑数据」的国赛，做运营/用研岗时说服力很强。",
        "tip": "评委最看重抽样方法是否站得住。样本量不够就别硬报显著性，老老实实写局限反而加分。",
        "link": "",
        "checked": False,
    },
    {
        "key": "neccs",
        "name": "全国大学生英语竞赛",
        "aka": "NECCS",
        "cat": "语言",
        "level": "国家级",
        "org": "高等学校大学外语教学研究会",
        "months": [12, 3],
        "runAt": "初赛 4 月 / 决赛 5 月",
        "cycle": "yearly",
        "fit": ["lang", "all"],
        "value": "报名门槛低、备考成本低，是「简历上竞赛那一栏空着」时的兜底选项。",
        "tip": "题型固定，刷 3 套真题就能摸到节奏。特等奖对保研有用，二三等奖基本只算参与。",
        "link": "",
        "checked": False,
    },
    {
        "key": "uchallenge",
        "name": "「外研社·国才杯」全国大学生外语能力大赛",
        "aka": "国才杯",
        "cat": "语言",
        "level": "国家级",
        "org": "外语教学与研究出版社",
        "months": [9, 12],
        "runAt": "校赛 9–10 月 / 省赛 11 月 / 国赛 12 月",
        "cycle": "yearly",
        "fit": ["lang", "all"],
        "value": "演讲/写作/阅读分赛道，演讲赛道含金量最高，外企与咨询岗认这个。",
        "tip": "校赛通常在 9 月开学两周内，暑假就要准备定题演讲，别等开学。",
        "link": "",
        "checked": False,
    },
    {
        "key": "xuechuang",
        "name": "「学创杯」全国大学生创业综合模拟大赛",
        "aka": "学创杯",
        "cat": "商业案例",
        "level": "国家级",
        "org": "高等学校国家级实验教学示范中心联席会",
        "months": [3, 6],
        "runAt": "校赛 4 月 / 省赛 5 月 / 国赛 6–10 月",
        "cycle": "yearly",
        "fit": ["biz", "growth", "product"],
        "value": "系统模拟经营，商科学生性价比最高的国赛之一，不需要真项目。",
        "tip": "规则和数值模型背熟就能打，队伍里要有一个专盯财务三张表的人。",
        "link": "",
        "checked": False,
    },
    {
        "key": "vendor-algo",
        "name": "企业算法/数据竞赛（天池、Kaggle、华为软挑等）",
        "aka": "厂商赛",
        "cat": "程序设计",
        "level": "行业级",
        "org": "阿里云、华为、Kaggle 等",
        "months": [1, 12],
        "runAt": "全年滚动，赛程 1–3 个月",
        "cycle": "yearly",
        "fit": ["algo", "data", "backend"],
        "value": "不看学校、只看排名。天池/Kaggle 拿到前 5% 可以直接写进简历，算法岗面试官认。",
        "tip": "挑一个已经结束的比赛复现 baseline 再改进，比追新赛更省时间；复现过程本身就是可讲的项目。",
        "link": "https://tianchi.aliyun.com",
        "checked": False,
    },
]


def _in_window(month, months):
    """窗口可能跨年（如 10 月到次年 3 月）。"""
    a, b = months
    if a <= b:
        return a <= month <= b
    return month >= a or month <= b


def _next_window(month, months):
    """还有几个月开始报名（0 表示本月就是窗口首月）。"""
    a = months[0]
    return (a - month) % 12


def status_of(item, now=None):
    """
    返回 open / soon / closed / offyear。
    offyear 单独一种状态很重要：两年一届的赛事在空档年被标成「已结束」，
    用户会以为错过了，其实是今年根本没有。
    """
    now = now or time.localtime()
    month, year = now.tm_mon, now.tm_year

    cyc = item.get("cycle", "yearly")
    if cyc == "odd" and year % 2 == 0:
        return {"state": "offyear", "label": "今年停办（奇数年）", "hint": "下一年再准备"}
    if cyc == "even" and year % 2 == 1:
        return {"state": "offyear", "label": "今年停办（偶数年）", "hint": "下一年再准备"}

    months = item.get("months") or [1, 12]
    if _in_window(month, months):
        # 全年开放的（厂商赛）不喊"仅剩几天"
        if months == [1, 12]:
            return {"state": "open", "label": "全年滚动", "hint": "随时可报名"}
        return {"state": "open", "label": "正在报名窗口", "hint": "校内选拔可能提前截止"}
    n = _next_window(month, months)
    if n <= 2:
        return {"state": "soon", "label": f"{n} 个月后开放", "hint": "现在开始备赛正好"}
    return {"state": "closed", "label": f"约 {n} 个月后开放", "hint": "先把作品/队伍准备好"}


def list_all(cat="", fit="", q=""):
    out = []
    for it in CONTESTS:
        if cat and it["cat"] != cat:
            continue
        if fit and fit not in it["fit"]:
            continue
        if q:
            blob = " ".join([it["name"], it.get("aka", ""), it["org"], it["cat"],
                             it["value"], it["tip"]]).lower()
            kws = [w for w in q.lower().split() if w]
            if not all(w in blob for w in kws):
                continue
        d = dict(it)
        d["status"] = status_of(it)
        d["fitLabels"] = [FIT_LABELS.get(f, f) for f in it["fit"]]
        out.append(d)

    # 能报的排最前，其次即将开放，再按名称
    order = {"open": 0, "soon": 1, "closed": 2, "offyear": 3}
    out.sort(key=lambda x: (order.get(x["status"]["state"], 9), x["name"]))
    return out


def meta():
    open_now = [x for x in CONTESTS if status_of(x)["state"] in ("open", "soon")]
    return {
        "total": len(CONTESTS),
        "cats": CATS,
        "fits": FIT_LABELS,
        "openNow": len(open_now),
        "note": "报名时间是往年规律，校内选拔通常比官方截止早 1–2 个月；最终以你学校教务处的通知为准。",
        "source": "第三方整理，非官方报名入口。链接为赛事官网，失效时请在学校通知里搜赛事名称。",
    }
