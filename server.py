# 扫描器网页界面。标准库实现，不引入任何依赖
# 端口 3200，避开 rh-uni 的 3000 和 lp-terminal 的 4173
#
# 扫描和终审都很慢（终审每个池约 15 秒），所以放后台线程跑，
# 前端轮询进度，不阻塞页面

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import advice
import config
import decide
import fetch
import history
import judge
import lookup
import monitor
import notify
import plans
import siblings
import verify

PORT = int(os.environ.get("SCANNER_PORT", 3200))
HERE = os.path.dirname(os.path.abspath(__file__))

# 后台任务状态。同一时刻只允许一个扫描在跑
state = {
    "running": False,
    "phase": "空闲",
    "log": [],
    "result": None,
    "started": 0,
    "finished": 0,
}
lock = threading.Lock()

# 定时监控。做成服务里的后台线程而不是独立进程，这样网页上一个开关就能控制，
# 不用记命令行，也不会因为关掉终端窗口而悄悄停掉。
WATCH_STATE = os.path.join(HERE, "watch_state.json")

watch = {
    "on": False,
    "interval": 15 * 60,
    "budget": config.DEFAULT_BUDGET,
    "available": config.DEFAULT_BUDGET,
    "verify_top": 3,
    "last_run": 0,
    "next_run": 0,
    "rounds": 0,
    "alerts": 0,
    "last_note": "未启动",
}
# 每次启动都换一个新的停止事件，线程只认自己拿到的那个。
# 如果停止和启动共用一个事件，用户快速点"停止→开启"时，老线程还没从 wait() 醒来
# 事件就已被 clear()，它永远收不到停止信号，结果是两个循环同时跑、轮数翻倍、提示重复。
_watch_stop = threading.Event()


# 开关状态落盘。服务重启后自动恢复上次的开/关，免得再出现"以为在跑其实没开"
def _save_watch_state():
    try:
        with open(WATCH_STATE, "w", encoding="utf-8") as f:
            json.dump({k: watch[k] for k in ("on", "interval", "budget", "available", "verify_top")}, f)
    except OSError:
        pass


def _load_watch_state():
    try:
        with open(WATCH_STATE, encoding="utf-8") as f:
            d = json.load(f)
        for k in ("on", "interval", "budget", "available", "verify_top"):
            if k in d:
                watch[k] = d[k]
    except (OSError, json.JSONDecodeError):
        pass


def _start_watch():
    global _watch_stop
    _watch_stop = threading.Event()
    watch["on"] = True
    watch["last_note"] = "已启动，正在跑第一轮"
    # 线程按代数命名，状态接口据此数出活着的循环数。OS 线程数分不清监控循环和
    # 子进程管道读取线程，只有这个计数能直接回答"有没有漏掉没停的循环"
    watch["gen"] = watch.get("gen", 0) + 1
    threading.Thread(target=_watch_loop, args=(_watch_stop,), daemon=True,
                     name=f"watch-{watch['gen']}").start()
    _save_watch_state()


def _stop_watch():
    watch["on"] = False
    _watch_stop.set()
    watch["last_note"] = "已停止"
    _save_watch_state()


def _watch_loop(stop):
    while not stop.is_set():
        try:
            watch["last_run"] = int(time.time())
            watch["rounds"] += 1
            raw = fetch.fetch_all_pools()
            # 被替换的循环在各阶段之间就退出，不把整轮跑完。否则快速"停→开"会让
            # 新旧两轮并发：接口请求翻倍触发 429，演练子进程也成倍堆积
            if stop.is_set():
                return
            rows = fetch.extract(raw, watch["budget"])
            passed, rejected = judge.rank(rows)
            history.append_snapshot(passed, watch["budget"])
            passed = history.merge(passed, history.stability(history.load_history()))

            n_alert = 0
            # 持仓预警优先：钱已经在里面了，比找新机会重要
            # 按池子匹配而不是按代币：你持有的是 AI 的 0.23% 池，
            # 不该因为同代币的 1% 池出了撤退信号就被吵醒
            held = notify.held_pools()
            if held:
                mine = [r for r in passed + rejected if r["pool_addr"].lower() in held]
                n_alert += len(notify.warnings(mine))

            verified = []
            for r in passed[:watch["verify_top"]]:
                if stop.is_set():
                    return
                ok, _, _ = verify.dry_run(r["token"], int(watch["budget"]))
                if not ok:
                    continue
                pl = plans.three(r, watch["available"], None)
                sibs = siblings.siblings_of(r, rows, watch["budget"], fetch)
                verified.append({
                    "pool": r, "plans": pl, "siblings": sibs,
                    "advice": advice.build(r, pl, sibs, pl[1]["budget"]),
                    "decision": decide.build(r, pl[1]["budget"], pl),
                })
            hits = notify.opportunities(verified)
            n_alert += len(hits)
            watch["alerts"] += n_alert

            best = max((v["decision"]["score"] for v in verified), default=0)
            watch["last_note"] = (f"发现 {len(hits)} 个机会" if hits
                                  else f"无达标机会（最高 {best}/14，需 12）")
            if n_alert and not hits:
                watch["last_note"] = f"发出 {n_alert} 条持仓预警"
        except Exception as e:
            watch["last_note"] = f"本轮失败：{str(e)[:60]}"

        watch["next_run"] = int(time.time()) + watch["interval"]
        if stop.wait(watch["interval"]):
            return


