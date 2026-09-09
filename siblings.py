# 同一代币的多个池对比
#
# SHROOM 的教训：扫描器按绝对日入排名，推荐了 0.9% 那个池（日入 19），
# 但同代币的 0.56% 池日入 31.87，因为它流动性只有 6.2 万、换手 11.5 倍。
# 只按日入排名会漏掉这种；只按换手排名又会推荐浅到撤不出来的池。
# 所以要把同代币的池并排列出，标明各自的取舍，让人自己选。

# 池子浅到什么程度算风险：仓位占比超过这个数，撤退时你自己就是砸盘的人
RISKY_SHARE = 0.005


def group_by_token(rows):
    g = {}
    for r in rows:
        g.setdefault(r["token"], []).append(r)
    for v in g.values():
        v.sort(key=lambda x: -x["daily_income"])
    return g


# 给某个池找出同代币的其它可用池，标注取舍
# 必须单独查该代币的池列表：全链榜单按成交量排序，每个代币通常只收录最大的一个池
def siblings_of(r, all_rows, budget, fetch_mod=None):
    same = [x for x in all_rows if x["token"] == r["token"] and x["name"] != r["name"]]
    if not same and fetch_mod:
        pools = fetch_mod.fetch_token_pools(r["token"], budget)
        same = [x for x in pools if x["name"] != r["name"]]
    if not same:
        return []
    out = []
    for s in sorted(same, key=lambda x: -x["daily_income"])[:3]:
        share = budget / s["tvl"] if s["tvl"] else 1
        if s["daily_income"] > r["daily_income"] * 1.2:
            if share > RISKY_SHARE:
                note = f"日入更高但池子浅，{budget:.0f} 美元占 {share*100:.2f}%，撤退冲击大"
            else:
                note = "日入更高且深度够，更值得选"
        elif s["tvl"] > r["tvl"] * 2:
            note = "更深更稳，但收益低"
        else:
            note = "指标接近"
        out.append({
            "name": s["name"], "fee": s["fee_pct"], "tvl": s["tvl"],
            "turnover": s["turnover"], "income": s["daily_income"],
            "share_pct": share * 100, "note": note,
        })
    return out
