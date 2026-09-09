# 为每个池子生成三档方案，字段名和 rh-uni 网页表单一一对应，可以照抄
#
# 三档的差别只在两处：投入多少、区间多宽。其余字段三档相同，因为那些是安全设置，不该拿来赌。
# 保守：宽区间、小仓位，价格很难跑出去，收益低但省心
# 最佳：按波动率匹配区间，收益和风险的平衡点
# 激进：窄区间、大仓位，收益高但需要盯盘和频繁再平衡，gas 消耗大

import advice
import config
import decide
import fundamentals
import safety
import timing


# 区间宽度按波动率分档。窄区间收益高但跌出去的概率也高
# 倍数关系：收益大致与区间宽度成反比，区间收窄一半，收益约翻倍
RANGE_TABLE = {
    "低波动": {"保守": 25, "最佳": 15, "激进": 8},
    "中波动": {"保守": 40, "最佳": 25, "激进": 15},
    "高波动": {"保守": 60, "最佳": 35, "激进": 22},
}

# 仓位占可用资金的比例
SIZE_RATIO = {"保守": 0.25, "最佳": 0.50, "激进": 0.80}

# 单个仓位最多占整池多少。占比越高，撤退时自己砸盘越狠
SHARE_CAP = {"保守": 0.002, "最佳": 0.005, "激进": 0.010}


# 单档方案。cap 是这个池实际能吃下的上限（来自演练终审），会压住仓位
def build(r, style, available, cap):
    pct = RANGE_TABLE[r["band"]][style]
    # 三道闸取最小：按可用资金的比例、占池比例上限、演练验证过的上限
    # 占池上限随风险偏好变化，否则浅池里三档会一起顶到同一个天花板、失去区分度
    # 时点差的池子仓位再打折：数字合格不代表现在该进场
    tf = timing.SIZE_FACTOR.get(r.get("timing", "尚可"), 0.5)
    # 合约有问题也压仓位：高危只给三成半
    sf = safety.SIZE_FACTOR.get(r.get("sec_level", "未知"), 0.5)
    # 基本面差也压：新币、筹码集中都该少投
    ff = fundamentals.SIZE_FACTOR.get(r.get("fun_level", "未知"), 0.7)
    caps = [available * SIZE_RATIO[style] * tf * sf * ff, r["tvl"] * SHARE_CAP[style]]
    if cap:
        caps.append(cap)
    size = int(min(caps))

    # 收益 = 该池日手续费总额 x 你的出资占比 x 区间集中系数
    # 不能按仓位线性放大：仓位越大占比越高，边际收益递减
    pool_daily_fee = r["vol_24h"] * r["fee_pct"] / 100
    share = size / (r["tvl"] + size) if r["tvl"] else 0
    # 集中系数：用实盘反推校准。理论上区间收窄一倍收益翻倍，但实际增益小得多，
    # 因为你的钱只占池子极小一部分，池内其他 LP 的分布才是主导。开方后再用。
    base_pct = RANGE_TABLE[r["band"]]["最佳"]
    scale = (base_pct / pct) ** 0.5
    income = pool_daily_fee * share * scale

    return {
        "style": style,
        "token": r["token"],
        "budget": size,
        "pool_select": "auto",
        "fee": f"{r['fee_pct']:.4g}",
        "spacing": "",           # 留空，复用已有池时以链上为准
        "max_deviation": "10",
        "range_mode": "相对现价（百分比）",
        "range": f"-{pct}%,{pct}%",
        "shape": "spot",
        "swap_slippage": "5",
        "lp_slippage": "5",
        "watch": "勾上",
        "est_income": income,
        "range_pct": pct,
    }


def three(r, available, cap=None):
    return [build(r, s, available, cap) for s in ("保守", "最佳", "激进")]


# 渲染成可以照着填的文本
# 用最佳档仓位估算币价波动带来的本金变动，提醒用户手续费不是全部
def plans_budget_hint(plans, r):
    mid = plans[1]["budget"]
    return abs(mid * r["chg_24h"] / 100)


def render(r, plans, cap_note="", sibs=None):
    lines = []
    lines.append(f"【{r['name']}】 {r['band']}  日换手 {r['turnover']:.1f} 倍  24h {r['chg_24h']:+.1f}%")
    lines.append(f"  时点 {r.get('timing','?')}：{r.get('timing_note','')}")
    lines.append(f"  合约 {r.get('sec_level','?')}：{r.get('sec_note','')}")
    lines.append(f"  基本面 {r.get('fun_level','?')}：{r.get('fun_note','')}")
    lines.append(f"  代币地址 {r['token']}")
    if cap_note:
        lines.append(f"  {cap_note}")
    lines.append("")
    lines.append(f"  {'字段':<14}{'保守':<20}{'最佳':<20}{'激进':<20}")
    lines.append("  " + "-" * 74)
    rows = [
        ("预算(USDG)", [f"{p['budget']}" for p in plans]),
        ("区间(%)", [p["range"] for p in plans]),
        ("预估日入", [f"${p['est_income']:.2f}" for p in plans]),
        ("占池比例", [f"{p['budget']/r['tvl']*100:.2f}%" for p in plans]),
    ]
    for label, vals in rows:
        lines.append(f"  {label:<14}{vals[0]:<20}{vals[1]:<20}{vals[2]:<20}")
    lines.append("")
    p = plans[1]
    lines.append(f"  ⚠ 预估日入只算手续费。该币 24h 波动 {r['chg_24h']:+.1f}%，"
                 f"本金按此波动一天可能变动 ${plans_budget_hint(plans, r):.0f}，")
    lines.append("    币价波动通常远大于手续费收入，别只看日入。")
    lines.append("")
    if sibs:
        lines.append("  同代币其它可用池：")
        for x in sibs:
            lines.append(f"    {x['name'][:24]:<24} 换手{x['turnover']:>5.1f}  日入${x['income']:>7.2f}  占池{x['share_pct']:>5.2f}%  {x['note']}")
        lines.append("")
    lines.append("")
    lines.append(decide.render(decide.build(r, plans[1]["budget"], plans)))
    lines.append("")
    lines.append(advice.render(advice.build(r, plans, sibs or [], plans[1]["budget"])))
    lines.append("")
    lines.append("  三档共用的字段（照填即可）：")
    lines.append(f"    池选择 auto   费率 {p['fee']}   tick间距 留空   最大偏离 10")
    lines.append(f"    区间写法 相对现价（百分比）   形状 spot")
    lines.append(f"    换币滑点 5   LP余量 5   继续监控 勾上")
    return "\n".join(lines)
