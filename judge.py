# 判定一个池子当下适不适合做 LP。先硬性淘汰，再对幸存者排名
# 设计原则：不做黑箱打分，每个池子都给出能看懂的淘汰理由或推荐理由

import config
import flow
import fomo_flow
import fundamentals
import phase
import safety
import timing


# 硬性淘汰。返回理由列表，空列表表示通过
def disqualify(r):
    bad = []

    # 流动性太薄：你的钱占比过高，撤退时会把价格砸穿，滑点吃掉的比手续费赚的多
    if r["tvl"] < config.MIN_TVL:
        bad.append(f"流动性仅 ${r['tvl']:,.0f}，撤退时卖不掉")
    # 流动性太厚：分钱的人太多，小资金收益被稀释到没有意义
    elif r["tvl"] > config.MAX_TVL:
        bad.append(f"流动性 ${r['tvl']:,.0f} 过厚，小资金占比太低")

    if r["vol_24h"] < config.MIN_VOL_24H:
        bad.append(f"日成交仅 ${r['vol_24h']:,.0f}，池子没人用")

    # 笔数少而成交大，通常是刷量或者几笔大单，不是真实的散户交易流
    if r["trades_24h"] < config.MIN_TRADES_24H:
        bad.append(f"日笔数仅 {r['trades_24h']}，成交可能不真实")

    if r["turnover"] < config.MIN_TURNOVER:
        bad.append(f"日换手仅 {r['turnover']:.1f} 倍，赚不到手续费")

    # 暴涨途中进场，仓位会在上涨中把币不断卖出，涨完你只拿到区间上沿那点钱
    if r["chg_24h"] > config.MAX_PUMP_24H:
        bad.append(f"24h 暴涨 {r['chg_24h']:+.0f}%，进场会在半山腰卖光")

    # 跌势未止时进场，等于用 USDG 一路接越来越便宜的币
    if r["chg_24h"] < config.DUMP_24H and r["chg_6h"] < config.DUMP_6H:
        bad.append(f"24h {r['chg_24h']:+.0f}% 且 6h {r['chg_6h']:+.0f}%，跌势未止")

    # 成交全挤在最近一小时，说明是突发行情，退潮后你的钱会干躺着
    if 0 < r["durability"] < config.MIN_DURABILITY:
        bad.append(f"持久度 {r['durability']:.0f}%，成交集中在最近一小时")

    return bad


# 波动分档，决定区间建议
def volatility_band(r):
    v = abs(r["chg_24h"])
    if v <= config.CALM_24H:
        return "低波动"
    if v >= config.WILD_24H:
        return "高波动"
    return "中波动"


# 根据波动给出区间建议：波动越大区间越宽，避免频繁跌出去
def suggest_range(r):
    band = volatility_band(r)
    if band == "低波动":
        return "-15%,15%"
    if band == "高波动":
        return "-35%,35%"
    return "-25%,25%"


# 对通过淘汰的池子排名。收益是主轴，但用稳定性做修正
# 不把两者揉成一个不可解释的分数，而是先按收益排，再标注稳定性供人工判断
def rank(rows):
    # 先把聪明钱读数挂上，后面 flow / phase 的判级都会用到；8090 没开就是零读数
    fomo_flow.attach(rows)
    passed, rejected = [], []
    for r in rows:
        bad = disqualify(r)
        if bad:
            r["reasons"] = bad
            rejected.append(r)
        else:
            # 合约安全：致命项直接淘汰，交易数据再好也不做
            sec = safety.check(r["token"])
            lvl, note = safety.summary(sec)
            r["sec"] = sec
            r["sec_level"] = lvl
            r["sec_note"] = note
            if lvl == "致命":
                r["reasons"] = [f"合约致命风险：{note}"]
                rejected.append(r)
                continue
            fun = fundamentals.check(r["token"])
            flvl, fnote = fundamentals.summary(fun)
            r["fun"] = fun
            r["fun_level"] = flvl
            r["fun_note"] = fnote
            fl, fnote, _ = flow.grade(r)
            r["flow_level"] = fl
            r["flow_note"] = fnote
            ph, pnote, play = phase.detect(r)
            r["phase"] = ph
            r["phase_note"] = pnote
            r["phase_play"] = play
            r["layout"] = phase.LAYOUT[ph]
            r["exit_signals"] = phase.exit_signals(r)
            r["fomo_label"] = fomo_flow.label(r)
            r["band"] = volatility_band(r)
            r["suggest_range"] = suggest_range(r)
            r["timing"], r["timing_note"] = timing.grade(r)
            passed.append(r)
    passed.sort(key=lambda x: -x["daily_income"])
    rejected.sort(key=lambda x: -x["daily_income"])
    return passed, rejected


# 给通过的池子一句话点评，说明它的性格
def comment(r):
    if r["band"] == "低波动" and r["turnover"] >= 5:
        return "本金稳、成交活跃，适合求稳"
    if r["band"] == "低波动":
        return "本金稳但成交一般，收益慢"
    if r["band"] == "高波动" and r["daily_income"] > 0:
        return "收益高但币价波动大，本金可能缩水"
    if r["durability"] >= 120:
        return "成交全天均匀，收益可预期"
    return "指标均衡"
