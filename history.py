# 观察模式：把每次扫描落盘，用历史判断一个池子是持续优质还是昙花一现
# 单次快照容易被突发行情骗到，连续多次上榜才说明它真的稳定

import json
import os
import time

import config


def append_snapshot(passed, budget):
    line = {
        "ts": int(time.time()),
        "budget": budget,
        # 只存排名和关键指标，不存全量，文件不会膨胀
        "pools": [
            {
                "name": r["name"],
                "addr": r["token"],
                "pool": r["pool_addr"],
                "tvl": round(r["tvl"]),
                "turnover": round(r["turnover"], 2),
                "income": round(r["daily_income"], 2),
                "chg24": round(r["chg_24h"], 2),
            }
            for r in passed[:15]
        ],
    }
    with open(config.HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def load_history():
    if not os.path.exists(config.HISTORY_FILE):
        return []
    out = []
    with open(config.HISTORY_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


# 统计每个池子上榜次数和收益的稳定性
# 上榜率高 = 长期优质；收益忽高忽低 = 靠突发行情，不可依赖
def stability(snapshots, recent=20):
    snaps = snapshots[-recent:]
    if not snaps:
        return {}
    stat = {}
    for s in snaps:
        for p in s["pools"]:
            k = p.get("pool") or p["addr"]
            e = stat.setdefault(k, {"name": p["name"], "hits": 0, "incomes": []})
            e["hits"] += 1
            e["incomes"].append(p["income"])
    total = len(snaps)
    for k, e in stat.items():
        inc = e["incomes"]
        avg = sum(inc) / len(inc)
        # 极差比均值，衡量收益的抖动程度
        spread = (max(inc) - min(inc)) / avg if avg else 0
        e["rate"] = e["hits"] / total * 100
        e["avg_income"] = avg
        e["spread"] = spread
        e["scans"] = total
    return stat


# 把稳定性合并进当前排名，供报告展示
def merge(passed, stat):
    for r in passed:
        e = stat.get(r["pool_addr"])
        if e and e["scans"] >= 3:
            r["hit_rate"] = e["rate"]
            r["avg_income"] = e["avg_income"]
            r["spread"] = e["spread"]
        else:
            r["hit_rate"] = None
    return passed
