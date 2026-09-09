# 给每个池生成人话建议：该不该做、为什么、注意什么、怎么办
#
# 数字表格回答"是多少"，这里回答"所以呢"。
# 目标是复现人工分析的判断密度：先给结论，再给理由，最后给可执行的下一步。

import config
import timing


# 结论分三种：推荐、可做但有条件、建议观望
def verdict(r, sibs):
    # 合约风险优先于一切交易指标：后门比行情更致命
    sl = r.get("sec_level")
    if sl == "高危":
        return "谨慎", f"合约层有高风险：{r.get('sec_note','')}。交易数据再好也要小仓，随时可能被增发或抽走流动性。"
    if sl == "未知":
        return "谨慎", "查不到合约安全数据，无法确认有没有后门，建议只用保守档。"

    t = r.get("timing", "尚可")
    turn = r["turnover"]
    band = r["band"]

    if t == "差":
        return "观望", f"池子质量合格，但{r.get('timing_note','时点不好')}。等形态稳下来再进，那时收益依然成立，风险却小得多。"

    if t == "尚可" and abs(r["chg_24h"]) > 15:
        return "谨慎", f"数字合格但波动偏大（24h {r['chg_24h']:+.1f}%）。建议只用保守档试水，别一次投满。"

    if turn >= 8 and band == "低波动":
        return "推荐", "换手高且币价稳，是做市最舒服的组合：手续费吃得到，本金不怎么波动。"

    if turn >= 5:
        return "推荐", f"日换手 {turn:.1f} 倍，成交活跃，手续费收入有支撑。"

    return "可做", f"日换手 {turn:.1f} 倍，收益一般但没有明显问题。"


# 风险提示：把这个池最该当心的一两件事说清楚
def risks(r, budget):
    out = []

    sec = r.get("sec") or {}
    for x in sec.get("high", []):
        out.append(f"合约风险：{x}")
    for x in sec.get("medium", []):
        out.append(f"合约提示：{x}")

    swing = abs(budget * r["chg_24h"] / 100)
    fee_day = r["daily_income"] * budget / config.DEFAULT_BUDGET
    if fee_day > 0 and swing > fee_day * 2:
        out.append(f"币价波动是手续费的 {swing/fee_day:.0f} 倍：本金一天可能变动 ${swing:.0f}，"
                   f"手续费才 ${fee_day:.0f}。做 LP 赚的是手续费，亏的可能是本金。")

    share = budget / r["tvl"] * 100 if r["tvl"] else 100
    if share > 0.4:
        out.append(f"这笔钱占整池 {share:.2f}%，撤退时你自己就是把价格砸下去的那个人，"
                   f"实际拿回的会比账面少。")

    if r["durability"] < 80:
        out.append(f"持久度 {r['durability']:.0f}%，成交偏集中在最近一小时。"
                   f"如果那波行情过去，日入会明显低于预估。")

    if r["trades_24h"] < 3000:
        out.append(f"日笔数 {r['trades_24h']}，交易不算密集，收益的稳定性打折扣。")

    if r["band"] == "高波动":
        out.append("高波动币做 LP，价格穿出区间的概率大。必须勾监控，否则跌出去无人接管。")

    return out


# 下一步该干什么，给具体动作
def next_steps(r, plans_list, sibs):
    out = []
    best = plans_list[1]
    out.append(f"先点「计划」演练，确认日志里写的是「复用」不是「新建」，"
               f"再核对区间落在现价上下 {best['range_pct']}% 附近。")

    if sibs:
        better = [s for s in sibs if s["income"] > r["daily_income"] * 1.2 and s["share_pct"] <= 0.5]
        if better:
            b = better[0]
            out.append(f"同代币的 {b['name']} 日入 ${b['income']:.2f} 更高且深度够（占池 {b['share_pct']:.2f}%），"
                       f"值得优先考虑。")
        shallow = [s for s in sibs if s["income"] > r["daily_income"] and s["share_pct"] > 0.5]
        if shallow:
            s = shallow[0]
            out.append(f"别被 {s['name']} 的高日入吸引，它占池 {s['share_pct']:.2f}%，撤退时冲击大。")

    if r.get("timing") == "差":
        out.append("当前不建议执行。把这个池加进观察，等一小时读数转正或走平再动手。")
    else:
        out.append("执行后去仓位页确认监控已挂上，跌出区间才有人接管。")

    return out


def build(r, plans_list, sibs, budget):
    v, why = verdict(r, sibs)
    return {
        "verdict": v,
        "why": why,
        "risks": risks(r, budget),
        "steps": next_steps(r, plans_list, sibs),
    }


# 文本渲染，命令行用
def render(a):
    lines = [f"  结论：{a['verdict']} —— {a['why']}"]
    if a["risks"]:
        lines.append("  要当心：")
        for x in a["risks"]:
            lines.append(f"    · {x}")
    lines.append("  下一步：")
    for x in a["steps"]:
        lines.append(f"    · {x}")
    return "\n".join(lines)
