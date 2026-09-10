# 定时监控：每 15 分钟扫一轮，发现优质机会或持仓预警就弹窗提示
#
# 为什么是 15 分钟而不是 5 分钟：
#   一、接口限流是硬约束。全量扫描要抓七页，页间已强制间隔 5 秒，
#       间隔太短会收到 429，导致只抓到一部分池子，数据不全反而误判。
#   二、发现机会后你要演练、核对、执行，一轮至少五到十分钟。扫太快你处理不完。
#   三、再快也没有信息增益。资金流指标本身就是 5 分钟成交对比过去一小时，
#       15 分钟一轮不会漏掉资金流的转向。
#
# 用法:
#   python watch.py                     # 15 分钟一轮，持续运行
#   python watch.py --interval 1800     # 改成 30 分钟
#   python watch.py --test-notify       # 只测通知，不扫描
#   python watch.py --once              # 只跑一轮

import argparse
import sys
import time

import config
import fetch
import history
import judge
import notify
import plans
import siblings
import verify

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

INTERVAL = 15 * 60
VERIFY_TOP = 3


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def one_round(budget, available, do_verify):
    raw = fetch.fetch_all_pools()
    rows = fetch.extract(raw, budget)
    log(f"抓到 {len(raw)} 个池，其中可做 {len(rows)} 个")

    passed, rejected = judge.rank(rows)
    history.append_snapshot(passed, budget)
    snaps = history.load_history()
    passed = history.merge(passed, history.stability(snaps))
    log(f"通过筛选 {len(passed)} 个")

    # 持仓预警优先：钱已经在里面了，比找新机会重要。按池子匹配，不按代币
    held = notify.held_pools()
    if held:
        mine = [r for r in passed + rejected if r["pool_addr"].lower() in held]
        warned = notify.warnings(mine)
        if warned:
            log(f"⚠ 持仓预警: {', '.join(warned)}")
        else:
            log(f"持仓 {len(held)} 个池，无预警")

    if not do_verify or not passed:
        return

    # 只对排名靠前的池跑终审，每个约 15 秒
    verified = []
    for r in passed[:VERIFY_TOP]:
        ok, imp, note = verify.dry_run(r["token"], int(budget))
        if not ok:
            continue
        pl = plans.three(r, available, None)
        sibs = siblings.siblings_of(r, rows, budget, fetch)
        import advice
        import decide
        verified.append({
            "pool": r, "plans": pl, "siblings": sibs,
            "advice": advice.build(r, pl, sibs, pl[1]["budget"]),
            "decision": decide.build(r, pl[1]["budget"], pl),
        })

    hits = notify.opportunities(verified)
    if hits:
        log(f"★ 发现机会: {', '.join(hits)}")
    else:
        best = max((v["decision"]["score"] for v in verified), default=0)
        log(f"无达标机会（终审 {len(verified)} 个，最高 {best}/14 分，需 12 分且无红灯）")


def main():
    ap = argparse.ArgumentParser(description="LP 池定时监控，达标就弹窗提示")
    ap.add_argument("--interval", type=int, default=INTERVAL, help="轮询间隔秒数，默认 900")
    ap.add_argument("--budget", type=float, default=config.DEFAULT_BUDGET)
    ap.add_argument("--available", type=float, default=None, help="钱包可用 USDG")
    ap.add_argument("--no-verify", action="store_true", help="跳过终审，只做排名和持仓预警")
    ap.add_argument("--once", action="store_true", help="只跑一轮")
    ap.add_argument("--test-notify", action="store_true", help="只发一条测试通知")
    args = ap.parse_args()

    if args.test_notify:
        notify.test()
        return

    available = args.available or args.budget
    log(f"监控启动：每 {args.interval // 60} 分钟一轮，预算 {args.budget:.0f}，可用 {available:.0f}")
    log("达标条件：决策卡判定为「投」，即七项体检无红灯且总分 12/14 以上")
    log("持仓预警：出现两个以上撤退信号")

    while True:
        try:
            one_round(args.budget, available, not args.no_verify)
        except KeyboardInterrupt:
            log("已停止")
            return
        except Exception as e:
            log(f"本轮失败: {e}")
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
