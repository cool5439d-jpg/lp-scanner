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


def say(msg):
    with lock:
        state["log"].append(f"{time.strftime('%H:%M:%S')} {msg}")
        state["log"] = state["log"][-60:]
        state["phase"] = msg


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
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"扫描器界面: http://127.0.0.1:{PORT}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
