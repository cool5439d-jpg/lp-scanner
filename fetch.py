# 从 GeckoTerminal 抓取池子数据并算出判断用的指标

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import config

API = "https://api.geckoterminal.com/api/v2"


# 走系统代理（urllib 自动读 https_proxy 环境变量），带 UA 避免被拒
# GeckoTerminal 免费接口限流很紧，429 之后退避重试，否则会只抓到几页就断
def _get(url, tries=4):
    last = None
    for i in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": "lp-scanner/1.0", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code != 429:
                raise
            time.sleep(6 * (i + 1))
        except Exception as e:
            last = e
            time.sleep(3)
    raise last


# 全链池子，按 24 小时成交量降序翻页
def fetch_all_pools():
    out = []
    for page in range(1, config.PAGES + 1):
        url = f"{API}/networks/{config.NETWORK}/pools?page={page}&sort=h24_volume_usd_desc"
        try:
            data = _get(url).get("data", [])
        except Exception as e:
            print(f"  第 {page} 页抓取失败: {e}")
            break
        if not data:
            break
        out.extend(data)
        if page < config.PAGES:
            time.sleep(config.PAGE_SLEEP)
    return out


# 从池子名字里取费率，例如 "AI / USDG 0.23%" -> 0.0023
def _fee_from_name(name):
    m = re.search(r"([0-9.]+)%", name)
    return float(m.group(1)) / 100 if m else 0.0


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


# 把原始池子转成带指标的记录，只保留工具能做的那些
def extract(raw_pools, budget):
    rows = []
    for p in raw_pools:
        a = p.get("attributes") or {}
        rel = p.get("relationships") or {}
        dex = ((rel.get("dex") or {}).get("data") or {}).get("id", "")
        name = a.get("name") or ""
        # 只要 Uniswap v4 且 USDG 计价，这是工具唯一能建仓的组合
        if dex != config.DEX_ID or config.QUOTE not in name:
            continue

        vol = a.get("volume_usd") or {}
        chg = a.get("price_change_percentage") or {}
        tx24 = (a.get("transactions") or {}).get("h24") or {}

        # 取出非 USDG 的那一侧，那才是要填进工具表单的代币地址
        def _addr(side):
            return ((rel.get(side) or {}).get("data") or {}).get("id", "").split("_")[-1].lower()

        base, quote = _addr("base_token"), _addr("quote_token")
        usdg = config.USDG_ADDRESS
        token = quote if base == usdg else base
        # 代币美元价：Gecko 分别给 base/quote 两侧的价格，取非 USDG 那一侧
        price = _f(a.get("quote_token_price_usd") if base == usdg else a.get("base_token_price_usd"))

        tvl = _f(a.get("reserve_in_usd"))
        m5 = _f(vol.get("m5"))
        h1 = _f(vol.get("h1"))
        h6 = _f(vol.get("h6"))
        h24 = _f(vol.get("h24"))
        trades = int(_f(tx24.get("buys")) + _f(tx24.get("sells")))
        fee = _fee_from_name(name)

        # 日换手：每一块钱流动性一天做了多少成交，收入的根本来源
        turnover = h24 / tvl if tvl else 0.0
        # 持久度：拿最近一小时的速度推算全天，再看实际全天占推算的比例
        # 100% 附近 = 成交全天均匀；远低于 100% = 成交集中在最近一小时，是烟花
        durability = (h24 / (h1 * 24) * 100) if h1 > 0 else 0.0
        # 预估日收入：该池当日手续费总额 x 你的出资占比
        daily_income = h24 * fee * budget / (tvl + budget) if tvl else 0.0

        rows.append({
            "pool_id": p.get("id", ""),
            "name": name,
            "token": token,
            "pool_addr": (a.get("address") or "").lower(),
            "price": price,
            "fee_pct": fee * 100,
            "tvl": tvl,
            "vol_5m": m5,
            "vol_1h": h1,
            "vol_6h": h6,
            "vol_24h": h24,
            "trades_24h": trades,
            "turnover": turnover,
            "durability": durability,
            "chg_5m": _f(chg.get("m5")),
            "chg_1h": _f(chg.get("h1")),
            "chg_6h": _f(chg.get("h6")),
            "chg_24h": _f(chg.get("h24")),
            "daily_income": daily_income,
        })
    return rows


# 单个代币的全部池子。全链榜单按成交量排序，只会收录每个代币最大的那个池，
# 要做同代币跨池对比必须单独查这个接口。
def fetch_token_pools(token, budget):
    url = f"{API}/networks/{config.NETWORK}/tokens/{token}/pools?page=1"
    try:
        data = _get(url).get("data", [])
    except Exception:
        return []
    return extract(data, budget)


# 同上，但抓取失败直接抛错。监测器要区分"这个币没有池"和"接口挂了"，
# 后者不能把已有的读数清零
def fetch_token_pools_strict(token, budget):
    url = f"{API}/networks/{config.NETWORK}/tokens/{token}/pools?page=1"
    return extract(_get(url).get("data", []), budget)


# 按符号或名字搜代币。Gecko 的搜索接口返回的是池子，从池子两侧把非 USDG 的代币收集起来，
# 同一个代币按其最大池的流动性排序，只保留本链 Uniswap v4 的
def search_tokens(query):
    url = f"{API}/search/pools?query={urllib.parse.quote(query)}&network={config.NETWORK}&page=1"
    found = {}
    for p in _get(url).get("data", []):
        a = p.get("attributes") or {}
        rel = p.get("relationships") or {}
        if ((rel.get("dex") or {}).get("data") or {}).get("id", "") != config.DEX_ID:
            continue
        name = a.get("name") or ""
        parts = [s.strip() for s in re.sub(r"\s[0-9.]+%$", "", name).split("/")]
        for side, sym in zip(("base_token", "quote_token"), parts):
            addr = ((rel.get(side) or {}).get("data") or {}).get("id", "").split("_")[-1].lower()
            if not addr or addr == config.USDG_ADDRESS:
                continue
            tvl = _f(a.get("reserve_in_usd"))
            e = found.setdefault(addr, {"token": addr, "symbol": sym, "tvl": 0.0, "pools": 0})
            e["pools"] += 1
            e["tvl"] = max(e["tvl"], tvl)
    return sorted(found.values(), key=lambda x: -x["tvl"])
