# 代币监测器：盯住几个你已经开了仓、或准备开仓的币，每分钟读一次它的池子，
# 把价格、资金流、阶段、撤退信号连成时间线
#
# 和"定时监控"的分工：定时监控每 15 分钟扫全链找新机会，这里每 1 分钟只看名单上的几个币。
# 全链扫描要抓七页、页间强制等 5 秒，做不到分钟级；单币接口一次一个请求，几个币一轮几秒就完。
#
# 每一轮：按名单逐个抓该代币的池 -> 挑出你持仓的那个池（没指定就取流动性最大的）
# -> 算资金流 / 阶段 / 撤退信号 -> 存一个样本 -> 撤退信号达到两个且数量变化就弹窗

import json
import os
import threading
import time
import urllib.request

import config
import fetch
import flow
import fomo_flow
import notify
import phase
import timing

HERE = os.path.dirname(os.path.abspath(__file__))
LIST_FILE = os.path.join(HERE, "monitor.json")
# 保留多少个样本：1 分钟一个，1440 个正好一天
KEEP = 1440
# 两次抓取之间停一下，Gecko 免费接口每分钟 30 次上限
GAP_SEC = 2.0
RH_UNI_POSITIONS = "http://127.0.0.1:3000/api/positions"

state = {
    "on": False,
    "interval": 60,
    "auto_sync": True,
    "rounds": 0,
    "last_run": 0,
    "next_run": 0,
    "last_note": "未启动",
    "gen": 0,
}
# token -> {"token", "symbol", "pool": 指定池地址或"", "added": ts, "latest": 最新样本, "series": [...], "error": ""}
entries = {}
# 用户从名单里手动移出过的持仓代币。自动同步不再把它们加回来，否则"移出"这个按钮就形同虚设；
# 该币在 rh-uni 里的仓位全部关掉后清除记录，下次再开仓又会自动进来
muted = set()
_lock = threading.Lock()
_stop = threading.Event()


