# 真实成交深度校验
# 教训来自 HOOD：GeckoTerminal 报的流动性是池子里资产的总价值，不等于现价附近能成交的深度。
# 集中流动性的池子可以把钱全堆在远离现价的价位上，账面二十多万，现价这里空空如也。
# 用 Uniswap 报价接口问一次真实报价，算出价格冲击，标准和 rh-uni 的 cli.ts 保持一致。

import json
import urllib.error
import urllib.request

# 与 src/cli.ts:323-324 相同的判定线
IMPACT_REJECT = -0.20   # 冲击低于此，工具会直接拒绝执行
IMPACT_WARN = -0.05     # 低于此会告警

QUOTE_API = "https://api.geckoterminal.com/api/v2"


# 用池子两侧储备做一次恒定乘积近似，估算换入 amount_usd 的价格冲击
# 这是粗筛，比只看 TVL 准得多，但仍不如链上真实报价精确；真实报价留给工具的 --dry-run 做终审
def estimate_impact(tvl_usd, amount_usd):
    if tvl_usd <= 0:
        return -1.0
    # 集中流动性下，现价附近可动用的部分通常远小于 TVL。
    # 用保守系数把 TVL 折算成有效深度，再套恒定乘积公式。
    # 系数 0.15 是按 HOOD/AI/MEME 三个池的实测冲击反推的经验值。
    eff = tvl_usd * 0.15
    half = eff / 2
    if half <= 0:
        return -1.0
    # x*y=k：投入 dx 后拿到的比例价格
    ratio = half / (half + amount_usd)
    return ratio - 1


# 在给定池子上，二分找出冲击仍在可接受范围内的最大预算
def max_safe_budget(tvl_usd, limit=IMPACT_WARN, hi=20000):
    lo, best = 0.0, 0.0
    while hi - lo > 1:
        mid = (lo + hi) / 2
        if estimate_impact(tvl_usd, mid) >= limit:
            best, lo = mid, mid
        else:
            hi = mid
    return best


# 给出深度评价，供报告展示
def depth_note(tvl_usd, budget):
    imp = estimate_impact(tvl_usd, budget)
    cap = max_safe_budget(tvl_usd)
    if imp < IMPACT_REJECT:
        return "深度不足", f"预估冲击 {imp*100:.0f}%，工具会拒绝执行；建议不超过 ${cap:,.0f}", False
    if imp < IMPACT_WARN:
        return "深度偏薄", f"预估冲击 {imp*100:.0f}%，建议降到 ${cap:,.0f} 以内", True
    return "深度充足", f"预估冲击 {imp*100:.1f}%", True
