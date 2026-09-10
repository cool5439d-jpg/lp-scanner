# 资金流强度：钱还在不在往这个池子流
#
# 来源是一份实盘 LP 分享，核心那句是"我不看 APR 还有多高，我看钱还在不在往这个方向流"。
# APR 是历史快照，5 分钟成交量才是当下的真实状态。一个池子的 5 分钟成交开始萎缩，
# 手续费就在快速消失，而仪表板上的 APR 还会挂着漂亮数字骗你留在里面。
#
# 做法：把 5 分钟成交换算成小时速率，和过去一小时的实际速率比。
#   比值 > 1 说明当下比过去一小时更热，资金在涌入
#   比值 < 1 说明在退潮，手续费收入正在往下走

import fomo_flow

# 5 分钟折算成小时要乘的倍数
M5_TO_HOUR = 12

# 资金流分档
SURGE = 2.0      # 当下速率是过去一小时的两倍以上，正在爆发
STEADY = 0.6     # 0.6 到 2 之间算正常
FADING = 0.3     # 低于 0.6 是退潮，低于 0.3 是快没了


def strength(r):
    m5 = r.get("vol_5m", 0.0)
    h1 = r.get("vol_1h", 0.0)
    if h1 <= 0:
        return None
    now_rate = m5 * M5_TO_HOUR
    return now_rate / h1


# 判级阶梯，从差到好；聪明钱修正在这条阶梯上挪一格
LADDER = [("枯竭", 0), ("退潮", 1), ("正常", 2), ("涌入", 2)]


def _base(r):
    s = strength(r)
    if s is None:
        return None, "拿不到 5 分钟数据，无法判断当下资金流"
    if s >= SURGE:
        return 3, f"当下成交速率是过去一小时的 {s:.1f} 倍，资金正在涌入"
    if s >= STEADY:
        return 2, f"当下速率与过去一小时相当（{s:.1f} 倍），资金流稳定"
    if s >= FADING:
        return 1, f"当下速率只有过去一小时的 {s:.1f} 倍，成交在萎缩，手续费会跟着掉"
    return 0, f"当下速率仅为过去一小时的 {s:.1f} 倍，成交几乎停了，留在池子里只剩风险"


# 成交量定基准档，FOMO 榜单交易员的买卖再修正一档：他们同时进场往往先于成交量起来，
# 是更早的证据；他们在卖则是实打实的离场。r 里没有 fomo 字段时修正为 0，行为不变
def grade(r):
    idx, note = _base(r)
    if idx is None:
        return "未知", note, 1
    adj = fomo_flow.adjust(r)
    if adj:
        f = r["fomo"]
        idx = max(0, min(len(LADDER) - 1, idx + adj))
        note += (f"；15 分钟内 {f['smart_in_15']} 位榜单交易员买入，上调一档" if adj > 0
                 else f"；15 分钟内 {f['smart_out_15']} 位榜单交易员卖出，下调一档")
    lvl, pts = LADDER[idx]
    return lvl, note, pts


# 资金枯竭时仓位大幅压缩：钱不流了，待在里面只有风险没有收益
SIZE_FACTOR = {"涌入": 1.0, "正常": 1.0, "退潮": 0.5, "枯竭": 0.2, "未知": 0.8}
