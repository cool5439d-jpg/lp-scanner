# LP 选池扫描器
# 用法:
#   python scan.py                 扫一次，打印报告
#   python scan.py --budget 300    按 300 美元预算估算收益
#   python scan.py --watch         观察模式，每半小时扫一次并累积历史
#   python scan.py --show-rejected 连被淘汰的池子和原因一起打印

import argparse
import io
import sys
import time

import config
import fetch
import history
import judge
import plans
import siblings
import verify

# Windows 控制台默认 GBK，中文会乱码，强制 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def money(x):
    return f"${x:,.0f}"


def render(passed, rejected, budget, stat, show_rejected, verified=None):
    buf = io.StringIO()
    w = buf.write
    w(f"LP 选池扫描 · 预算 {money(budget)} · {time.strftime('%Y-%m-%d %H:%M')}\n")
    w(f"只看 Uniswap v4 + USDG 计价的池（工具唯一能建仓的组合）\n\n")

    if not passed:
        w("当前没有池子通过筛选。市场可能整体在暴涨或暴跌，等行情稳下来再看。\n\n")
    else:
        w(f"通过筛选 {len(passed)} 个，按预估日收入排序：\n\n")
        w(f"{'池子':<22}{'流动性':>10}{'日换手':>7}{'笔数':>7}{'24h':>8}{'持久度':>7}{'日入':>8}  {'建议区间':<12}{'上榜率':>7}\n")
        w("-" * 100 + "\n")
        for r in passed[:12]:
            hit = f"{r['hit_rate']:.0f}%" if r.get("hit_rate") is not None else "新"
            w(f"{r['name'][:22]:<22}{money(r['tvl']):>10}{r['turnover']:>7.1f}"
              f"{r['trades_24h']:>7}{r['chg_24h']:>7.1f}%{r['durability']:>6.0f}%"
              f"{r['daily_income']:>8.2f}  {r['suggest_range']:<12}{hit:>7}\n")

        w("\n各池点评：\n")
        for r in passed[:6]:
            w(f"  {r['name'][:26]:<26} {r['band']}  {judge.comment(r)}\n")
            w(f"  {'':26} 费率填 {r['fee_pct']:.4g}  地址 {r['token']}\n")

    if show_rejected and rejected:
        w(f"\n被淘汰 {len(rejected)} 个，前 10 个及原因：\n")
        for r in rejected[:10]:
            w(f"  {r['name'][:26]:<26} 日入{r['daily_income']:>7.2f}  {'；'.join(r['reasons'])}\n")

    if verified:
        w("\n" + "=" * 78 + "\n")
        w("表单填法（已通过工具演练验证，可直接照抄）\n")
        w("=" * 78 + "\n\n")
        for r, three, note, sibs in verified:
            w(plans.render(r, three, note, sibs) + "\n\n")

    w("\n提示：日换手 = 日成交/流动性，是收入的根本来源。持久度低于 100% 说明成交\n")
    w("集中在最近一小时，退潮后收益会掉。上榜率来自历史扫描，越高越稳定。\n")
    return buf.getvalue()


def run_once(budget, show_rejected, record, verify_top=0, available=None):
    available = available if available else budget
    print("抓取全链池子…")
    raw = fetch.fetch_all_pools()
    print(f"  拿到 {len(raw)} 个池子")
    rows = fetch.extract(raw, budget)
    print(f"  其中 Uniswap v4 + USDG 的有 {len(rows)} 个")

    passed, rejected = judge.rank(rows)
    if record:
        history.append_snapshot(passed, budget)
    snaps = history.load_history()
    stat = history.stability(snaps)
    passed = history.merge(passed, stat)

    # 终审：对排名靠前的池跑工具自己的演练，滤掉换币路径走不通的
    # HOOD 的教训：GeckoTerminal 数据再好看，换币那一步冲击 -78% 就是不能做
    verified = []
    if verify_top > 0 and passed:
        n = min(verify_top, len(passed))
        print("")
        print(f"对前 {n} 个池跑演练终审（每个约 15 秒）…")
        for r in passed[:verify_top]:
            ok, imp, note = verify.dry_run(r["token"], int(budget))
            if not ok:
                print(f"  {r['name'][:24]:<24} 淘汰：{note}")
                r["reasons"] = [note]
                rejected.insert(0, r)
                continue
            cap_note = "" if imp == 0 else f"注意：{note}"
            print(f"  {r['name'][:24]:<24} 通过 {note}")
            verified.append((r, plans.three(r, available, None), cap_note, siblings.siblings_of(r, rows, budget, fetch)))
        passed = [r for r in passed if any(r is v[0] for v in verified)] + [
            r for r in passed[verify_top:]]

    text = render(passed, rejected, budget, stat, show_rejected, verified)
    print("\n" + text)
    with open(config.REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"报告已写入 {config.REPORT_FILE}（共 {len(snaps)} 次历史扫描）")
    return passed


def main():
    ap = argparse.ArgumentParser(description="扫描 Robinhood 链上适合做 LP 的池子")
    ap.add_argument("--budget", type=float, default=config.DEFAULT_BUDGET, help="预算，用于估算日收入")
    ap.add_argument("--watch", action="store_true", help="观察模式，按间隔反复扫描并累积历史")
    ap.add_argument("--interval", type=int, default=config.WATCH_INTERVAL_SEC, help="观察模式间隔秒数")
    ap.add_argument("--show-rejected", action="store_true", help="同时打印被淘汰的池子和原因")
    ap.add_argument("--no-record", action="store_true", help="不写入历史")
    ap.add_argument("--verify", type=int, default=0, metavar="N", help="对前 N 个池跑工具演练终审并生成三档填法")
    ap.add_argument("--available", type=float, default=None, help="钱包可用 USDG，用来算三档仓位；默认等于预算")
    args = ap.parse_args()

    if not args.watch:
        run_once(args.budget, args.show_rejected, not args.no_record, args.verify, args.available)
        return

    print(f"观察模式：每 {args.interval} 秒扫一次，Ctrl+C 停止")
    while True:
        try:
            run_once(args.budget, args.show_rejected, not args.no_record, args.verify, args.available)
        except KeyboardInterrupt:
            print("已停止")
            return
        except Exception as e:
            print(f"本轮失败: {e}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
