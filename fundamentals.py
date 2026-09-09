# 代币基本面：合约安全之外，还要看这个币本身处在什么阶段、筹码分布如何
#
# 来源是用户提供的一份 CME 人工分析，里面几个维度我原来完全没有：
#   代币年龄、持有人数、Top10 集中度、创建者持仓、发射平台。
# 这些不影响"池子能不能做"，但决定"这个币值不值得把钱放进去"。
# 典型例子：CME 上线才 1-2 天，交易数据极漂亮，但新币的漂亮数据没有任何持续性保证。

import json
import time
import urllib.request

GOPLUS = "https://api.gopluslabs.io/api/v1/token_security/4663"
GECKO = "https://api.geckoterminal.com/api/v2/networks/robinhood/tokens"

# 代币太新，数据再好也没经过时间检验
AGE_VERY_NEW_H = 48      # 小于两天算极新
AGE_NEW_H = 168          # 小于一周算新

# 持有人太少说明筹码集中，容易被少数人操纵
HOLDERS_MIN = 1500

# 创建者持仓超过这个比例，他砸盘你接不住
CREATOR_MAX = 5.0

_cache = {}


def _get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "lp-scanner/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            time.sleep(2 * (i + 1))
    return None


# 代币年龄：用 GeckoTerminal 里最早那个池的创建时间近似
def _age_hours(token):
    d = _get(f"{GECKO}/{token}/pools?page=1")
    if not d:
        return None
    times = []
    for p in d.get("data", []):
        t = (p.get("attributes") or {}).get("pool_created_at")
        if t:
            try:
                times.append(time.mktime(time.strptime(t[:19], "%Y-%m-%dT%H:%M:%S")))
            except ValueError:
                pass
    if not times:
        return None
    return max(0.0, (time.time() - min(times)) / 3600)


def check(token):
    token = token.lower()
    if token in _cache:
        return _cache[token]

    d = _get(f"{GOPLUS}?contract_addresses={token}")
    result = (d or {}).get("result") or {}
    key = next(iter(result), None)
    if not key:
        return None
    t = result[key]

    def num(f, mul=1.0):
        v = t.get(f)
        try:
            return float(v) * mul if v not in (None, "") else 0.0
        except (TypeError, ValueError):
            return 0.0

    holders = int(num("holder_count"))
    creator_pct = num("creator_percent", 100)
    owner_pct = num("owner_percent", 100)
    top10 = sum(float(h.get("percent") or 0) for h in (t.get("holders") or [])[:10]) * 100
    age = _age_hours(token)

    warn = []
    if age is not None and age < AGE_VERY_NEW_H:
        warn.append(f"上线仅 {age:.0f} 小时，交易数据再漂亮也未经时间检验，随时可能崩")
    elif age is not None and age < AGE_NEW_H:
        warn.append(f"上线 {age/24:.1f} 天，仍属新币，谨慎对待")

    if holders and holders < HOLDERS_MIN:
        warn.append(f"持有人仅 {holders}，筹码集中，容易被少数人操纵")

    if creator_pct > CREATOR_MAX:
        warn.append(f"创建者持有 {creator_pct:.1f}%，他砸盘你接不住")
    if owner_pct > CREATOR_MAX:
        warn.append(f"合约所有者持有 {owner_pct:.1f}%")

    if top10 > 40:
        warn.append(f"前十大持有 {top10:.0f}%，筹码高度集中")

    # 持有人数和年龄都拿不到 = 没有这个代币的基本面数据，不能当成"正常"
    if not holders and age is None:
        return None

    out = {
        "age_hours": age, "holders": holders,
        "creator_pct": creator_pct, "owner_pct": owner_pct, "top10_pct": top10,
        "warn": warn,
        "level": "很新" if (age is not None and age < AGE_VERY_NEW_H) else ("有问题" if warn else "正常"),
    }
    _cache[token] = out
    return out


# 基本面差就压仓位
SIZE_FACTOR = {"正常": 1.0, "有问题": 0.6, "很新": 0.4, "未知": 0.7}


def summary(f):
    if f is None:
        return "未知", "查不到基本面数据"
    if f["warn"]:
        return f["level"], "；".join(f["warn"])
    age = f["age_hours"]
    a = f"上线 {age/24:.0f} 天" if age else "年龄未知"
    return "正常", f"{a}，持有人 {f['holders']}，前十大 {f['top10_pct']:.0f}%"
