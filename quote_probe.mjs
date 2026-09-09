// 用链上 Quoter 问真实报价，算出价格冲击。给 Python 扫描器当子进程调用。
// 教训来自 HOOD：GeckoTerminal 的 TVL 完全无法预测冲击，同量级 TVL 的两个池冲击能差五十倍，
// 因为集中流动性可以把钱堆在远离现价的地方。只有真实报价作数。
// 用法: node quote_probe.mjs <代币地址> <预算USDG> [费率%]
// 输出一行 JSON: {ok, impact, fee, spacing, poolPrice}

import { makeClients } from '../robinhood-chain-LP/src/common.ts'
import { discoverQuotePools } from '../robinhood-chain-LP/src/pools.ts'

const [token, budgetArg, feeArg] = process.argv.slice(2)
const budget = Number(budgetArg)

const out = (o) => { console.log(JSON.stringify(o)); process.exit(0) }

try {
  const c = await makeClients({ needKey: false, from: '0x0000000000000000000000000000000000000001' })
  const found = await discoverQuotePools(c, token)
  if (!found.length) out({ ok: false, err: 'no-pool' })

  // 没指定费率就取成交量最大的那个池，和工具的 auto 逻辑一致
  const want = feeArg ? Math.round(Number(feeArg) * 10000) : null
  const pick = want ? found.find((f) => f.pool.fee === want) : found.sort((a, b) => b.volume24h - a.volume24h)[0]
  if (!pick) out({ ok: false, err: 'fee-not-found' })

  // 按对半分估算要换掉的金额，和 cli.ts 的做法一致
  const dec = c.cfg.quote.decimals
  const amountIn = BigInt(Math.floor((budget / 2) * 10 ** dec))
  const zeroForOne = pick.pool.currency0.toLowerCase() === c.cfg.quote.address.toLowerCase()
  const s0 = await c.lp.slot0(pick.pool).catch(() => null)
  if (!s0) out({ ok: false, err: 'slot0-failed' })
  // sqrtP 是 sqrt(price) x 2^96，price = currency1 每单位 currency0 的数量（含精度差）
  const sq = Number(s0.sqrtP) / 2 ** 96
  const dec0 = zeroForOne ? c.cfg.quote.decimals : 18
  const dec1 = zeroForOne ? 18 : c.cfg.quote.decimals
  const raw = sq * sq
  // 换成「1 个代币值多少 USDG」
  const priceTokenInQuote = zeroForOne ? (1 / raw) * 10 ** (dec1 - dec0) : raw * 10 ** (dec0 - dec1)
  const spot = priceTokenInQuote
  const q = await c.lp.quoteExactIn(pick.pool, zeroForOne, amountIn).catch(() => null)
  if (!q || !spot) out({ ok: false, err: 'quote-failed' })

  // 冲击 = 实际拿到 / 按现价应拿到 - 1
  const expected = (budget / 2) / spot
  const actual = Number(q) / 10 ** 18
  const impact = actual / expected - 1

  out({ ok: true, impact, fee: pick.pool.fee / 10000, spacing: pick.pool.spacing, poolPrice: spot, name: pick.name })
} catch (e) {
  out({ ok: false, err: String(e?.shortMessage ?? e?.message ?? e).slice(0, 120) })
}
