# 用 rh-uni 自己的 --dry-run 做终审
#
# 为什么不自己算：HOOD 的教训有两层。
# 第一层，GeckoTerminal 的 TVL 完全无法预测价格冲击，同量级 TVL 的两个池冲击能差五十倍，
#   因为集中流动性可以把钱堆在远离现价的地方。
# 第二层更隐蔽：直接问链上 v4 池的 Quoter，HOOD 换 250 USDG 只有 0.24% 冲击，看着很好；
#   但工具实际换币走的是 Uniswap 路由接口，那条路径给出的是 -79%。约束在换币这一步，不在池子深度。
# 所以唯一可靠的判据，就是跑一遍工具自己的演练。

import re
import subprocess
import os

TOOL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "robinhood-chain-LP")

# 与 rh-uni 的 src/cli.ts 一致：冲击低于 -20% 直接拒绝，低于 -5% 告警
RE_REJECT = re.compile(r"错误:.*价格冲击\s*(-[\d.]+)%")
RE_WARN = re.compile(r"警告:.*价格冲击\s*(-[\d.]+)%")
RE_PLAN = re.compile(r"计划:\s*换币\s*≈([\d.]+)")
RE_POOL = re.compile(r"复用\s+(.+?)（")
RE_NEW = re.compile(r"已存在|新建")


# 跑一次演练。返回 (可行, 冲击百分比或None, 说明)
def dry_run(token, budget, rng="-25%,25%", timeout=150):
    cmd = ["npm", "run", "launch", "--", f"--token={token}", f"--usdg={budget}", f"--range={rng}", "--dry-run"]
    try:
        p = subprocess.run(cmd, cwd=TOOL_DIR, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace", shell=True)
    except subprocess.TimeoutExpired:
        return False, None, "演练超时"
    out = (p.stdout or "") + (p.stderr or "")

    m = RE_REJECT.search(out)
    if m:
        return False, float(m.group(1)), f"价格冲击 {m.group(1)}%，工具拒绝执行"
    m = RE_WARN.search(out)
    if m:
        return True, float(m.group(1)), f"价格冲击 {m.group(1)}%，偏高但可执行"
    if RE_PLAN.search(out):
        return True, 0.0, "无价格冲击警告"
    return False, None, "演练未产出计划"


# 二分找出能干净执行的最大预算。step 控制精度，避免跑太多次演练
def max_budget(token, hi=2000, rng="-25%,25%", tries=5):
    ok_hi, _, _ = dry_run(token, hi, rng)
    if ok_hi:
        return hi, True
    lo, best = 0, 0
    for _ in range(tries):
        mid = int((lo + hi) / 2)
        if mid < 10:
            break
        ok, _, _ = dry_run(token, mid, rng)
        if ok:
            best, lo = mid, mid
        else:
            hi = mid
    return best, False