def _save():
    try:
        with _lock:
            data = {
                "on": state["on"], "interval": state["interval"], "auto_sync": state["auto_sync"], "muted": sorted(muted),
                "entries": [{k: e[k] for k in ("token", "symbol", "pool", "added")} | {"series": e["series"][-KEEP:], "latest": e["latest"]}
                            for e in entries.values()],
            }
        with open(LIST_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except OSError:
        pass


def load():
    try:
        with open(LIST_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    state["on"] = bool(d.get("on"))
    state["interval"] = int(d.get("interval", 60))
    state["auto_sync"] = bool(d.get("auto_sync", True))
    muted.update(d.get("muted", []))
    for e in d.get("entries", []):
        entries[e["token"]] = {"token": e["token"], "symbol": e.get("symbol", ""), "pool": e.get("pool", ""),
                               "added": e.get("added", 0), "latest": e.get("latest"), "series": e.get("series", []), "error": ""}


def add(token, symbol="", pool=""):
    token = token.lower()
    with _lock:
        if token in entries:
            if pool:
                entries[token]["pool"] = pool.lower()
            return False
        entries[token] = {"token": token, "symbol": symbol, "pool": pool.lower(), "added": int(time.time()),
                          "latest": None, "series": [], "error": ""}
    _save()
    return True


def remove(token):
    token = token.lower()
    with _lock:
        gone = entries.pop(token, None) is not None
        # 手动移出的记下来，自动同步不再加回；只有它当前确实是持仓币才需要记
        if gone and token in positions():
            muted.add(token)
    _save()
    return gone


# 从 rh-uni 读当前仓位。返回 token -> [仓位...]，读不到就空字典
def positions():
    try:
        req = urllib.request.Request(RH_UNI_POSITIONS, headers={"User-Agent": "lp-scanner/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception:
        return {}
    out = {}
    for p in d.get("positions") or []:
        t = str(p.get("token", "")).lower()
        if t:
            out.setdefault(t, []).append(p)
    return out


# 把钱包里已开仓的币加进名单，池子用仓位所在的那个。pos 不传就现读 rh-uni。
# 手动点按钮时 force=True 连被移出过的也加回来（用户明确要了）；每轮自动同步时跳过 muted
def import_positions(pos=None, force=True):
    pos = positions() if pos is None else pos
    added = []
    # 仓位已关的币解除屏蔽。pos 为空多半是 rh-uni 没在跑，不是仓位真的全关了，这时别动
    if pos:
        with _lock:
            muted.difference_update([t for t in muted if t not in pos])
    for t, ps in pos.items():
        if not force and t in muted:
            continue
        pool = str(ps[0].get("poolId", "")).lower()
        if add(t, str(ps[0].get("symbol", "")), pool):
            added.append(ps[0].get("symbol") or t)
        if force:
            muted.discard(t)
    return added


# 抓一个币的池并算出这一轮的样本
def sample(e, pos_list):
    rows = fetch.fetch_token_pools_strict(e["token"], config.DEFAULT_BUDGET)
    if not rows:
        raise RuntimeError("没有 Uniswap v4 USDG 池")
    r = next((x for x in rows if x["pool_addr"] == e["pool"]), None) if e["pool"] else None
    if r is None:
        r = max(rows, key=lambda x: x["tvl"])
    fomo_flow.attach([r])
    fl, fnote, _ = flow.grade(r)
    ph, pnote, play = phase.detect(r)
    r["flow_level"], r["flow_note"], r["phase"], r["phase_note"], r["phase_play"] = fl, fnote, ph, pnote, play
    sigs = phase.exit_signals(r)
    tm, tnote = timing.grade(r)
    ratio = flow.strength(r)
    if not e["symbol"]:
        e["symbol"] = _symbol(r["name"])
    return {
        "ts": int(time.time()), "price": r["price"], "tvl": r["tvl"], "vol_5m": r["vol_5m"], "vol_1h": r["vol_1h"], "vol_24h": r["vol_24h"],
        "turnover": r["turnover"], "trades_24h": r["trades_24h"], "chg_5m": r["chg_5m"], "chg_1h": r["chg_1h"], "chg_6h": r["chg_6h"], "chg_24h": r["chg_24h"],
        "flow": fl, "flow_ratio": round(ratio, 2) if ratio is not None else None, "flow_note": fnote,
        "phase": ph, "phase_note": pnote, "phase_play": play, "timing": tm, "timing_note": tnote,
        "signals": sigs, "pool_name": r["name"], "pool_addr": r["pool_addr"], "fee_pct": r["fee_pct"],
        "fomo": r["fomo"], "fomo_label": fomo_flow.label(r),
        "positions": [_pos(p) for p in pos_list],
    }, r


# 仓位里只留展示要用的字段，数字统一转 float
def _pos(p):
    f = lambda k: float(p.get(k) or 0)
    return {"id": str(p.get("id", "")), "lo": f("lo"), "hi": f("hi"), "price": f("price"), "in_range": bool(p.get("inRange")),
            "value": f("value"), "fees_usd": f("feesUsd"), "pnl_usd": f("pnlUsd"), "entry_usd": f("entryUsd"),
            "fee_text": str(p.get("feeText", "")), "pool_id": str(p.get("poolId", "")).lower(), "watch_job": p.get("watchJob")}


def _round(stop):
    pos = positions()
    # 每轮先和 rh-uni 对一次：新开的仓位自动进名单，不用再手动点导入
    synced = import_positions(pos, force=False) if state["auto_sync"] and pos else []
    with _lock:
        todo = list(entries.values())
    n_alert = 0
    for i, e in enumerate(todo):
        if stop.is_set():
            return
        try:
            s, r = sample(e, pos.get(e["token"], []))
            with _lock:
                e["latest"] = s
                e["series"].append({"ts": s["ts"], "price": s["price"], "vol_5m": s["vol_5m"], "flow_ratio": s["flow_ratio"], "n_sig": len(s["signals"])})
                e["series"] = e["series"][-KEEP:]
                e["error"] = ""
            # 撤退预警复用 notify 的去重：同一个池信号数不变就不再响
            r["exit_signals"] = s["signals"]
            n_alert += len(notify.warnings([r]))
            n_alert += _range_alert(e, s)
        except Exception as ex:
            with _lock:
                e["error"] = f"{time.strftime('%H:%M:%S')} {str(ex)[:80]}"
        if i < len(todo) - 1:
            time.sleep(GAP_SEC)
    return n_alert, synced


# 仓位跳出区间也要提醒：rh-uni 的监控会自动撤，但你得知道它撤了或者快撤了
def _range_alert(e, s):
    n = 0
    for p in s["positions"]:
        if p["in_range"] or not p["hi"]:
            continue
        key = f"range:{p['id']}"
        seen = notify._load_seen()
        if seen.get(key):
            continue
        side = "跌破下沿" if p["price"] < p["lo"] else "涨破上沿"
        notify._toast(f"仓位出区间 {e['symbol']}", f"{side}：现价 {p['price']:.6g}，区间 {p['lo']:.6g} ~ {p['hi']:.6g}")
        notify._log(f"[出区间] {e['symbol']} 仓位 {p['id']} {side} 现价 {p['price']:.6g} 区间 {p['lo']:.6g}~{p['hi']:.6g}")
        seen[key] = {"ts": time.time()}
        notify._save_seen(seen)
        n += 1
    return n


def _loop(stop):
    while not stop.is_set():
        state["last_run"] = int(time.time())
        state["rounds"] += 1
        out = _round(stop)
        if stop.is_set() or out is None:
            return
        n, synced = out
        with _lock:
            errs = sum(1 for e in entries.values() if e["error"])
            total = len(entries)
        state["last_note"] = f"{total} 个币，{errs} 个抓取失败" if errs else f"{total} 个币正常"
        if synced:
            state["last_note"] += f"，自动加入持仓 {'、'.join(synced)}"
        if n:
            state["last_note"] += f"，发出 {n} 条预警"
        _save()
        state["next_run"] = int(time.time()) + state["interval"]
        if stop.wait(state["interval"]):
            return


def start():
    global _stop
    _stop = threading.Event()
    state["on"] = True
    state["gen"] += 1
    state["last_note"] = "已启动，正在跑第一轮"
    threading.Thread(target=_loop, args=(_stop,), daemon=True, name=f"monitor-{state['gen']}").start()
    _save()


def stop():
    state["on"] = False
    _stop.set()
    state["last_note"] = "已停止"
    _save()


def snapshot():
    with _lock:
        items = [dict(e, series=e["series"][-KEEP:]) for e in entries.values()]
    return {
        "on": state["on"], "interval": state["interval"], "autoSync": state["auto_sync"], "muted": sorted(muted), "rounds": state["rounds"],
        "lastRun": state["last_run"], "nextRun": state["next_run"], "note": state["last_note"],
        "loops": sum(1 for t in threading.enumerate() if t.name.startswith("monitor-")),
        "entries": sorted(items, key=lambda e: e["added"]),
    }


def _symbol(name):
    parts = [s.strip() for s in name.rsplit(" ", 1)[0].split("/")]
    return next((p for p in parts if p and p != config.QUOTE), name)
