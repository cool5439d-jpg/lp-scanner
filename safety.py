# 代币合约安全检查
#
# 交易数据再好看，合约本身有后门也白搭。NET 就是例子：日换手、笔数、走势都合格，
# 但 treasury 能无限增发、LP 一点没锁，随时可以砸盘跑路。
#
# 数据源选 GoPlus：实测支持 Robinhood 链（chainId 4663），且返回结果与 GMGN 页面
# 展示的风险项一一对应（NET 的 is_mintable=1 就是那条 UnlimitedMinting，
# LP 锁定 0% 就是 Burnt/Locked LP < 80%），两者很可能同源。
# 不直接抓 GMGN 是因为它全站挂 Cloudflare 人机验证，脚本一律 403。

import json
import time
import urllib.request

# 判断地址是不是合约。LP 大户是合约（锁仓器/销毁器/launchpad）和是个人钱包，
# 风险完全不同：CME 最大 LP 持有者占 74%，看着像大户掌控，实际是 22KB 的合约，
# 第二名占 24% 是 23 字节的最小代理（典型销毁/锁仓），两者合计 98% 都撤不走。
# 只看占比不看地址类型，会把"已锁仓"误报成"随时跑路"。
_RPC = "https://rpc.mainnet.chain.robinhood.com"
_code_cache = {}


