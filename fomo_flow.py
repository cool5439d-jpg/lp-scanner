# 聪明钱流向：FOMO 榜单交易员在某个币上的买卖人数，来自本机 8090 的 fomo_alpha
#
# GeckoTerminal 的 5 分钟成交量是"钱已经进来了"的事后读数；FOMO 榜单交易员同时买入同一个币，
# 往往发生在成交量起来之前，是更早一步的资金流证据。所以拿它来修正 flow 的判级，而不是替代。
#
# 两个边界：
#   一、它只看到 FOMO 上的交易员。Robinhood 链上大量成交不经过 FOMO，所以"没人进"不等于没资金，
#       修正只在有正向证据时往上提，不因为读数为零往下压。卖出是例外：榜单交易员在卖是实打实的离场证据。
#   二、8090 没开就返回空表，所有调用方拿到零读数，扫描器行为和没接之前完全一样。

import json
import time
import urllib.request

FOMO_HOT = "http://127.0.0.1:8090/api/hot"
CHAIN = "robinhood"
# 两个窗口：短窗口看当下有没有人进（修正资金流），长窗口看有没有人走（撤退信号）
SHORT_MIN = 15
LONG_MIN = 60
# 榜单交易员达到这个人数才算证据。单人可能是个人行为，两人同一窗口买同一个币才是共识
MIN_SMART = 2
# 扫描一轮几十秒，同一轮里各池共用一次抓取
_cache = {"ts": 0.0, "data": None}
CACHE_SEC = 20

EMPTY = {"smart_in_15": 0, "in_15": 0, "usd_15": 0.0, "smart_out_15": 0,
         "smart_in_60": 0, "in_60": 0, "usd_60": 0.0, "smart_out_60": 0, "out_60": 0, "thesis_60": 0, "ok": False}


def _get(minutes):
    url = f"{FOMO_HOT}?minutes={minutes}&chain={CHAIN}&limit=500"
    req = urllib.request.Request(url, headers={"User-Agent": "lp-scanner/1.0"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read().decode("utf-8"))


# token(小写地址) -> 读数。失败返回 {}，调用方按零处理
def load():
    now = time.time()
    if _cache["data"] is not None and now - _cache["ts"] < CACHE_SEC:
        return _cache["data"]
    try:
        short, long_ = _get(SHORT_MIN), _get(LONG_MIN)
    except Exception:
        _cache.update(ts=now, data={})
        return {}
    out = {}
    for h in long_:
        t = h["token_address"].lower()
        out[t] = dict(EMPTY, ok=True, smart_in_60=h["smart_buyers"], in_60=h["buyers"], usd_60=float(h["buy_usd"] or 0),
                      smart_out_60=h["smart_sellers"], out_60=h["sellers"], thesis_60=h["thesis"])
    for h in short:
        t = h["token_address"].lower()
        e = out.setdefault(t, dict(EMPTY, ok=True))
        e.update(smart_in_15=h["smart_buyers"], in_15=h["buyers"], usd_15=float(h["buy_usd"] or 0), smart_out_15=h["smart_sellers"])
    _cache.update(ts=now, data=out)
    return out


# 把读数挂到池子记录上。同一代币的几个池共享同一份读数，因为 FOMO 的买入不分池
def attach(rows):
    data = load()
    for r in rows:
        r["fomo"] = dict(data.get(r["token"].lower(), EMPTY))
    return rows


# 资金流判级修正：+1 往上提一档，-1 往下压一档，0 不动
def adjust(r):
    f = r.get("fomo") or EMPTY
    if f["smart_out_15"] >= MIN_SMART:
        return -1
    if f["smart_in_15"] >= MIN_SMART and f["smart_out_15"] == 0:
        return 1
    return 0


# 撤退信号：一小时内榜单交易员卖出达到人数线
def exit_signal(r):
    f = r.get("fomo") or EMPTY
    if f["smart_out_60"] >= MIN_SMART:
        return f"聪明钱离场：1 小时内 {f['smart_out_60']} 位榜单交易员卖出（买入 {f['smart_in_60']} 位）"
    return None


# 一句话标签，页面上显示
def label(r):
    f = r.get("fomo") or EMPTY
    if not f["ok"]:
        return ""
    return f"聪明钱 15 分 {f['smart_in_15']} 进 {f['smart_out_15']} 出 · 1 时 {f['smart_in_60']} 进 {f['smart_out_60']} 出"
