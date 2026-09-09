# 决策卡：把一堆并列指标压成一个明确结论
#
# 之前的输出问题是所有信息平铺，看完仍然不知道该不该投。
# 这里做三件事：
#   1. 五个维度各自打分，红灯一票否决，让"为什么"一眼可见
#   2. 给出唯一的决定性理由，而不是罗列五条
#   3. 结论只有三种，且直接带上要执行的金额和区间
#
# 另一个关键区分：通用风险 vs 差异化风险。
# "币价波动大于手续费"对几乎所有迷因币都成立，它是背景不是判据，
# 把它和"这个池特有的问题"混在一起，就是模棱两可的根源。

import fundamentals
import safety
import timing


# 每个维度返回 (分数 0/1/2, 状态, 一句话)
# 状态: 绿=没问题, 黄=需留意, 红=一票否决
def score_earning(r):
    t = r["turnover"]
    if t >= 8:
        return 2, "绿", f"日换手 {t:.1f} 倍，成交很活跃"
    if t >= 4:
        return 1, "绿", f"日换手 {t:.1f} 倍，成交够用"
    if t >= 2:
        return 1, "黄", f"日换手 {t:.1f} 倍，偏低，手续费收入有限"
    return 0, "红", f"日换手 {t:.1f} 倍，赚不到手续费"


def score_exit(r, budget):
    share = budget / r["tvl"] * 100 if r["tvl"] else 100
    if r.get("verify_failed"):
        return 0, "红", "演练不通过，换币这一步走不了"
    if share > 1.0:
        return 0, "红", f"这笔钱占整池 {share:.2f}%，撤退时自己砸盘，出不来"
    if share > 0.5:
        return 1, "黄", f"占整池 {share:.2f}%，撤退会有可感的冲击"
    return 2, "绿", f"占整池 {share:.2f}%，进出从容"


def score_contract(r):
    lvl = r.get("sec_level", "未知")
    note = r.get("sec_note", "")
    if lvl == "致命":
        return 0, "红", note
    if lvl == "高危":
        return 0, "红", note
    if lvl == "未知":
        return 1, "黄", "查不到合约数据，不能确认有没有后门"
    if lvl == "注意":
        return 1, "黄", note
    return 2, "绿", note


def score_timing(r):
    t = r.get("timing", "尚可")
    note = r.get("timing_note", "")
    if t == "差":
        return 0, "红", note
    if t == "尚可":
        return 1, "黄", note
    return 2, "绿", note


def score_stability(r):
    d = r["durability"]
    n = r["trades_24h"]
    hit = r.get("hit_rate")
    if d < 60 or n < 2000:
        return 0, "黄", f"持久度 {d:.0f}%、日笔数 {n}，收益可能不持续"
    if hit is not None and hit >= 70 and d >= 100:
        return 2, "绿", f"持久度 {d:.0f}%、上榜率 {hit:.0f}%，长期稳定"
    return 1, "绿", f"持久度 {d:.0f}%、日笔数 {n}，成交分布正常"


def score_fundamentals(r):
    lvl = r.get("fun_level", "未知")
    note = r.get("fun_note", "")
    f = r.get("fun") or {}
    age = f.get("age_hours")
    # 极新币一票否决：上线不到两天，任何数据都没有持续性保证
    if age is not None and age < fundamentals.AGE_VERY_NEW_H:
        return 0, "红", note
    if lvl == "有问题":
        return 0, "黄", note
    if lvl == "未知":
        return 1, "黄", "查不到基本面数据"
    return 2, "绿", note


DIMS = [
    ("赚钱能力", score_earning),
    ("进出安全", score_exit),
    ("合约安全", score_contract),
    ("进场时点", score_timing),
    ("收益稳定", score_stability),
    ("代币基本面", score_fundamentals),
]


def build(r, budget, plans_list):
    rows = []
    total = 0
    reds = []
    for name, fn in DIMS:
        pts, state, msg = fn(r, budget) if fn is score_exit else fn(r)
        rows.append({"name": name, "points": pts, "state": state, "msg": msg})
        total += pts
        if state == "红":
            reds.append((name, msg))

    # 红灯一票否决，无论总分多高
    if reds:
        name, msg = reds[0]
        verdict = "不投"
        reason = f"{name}不过关：{msg}"
        action = "换一个池，或等这个问题消失再看。"
    elif total >= 10:
        verdict = "投"
        best = plans_list[1]
        reason = f"六项全过，总分 {total}/12，没有拖后腿的短板。"
        action = f"按最佳档投 {best['budget']} USDG，区间 {best['range']}，勾上监控。"
    else:
        verdict = "小仓试"
        weak = min(rows, key=lambda x: x["points"])
        cons = plans_list[0]
        reason = f"总分 {total}/12，短板在{weak['name']}：{weak['msg']}"
        action = f"只按保守档投 {cons['budget']} USDG，区间 {cons['range']}，观察一天再决定加不加。"

    return {
        "verdict": verdict,
        "score": total,
        "reason": reason,
        "action": action,
        "rows": rows,
        "reds": [f"{n}：{m}" for n, m in reds],
    }


# 通用风险：几乎所有 LP 都成立，属于背景知识，不该混进判据
COMMON = [
    "做 LP 赚的是手续费，亏的可能是本金。币价单边下跌时，手续费通常补不回价差。",
    "价格跌出区间就停止收费。监控会自动撤退，但撤退本身要花 gas 且会把浮亏坐实。",
]


def render(d):
    lines = [f"  ┌─ 结论：{d['verdict']}   评分 {d['score']}/12"]
    lines.append(f"  │  {d['reason']}")
    lines.append(f"  │  怎么做：{d['action']}")
    lines.append("  ├─ 逐项体检")
    icon = {"绿": "○", "黄": "△", "红": "✕"}
    for x in d["rows"]:
        lines.append(f"  │   {icon[x['state']]} {x['name']:<6} {x['points']}/2  {x['msg']}")
    lines.append("  └─")
    return "\n".join(lines)