def say(msg):
    with lock:
        state["log"].append(f"{time.strftime('%H:%M:%S')} {msg}")
        state["log"] = state["log"][-60:]
        state["phase"] = msg


# 单币查询的任务状态，和全量扫描互不干扰，可以同时跑
look = {"running": False, "log": [], "result": None, "query": ""}


def look_say(msg):
    with lock:
        look["log"].append(f"{time.strftime('%H:%M:%S')} {msg}")
        look["log"] = look["log"][-40:]


def do_lookup(query, budget, available, do_verify):
    try:
        look["running"] = True
        look["result"] = None
        look["log"] = []
        look["query"] = query
        look_say(f"解析 {query}…")
        token, cands = lookup.resolve(query)
        if not token:
            with lock:
                look["result"] = {"query": query, "candidates": cands, "pools": [], "blocks": [],
                                  "note": "找到多个同名代币，点一个继续" if cands else "没搜到这个符号，试试直接填合约地址"}
            return
        res = lookup.evaluate(token, budget, available, do_verify, look_say)
        res["query"] = query
        res["candidates"] = cands
        res["budget"] = budget
        with lock:
            look["result"] = res
        look_say("完成")
    except Exception as e:
        look_say(f"失败：{e}")
        with lock:
            look["result"] = {"query": query, "candidates": [], "pools": [], "blocks": [], "note": f"查询失败：{str(e)[:120]}"}
    finally:
        look["running"] = False