def is_contract(addr):
    a = (addr or "").lower()
    if not a.startswith("0x") or len(a) != 42:
        return False
    if a in _code_cache:
        return _code_cache[a]
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_getCode", "params": [a, "latest"]}).encode()
    try:
        req = urllib.request.Request(_RPC, data=body,
                                     headers={"Content-Type": "application/json", "User-Agent": "lp-scanner/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            code = (json.loads(r.read().decode()) or {}).get("result", "0x")
        out = code not in ("0x", "", None)
    except Exception:
        out = False
    _code_cache[a] = out
    return out


# 销毁地址：持有到这里的 LP 永远撤不走
BURN = {"0x0000000000000000000000000000000000000000",
        "0x000000000000000000000000000000000000dead"}

API = "https://api.gopluslabs.io/api/v1/token_security/4663"

# 致命项：命中任何一条直接淘汰，交易数据再好也不做
FATAL = {
    "is_honeypot": "蜜罐合约，买得进卖不出",
    "selfdestruct": "合约可自毁",
    "hidden_owner": "存在隐藏的所有者",
    "can_take_back_ownership": "所有权可被收回",
    "owner_change_balance": "所有者可任意修改任何人的余额",
    "transfer_pausable": "转账可被暂停，随时冻结你的资产",
    "cannot_sell_all": "无法全部卖出",
}

# 高风险项：不直接淘汰，但显著压低仓位并明确警告
HIGH = {
    "is_mintable": "可无限增发，持有人面临稀释风险",
    "is_blacklisted": "存在黑名单，地址可能被禁止交易",
    "slippage_modifiable": "交易税可被随意修改",
    "personal_slippage_modifiable": "可针对特定地址设置不同税率",
}

# 中风险项：提示即可
MEDIUM = {
    "trading_cooldown": "有交易冷却限制",
    "is_proxy": "代理合约，逻辑可被替换",
}

# LP 集中度：单个持有者占比超过这个数，他一撤走池子就塌
# 不用 GMGN 那个"LP 锁定 < 80%"的判据：那是给 v2 式 LP 代币设计的，
# 靠销毁 LP 代币来证明锁定。Uniswap v4 的 LP 是 NFT，本来就没有"销毁 LP 代币"这回事，
# 实测这条链上几乎所有池的 is_locked 都是 0，照搬会把所有池误报成高危。
# 对 v4 真正有意义的是集中度：少数几个地址持有大部分 LP，才是抽血跑路的风险。
LP_TOP1_MAX = 60.0
LP_TOP3_MAX = 85.0

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


# 查一个代币的合约安全。返回 None 表示查不到，调用方要按"未知"处理，不能当作安全
def check(token):
    token = token.lower()
    if token in _cache:
        return _cache[token]

    d = _get(f"{API}?contract_addresses={token}")
    result = (d or {}).get("result") or {}
    key = next(iter(result), None)
    if not key:
        # 查询失败不写缓存：否则一次限流就会让这个代币永远显示"未知"
        return None
    t = result[key]

    on = lambda f: str(t.get(f, "0")) == "1"

    fatal = [msg for f, msg in FATAL.items() if on(f)]
    high = [msg for f, msg in HIGH.items() if on(f)]
    medium = [msg for f, msg in MEDIUM.items() if on(f)]

    # LP 集中度
    lp = t.get("lp_holders") or []
    pcts = sorted((float(x.get("percent") or 0) * 100 for x in lp), reverse=True)
    top1 = pcts[0] if pcts else 0.0
    top3 = sum(pcts[:3]) if pcts else 0.0
    # lp_holder_count 可能缺失。缺失时退回用列表长度；两者都没有就是"查不到"，
    # 绝不能当成 0 去触发"持有者过少"的高危判定
    raw_n = t.get("lp_holder_count")
    n_lp = int(raw_n) if raw_n not in (None, "") else len(lp)
    lp_known = bool(lp) or raw_n not in (None, "")
    locked = sum(float(x.get("percent") or 0) for x in lp if str(x.get("is_locked")) == "1") * 100

    # 区分"撤不走的"和"随时能撤的"：销毁地址和合约（锁仓器/launchpad）算前者
    safe_pct = 0.0
    eoa_top = 0.0
    for h in lp:
        addr = (h.get("address") or "").lower()
        pc = float(h.get("percent") or 0) * 100
        if addr in BURN or str(h.get("is_locked")) == "1" or is_contract(addr):
            safe_pct += pc
        else:
            eoa_top = max(eoa_top, pc)

    if lp_known and eoa_top > LP_TOP1_MAX:
        high.append(f"最大的个人持有者占 {eoa_top:.0f}% LP，他一撤走池子就塌")
    elif lp_known and n_lp <= 2 and safe_pct < 50:
        high.append(f"LP 持有者只有 {n_lp} 个且未锁仓，池子随时可能被抽干")
    elif lp_known and eoa_top > 30:
        medium.append(f"最大个人持有者占 {eoa_top:.0f}% LP，撤走会明显冲击流动性")
    elif lp_known and safe_pct >= 80:
        medium_ok = f"LP 有 {safe_pct:.0f}% 锁在合约或销毁地址，撤不走"

    if str(t.get("is_open_source", "1")) == "0":
        medium.append("合约未开源，无法审计")

    def tax(f):
        v = t.get(f)
        try:
            return float(v) * 100 if v not in (None, "") else 0.0
        except (TypeError, ValueError):
            return 0.0

    bt, st = tax("buy_tax"), tax("sell_tax")
    if bt > 5 or st > 5:
        high.append(f"交易税偏高（买 {bt:.1f}% / 卖 {st:.1f}%），会吃掉手续费收益")

    # 关键字段全为空 = 接口没有这个代币的数据，只是没报错而已。
    # 这种情况必须返回 None 走"未知"分支，否则会因为"没查出问题"而误判成干净，
    # 实测 UBIK 就是这样拿到合约满分的。
    # 判据放宽会漏：UBIK 的 is_open_source 有值但 holder_count 和 lp_holders 全空，
    # 结果"没查出问题"被当成干净并给了满分。持有人数才是这个代币被收录的硬标志。
    holders_known = str(t.get("holder_count") or "") not in ("", "0")
    if not holders_known and not lp:
        return None

    out = {
        "fatal": fatal, "high": high, "medium": medium,
        "holders": int(t.get("holder_count") or 0),
        "lp_locked_pct": locked,
        "lp_holders": n_lp,
        "lp_top1": top1,
        "lp_top3": top3,
        "lp_safe_pct": safe_pct,
        "lp_eoa_top": eoa_top,
        "buy_tax": bt, "sell_tax": st,
        "level": "致命" if fatal else ("高危" if high else ("注意" if medium else "干净")),
    }
    _cache[token] = out
    return out


# 安全等级对应的仓位系数：合约有问题就该少投
SIZE_FACTOR = {"干净": 1.0, "注意": 0.8, "高危": 0.35, "致命": 0.0, "未知": 0.5}


# 汇总成一行说明，供报告展示
def summary(s):
    if s is None:
        return "未知", "合约安全查不到，按未知处理，仓位减半"
    if s["fatal"]:
        return "致命", "；".join(s["fatal"])
    if s["high"]:
        return "高危", "；".join(s["high"])
    if s["medium"]:
        return "注意", "；".join(s["medium"])
    return "干净", f"未发现合约层风险，持有人 {s['holders']}，LP 持有者 {s['lp_holders']} 个（最大占 {s['lp_top1']:.0f}%）"
