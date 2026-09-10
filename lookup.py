# 单币查询：给一个代币地址或符号，把它在本链 Uniswap v4 上全部 USDG 池按扫描器的标准过一遍
#
# 全量扫描按成交量翻七页，冷门币或刚上的币根本不在榜上，这条路径专门补这个缺口。
# 判定逻辑完全复用 judge / plans / decide / advice，保证和扫描结果口径一致，
# 不另起一套标准，否则同一个池在两处会给出不同结论。

import re

import advice
import decide
import fetch
import history
import judge
import plans
import siblings
import verify

ADDR_RE = re.compile(r"^0[xX][0-9a-fA-F]{40}$")


# 把输入解析成代币地址。地址直接用；符号走搜索，多个候选时交给用户选
def resolve(query):
    q = query.strip()
    if ADDR_RE.match(q):
        return q.lower(), []
    cands = fetch.search_tokens(q)
    exact = [c for c in cands if c["symbol"].lower() == q.lower()]
    if len(exact) == 1:
        return exact[0]["token"], cands
    if len(cands) == 1:
        return cands[0]["token"], cands
    return None, cands


# 对一个代币的全部池子做完整评估。say 是进度回调
def evaluate(token, budget, available, do_verify, say):
    say("抓取该代币的全部池子…")
    rows = fetch.fetch_token_pools(token, budget)
    say(f"Uniswap v4 + USDG 的池有 {len(rows)} 个")
    if not rows:
        return {"token": token, "symbol": "", "pools": [], "blocks": [], "note": "GeckoTerminal 上没有这个代币的 Uniswap v4 USDG 池，可能是刚建池还没收录，或者它只配了 WETH"}

    passed, rejected = judge.rank(rows)
    passed = history.merge(passed, history.stability(history.load_history()))
    for r in rejected:
        r["hit_rate"] = None
    say(f"通过硬性筛选 {len(passed)} 个，淘汰 {len(rejected)} 个")

    blocks = []
    for i, r in enumerate(passed):
        if do_verify:
            say(f"终审 {i + 1}/{len(passed)}：{r['name']}")
            ok, _, note = verify.dry_run(r["token"], int(budget))
            r["verified"] = ok
            r["verify_note"] = note
            if not ok:
                r["reasons"] = [note]
                rejected.insert(0, r)
                say(f"  淘汰：{note}")
                continue
            say(f"  通过：{note}")
        pl = plans.three(r, available, None)
        sibs = siblings.siblings_of(r, rows, budget, None)
        blocks.append({
            "pool": r, "plans": pl, "siblings": sibs,
            "advice": advice.build(r, pl, sibs, pl[1]["budget"]),
            "decision": decide.build(r, pl[1]["budget"], pl),
        })
    done = {id(b["pool"]) for b in blocks}
    passed = [r for r in passed if id(r) in done]
    # 一张总表：通过的和被淘汰的并排，让人一眼看到这个币所有池的取舍
    table = [dict(r, status="通过") for r in passed] + [dict(r, status="淘汰") for r in rejected]
    table.sort(key=lambda x: -x["tvl"])
    symbol = _symbol(rows[0]["name"])
    return {"token": token, "symbol": symbol, "pools": table, "blocks": blocks, "note": ""}


# 从池名 "USDG / JUGGERNAUT 1%" 或 "AI / USDG 0.23%" 里取代币符号
def _symbol(name):
    parts = [s.strip() for s in re.sub(r"\s[0-9.]+%$", "", name).split("/")]
    for p in parts:
        if p and p != "USDG":
            return p
    return name