def do_scan(budget, available, verify_top):
    try:
        state["running"] = True
        state["started"] = int(time.time())
        state["result"] = None
        state["log"] = []

        say("抓取全链池子…")
        raw = fetch.fetch_all_pools()
        say(f"拿到 {len(raw)} 个池子")
        rows = fetch.extract(raw, budget)
        say(f"其中 Uniswap v4 + USDG 的有 {len(rows)} 个")

        passed, rejected = judge.rank(rows)
        say(f"通过硬性筛选 {len(passed)} 个，淘汰 {len(rejected)} 个")

        history.append_snapshot(passed, budget)
        snaps = history.load_history()
        stat = history.stability(snaps)
        passed = history.merge(passed, stat)

        verified = []
        if verify_top > 0:
            n = min(verify_top, len(passed))
            say(f"对前 {n} 个池跑演练终审，每个约 15 秒…")
            for i, r in enumerate(passed[:n]):
                say(f"终审 {i+1}/{n}：{r['name']}")
                ok, imp, note = verify.dry_run(r["token"], int(budget))
                r["verified"] = ok
                r["verify_note"] = note
                if not ok:
                    r["reasons"] = [note]
                    rejected.insert(0, r)
                    say(f"  淘汰：{note}")
                    continue
                say(f"  通过：{note}")
                sibs = siblings.siblings_of(r, rows, budget, fetch)
                pl = plans.three(r, available, None)
                verified.append({
                    "pool": r,
                    "plans": pl,
                    "siblings": sibs,
                    "advice": advice.build(r, pl, sibs, pl[1]["budget"]),
                    "decision": decide.build(r, pl[1]["budget"], pl),
                })
            done = {id(v["pool"]) for v in verified}
            passed = [r for r in passed[:n] if id(r) in done] + passed[n:]

        with lock:
            state["result"] = {
                "ts": int(time.time()),
                "budget": budget,
                "available": available,
                "passed": passed,
                "rejected": rejected[:20],
                "verified": verified,
                "snapshots": len(snaps),
            }
        say("完成")
    except Exception as e:
        say(f"失败：{e}")
    finally:
        state["running"] = False
        state["finished"] = int(time.time())


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # 状态接口每 20 秒轮询一次，任何一层缓存都会让页面显示陈旧的轮数和时间
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)

        if u.path == "/":
            with open(os.path.join(HERE, "index.html"), encoding="utf-8") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")

        if u.path == "/app.js":
            with open(os.path.join(HERE, "app.js"), encoding="utf-8") as f:
                return self._send(200, f.read(), "application/javascript; charset=utf-8")

        if u.path == "/api/status":
            with lock:
                return self._send(200, json.dumps({
                    "running": state["running"],
                    "phase": state["phase"],
                    "log": state["log"][-25:],
                    "hasResult": state["result"] is not None,
                }, ensure_ascii=False))

        if u.path == "/api/result":
            with lock:
                if not state["result"]:
                    return self._send(200, json.dumps({"empty": True}))
                return self._send(200, json.dumps(state["result"], ensure_ascii=False, default=str))

        if u.path == "/api/scan":
            if state["running"]:
                return self._send(200, json.dumps({"ok": False, "err": "已有扫描在跑"}, ensure_ascii=False))
            budget = float(q.get("budget", [config.DEFAULT_BUDGET])[0])
            available = float(q.get("available", [budget])[0])
            vt = int(q.get("verify", [0])[0])
            threading.Thread(target=do_scan, args=(budget, available, vt), daemon=True).start()
            return self._send(200, json.dumps({"ok": True}))

        if u.path == "/api/watch":
            act = q.get("on", ["status"])[0]
            if act == "test":
                threading.Thread(target=notify.test, daemon=True).start()
                return self._send(200, json.dumps({"ok": True}))
            if act == "1" and not watch["on"]:
                watch["budget"] = float(q.get("budget", [watch["budget"]])[0])
                watch["available"] = float(q.get("available", [watch["available"]])[0])
                watch["interval"] = max(60, int(q.get("interval", [watch["interval"]])[0]))
                _start_watch()
            elif act == "0" and watch["on"]:
                _stop_watch()
            return self._send(200, json.dumps({
                "on": watch["on"], "interval": watch["interval"],
                "budget": watch["budget"], "available": watch["available"],
                "rounds": watch["rounds"], "alerts": watch["alerts"],
                "lastRun": watch["last_run"], "nextRun": watch["next_run"],
                "note": watch["last_note"],
                # 活着的监控循环数，正常永远是 0 或 1。大于 1 就是有循环没停干净
                "loops": sum(1 for t in threading.enumerate() if t.name.startswith("watch-")),
                "threads": threading.active_count(),
            }, ensure_ascii=False))

        if u.path == "/api/lookup":
            q = q.get("q", [""])[0].strip()
            if not q:
                return self._send(200, json.dumps({"ok": False, "err": "请填代币地址或符号"}, ensure_ascii=False))
            if look["running"]:
                return self._send(200, json.dumps({"ok": False, "err": "上一个查询还没完"}, ensure_ascii=False))
            qs = parse_qs(u.query)
            budget = float(qs.get("budget", [config.DEFAULT_BUDGET])[0])
            available = float(qs.get("available", [budget])[0])
            do_verify = qs.get("verify", ["0"])[0] == "1"
            threading.Thread(target=do_lookup, args=(q, budget, available, do_verify), daemon=True).start()
            return self._send(200, json.dumps({"ok": True}))

        if u.path == "/api/lookup_status":
            with lock:
                return self._send(200, json.dumps({
                    "running": look["running"], "log": look["log"][-20:], "query": look["query"],
                    "result": look["result"],
                }, ensure_ascii=False, default=str))

        if u.path == "/api/monitor":
            act = q.get("act", ["status"])[0]
            if act == "add":
                token = q.get("token", [""])[0].strip().lower()
                if not lookup.ADDR_RE.match(token):
                    return self._send(200, json.dumps({"ok": False, "err": "代币地址不合法"}, ensure_ascii=False))
                monitor.add(token, q.get("symbol", [""])[0], q.get("pool", [""])[0])
                if not monitor.state["on"]:
                    monitor.start()
            elif act == "remove":
                monitor.remove(q.get("token", [""])[0])
            elif act == "import":
                added = monitor.import_positions()
                if added and not monitor.state["on"]:
                    monitor.start()
                monitor.state["last_note"] = f"导入了 {len(added)} 个持仓代币" if added else "rh-uni 没有仓位，或它没在跑"
            elif act == "on" and not monitor.state["on"]:
                monitor.state["interval"] = max(30, int(q.get("interval", [monitor.state["interval"]])[0]))
                monitor.start()
            elif act == "off" and monitor.state["on"]:
                monitor.stop()
            elif act == "interval":
                monitor.state["interval"] = max(30, int(q.get("interval", [monitor.state["interval"]])[0]))
                monitor._save()
            return self._send(200, json.dumps(monitor.snapshot(), ensure_ascii=False, default=str))

        if u.path == "/api/alerts":
            try:
                with open(notify.ALERT_LOG, encoding="utf-8") as f:
                    lines = f.read().splitlines()
            except OSError:
                lines = []
            return self._send(200, json.dumps({"lines": [l for l in lines if l][-40:][::-1]}, ensure_ascii=False))

        if u.path == "/api/history":
            snaps = history.load_history()
            stat = history.stability(snaps)
            out = sorted(
                [{"name": v["name"], "hits": v["hits"], "rate": v["rate"],
                  "avg": v["avg_income"], "spread": v["spread"], "scans": v["scans"]}
                 for v in stat.values()],
                key=lambda x: -x["rate"])
            return self._send(200, json.dumps({"scans": len(snaps), "pools": out}, ensure_ascii=False))

        return self._send(404, json.dumps({"err": "not found"}))


def main():
    _load_watch_state()
    resumed = watch["on"]
    if resumed:
        # 上次关服务前监控是开着的，自动续上
        _start_watch()
    monitor.load()
    if monitor.state["on"]:
        monitor.start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"扫描器界面: http://127.0.0.1:{PORT}" + ("  （监控已自动恢复）" if resumed else "")
          + (f"  （监测器 {len(monitor.entries)} 个币已恢复）" if monitor.state["on"] else ""))
    srv.serve_forever()


if __name__ == "__main__":
    main()
