// 扫描器前端。轮询后台任务状态，扫完渲染排名和三档方案

const $ = (id) => document.getElementById(id)
const money = (x) => '$' + Math.round(x).toLocaleString()
const pct = (x) => (x >= 0 ? '+' : '') + x.toFixed(1) + '%'
const cls = (x) => (x > 1 ? 'pos' : x < -1 ? 'neg' : 'mid')

let timer = null

// 外站跳转：GMGN 交易页（链名用 robinhood，2026-07 起支持）和 GeckoTerminal 池页
const gmgnUrl = (token) => `https://gmgn.ai/robinhood/token/${token}`
const geckoUrl = (pool) => `https://www.geckoterminal.com/robinhood/pools/${pool}`
const links = (token, pool) => `<a class="ext" href="${gmgnUrl(token)}" target="_blank" rel="noopener">GMGN</a>` +
  (pool ? ` <a class="ext" href="${geckoUrl(pool)}" target="_blank" rel="noopener">Gecko</a>` : '')

const setLog = (lines, running) => {
  $('logCard').style.display = 'block'
  $('log').innerHTML = lines.map((l, i) =>
    i === lines.length - 1 && running ? `<span class="now">${l}</span>` : l).join('\n')
  $('log').scrollTop = $('log').scrollHeight
}

const poll = async () => {
  const s = await (await fetch('/api/status')).json()
  setLog(s.log, s.running)
  if (s.running) return
  clearInterval(timer); timer = null
  $('go').disabled = false
  $('go').textContent = '开始扫描'
  if (s.hasResult) render(await (await fetch('/api/result')).json())
}

$('go').onclick = async () => {
  const b = $('budget').value, a = $('available').value, v = $('verify').value
  const r = await (await fetch(`/api/scan?budget=${b}&available=${a}&verify=${v}`)).json()
  if (!r.ok) return alert(r.err)
  $('go').disabled = true
  $('go').textContent = '扫描中…'
  $('out').innerHTML = ''
  timer = setInterval(poll, 1200)
}

$('hist').onclick = async () => {
  const h = await (await fetch('/api/history')).json()
  if (!h.pools.length) return alert('还没有历史记录，先扫描几次')
  const rows = h.pools.map((p) => `<tr>
    <td>${p.name}</td><td>${p.rate.toFixed(0)}%</td><td>${p.hits}/${p.scans}</td>
    <td>${money(p.avg)}</td><td>${(p.spread * 100).toFixed(0)}%</td></tr>`).join('')
  $('out').innerHTML = `<div class="card"><h2>历史稳定性（共 ${h.scans} 次扫描）</h2>
    <table><thead><tr><th>池子</th><th>上榜率</th><th>次数</th><th>平均日入</th><th>收益抖动</th></tr></thead>
    <tbody>${rows}</tbody></table>
    <div class="hint">上榜率高说明长期优质。收益抖动是极差比均值，越大说明越依赖突发行情。</div></div>`
}

const planCard = (p, best) => `
  <div class="plan ${best ? 'best' : ''}">
    <h3>${p.style}${best ? ' ★' : ''}</h3>
    <div class="kv"><span>预算</span><b>${p.budget} USDG</b></div>
    <div class="kv"><span>区间</span><b>${p.range}</b></div>
    <div class="kv"><span>预估日入</span><b class="big">$${p.est_income.toFixed(2)}</b></div>
  </div>`

const poolBlock = (v) => {
  const r = v.pool, ps = v.plans
  const mid = ps[1]
  const swing = Math.abs(mid.budget * r.chg_24h / 100)
  return `<div class="card">
    <h2>${r.name}<span class="tag ok">终审通过</span>${secTag(r)}</h2>
    <div class="hint">${r.band} · 日换手 ${r.turnover.toFixed(1)} 倍 · 24h <span class="${cls(r.chg_24h)}">${pct(r.chg_24h)}</span>
      · 流动性 ${money(r.tvl)} · 笔数 ${r.trades_24h}</div>
    <div class="hint">合约 ${r.sec_level || '?'}：${r.sec_note || ''}</div>
    ${phaseBlock(r)}
    <div class="hint">代币地址 <span class="addr" onclick="copy('${r.token}')">${r.token}（点击复制）</span> ${links(r.token, r.pool_addr)}</div>
    ${decisionBlock(v.decision)}
    <div class="plans">${ps.map((p, i) => planCard(p, i === 1)).join('')}</div>
    <div class="shared">
      <div class="kv"><span>池选择</span> auto</div>
      <div class="kv"><span>费率</span> ${mid.fee}</div>
      <div class="kv"><span>tick 间距</span> 留空</div>
      <div class="kv"><span>最大偏离</span> 10</div>
      <div class="kv"><span>区间写法</span> 相对现价</div>
      <div class="kv"><span>形状</span> ${mid.shape}${mid.layers > 1 ? ' × ' + mid.layers + ' 层' : ''}</div>
      <div class="kv"><span>换币滑点</span> 5</div>
      <div class="kv"><span>LP 余量</span> 5</div>
      <div class="kv"><span>继续监控</span> 勾上</div>
    </div>
    <div class="alert">预估日入只算手续费。该币 24h 波动 ${pct(r.chg_24h)}，按最佳档
      ${mid.budget} USDG 计，本金一天可能变动 ${money(swing)}，通常远大于手续费收入。</div>
    ${sibBlock(v.siblings)}
    ${adviceBlock(v.advice)}
  </div>`
}

const PH = { '拉升': 'warn', '横盘': 'good', '反弹': 'warn', '见顶': 'bad', '崩盘': 'bad', '不明': 'dim' }
const FL = { '涌入': 'good', '正常': 'good', '退潮': 'warn', '枯竭': 'bad', '未知': 'dim' }

const phaseBlock = (r) => {
  if (!r.phase) return ''
  const sigs = r.exit_signals || []
  const sg = sigs.length
    ? `<div class="alert" style="margin-top:8px">撤退信号 ${sigs.length} 个（中两个减仓，中三个清仓）
       <ul style="margin:6px 0 0;padding-left:20px">${sigs.map((x) => `<li>${x}</li>`).join('')}</ul></div>` : ''
  return `<div class="phase">
    <span class="ptag ${PH[r.phase] || 'dim'}">${r.phase}期</span>
    <span class="ptag ${FL[r.flow_level] || 'dim'}">资金${r.flow_level}</span>
    <span style="font-size:13px">${r.phase_note}　→　<b>${r.phase_play}</b></span>
    <div class="hint" style="margin-top:6px">${r.flow_note}</div>
    ${sg}
  </div>`
}

const DV = { '投': 'good', '小仓试': 'warn', '不投': 'bad' }
const ICON = { '绿': '○', '黄': '△', '红': '✕' }
const SCLS = { '绿': 'good', '黄': 'warn', '红': 'bad' }

const decisionBlock = (d) => {
  if (!d) return ''
  const rows = d.rows.map((x) => `<tr>
    <td class="${SCLS[x.state]}" style="width:28px;text-align:center">${ICON[x.state]}</td>
    <td style="width:80px">${x.name}</td>
    <td style="width:44px;text-align:center;color:var(--dim)">${x.points}/2</td>
    <td style="text-align:left;color:var(--dim)">${x.msg}</td></tr>`).join('')
  return `<div class="decision">
    <div class="dhead">
      <span class="dverdict ${DV[d.verdict]}">${d.verdict}</span>
      <span class="dscore">${d.score}/12</span>
      <span class="dreason">${d.reason}</span>
    </div>
    <div class="daction">怎么做：${d.action}</div>
    <table class="dtable"><tbody>${rows}</tbody></table>
  </div>`
}

const VC = { '推荐': 'good', '可做': 'accent', '谨慎': 'warn', '观望': 'bad' }
const SC = { '干净': 'ok', '注意': 'ok', '高危': 'no', '致命': 'no', '未知': 'no' }

const secTag = (r) => {
  if (!r.sec_level) return ''
  const t = `<span class="tag ${SC[r.sec_level] || 'no'}">合约${r.sec_level}</span>`
  return t
}

const sibBlock = (sibs) => {
  if (!sibs || !sibs.length) return ''
  const tr = sibs.map((s) => `<tr><td>${s.name}</td><td>${s.turnover.toFixed(1)}</td>
    <td>$${s.income.toFixed(2)}</td><td>${s.share_pct.toFixed(2)}%</td>
    <td style="text-align:left;color:var(--dim)">${s.note}</td></tr>`).join('')
  return `<div style="margin-top:14px"><div class="hint" style="margin-bottom:6px">同代币其它可用池</div>
    <table><thead><tr><th>池子</th><th>换手</th><th>日入</th><th>占池</th>
    <th style="text-align:left">取舍</th></tr></thead><tbody>${tr}</tbody></table></div>`
}

const adviceBlock = (a) => {
  if (!a) return ''
  const risks = a.risks.length
    ? `<div style="margin-top:10px"><b>要当心</b><ul style="margin:6px 0 0;padding-left:20px">
       ${a.risks.map((x) => `<li>${x}</li>`).join('')}</ul></div>` : ''
  const steps = `<div style="margin-top:10px"><b>下一步</b><ul style="margin:6px 0 0;padding-left:20px">
       ${a.steps.map((x) => `<li>${x}</li>`).join('')}</ul></div>`
  return `<div class="advice">
    <div class="verdict ${VC[a.verdict] || 'accent'}">${a.verdict}</div>
    <div style="margin-top:8px">${a.why}</div>${risks}${steps}</div>`
}

const rankTable = (rows) => {
  const tr = rows.slice(0, 14).map((r) => `<tr>
    <td>${r.name} ${links(r.token, r.pool_addr)}</td>
    <td>${money(r.tvl)}</td>
    <td>${r.turnover.toFixed(1)}</td>
    <td>${r.trades_24h}</td>
    <td class="${cls(r.chg_24h)}">${pct(r.chg_24h)}</td>
    <td>${r.durability.toFixed(0)}%</td>
    <td>$${r.daily_income.toFixed(2)}</td>
    <td>${r.hit_rate == null ? '新' : r.hit_rate.toFixed(0) + '%'}</td></tr>`).join('')
  return `<div class="card"><h2>通过筛选的池（按预估日入排序）</h2>
    <table><thead><tr><th>池子</th><th>流动性</th><th>日换手</th><th>笔数</th>
    <th>24h</th><th>持久度</th><th>日入</th><th>上榜率</th></tr></thead><tbody>${tr}</tbody></table>
    <div class="hint">日换手 = 日成交/流动性，是收入来源。持久度低于 100% 说明成交挤在最近一小时，退潮后收益会掉。</div>
  </div>`
}

const rejectTable = (rows) => {
  if (!rows.length) return ''
  const tr = rows.slice(0, 12).map((r) => `<tr>
    <td>${r.name}</td><td style="text-align:left;color:var(--dim)">${(r.reasons || []).join('；')}</td></tr>`).join('')
  return `<div class="card"><h2>被淘汰的池</h2>
    <table><thead><tr><th>池子</th><th style="text-align:left">原因</th></tr></thead><tbody>${tr}</tbody></table></div>`
}

const render = (d) => {
  if (d.empty) return
  let html = ''
  if (d.verified && d.verified.length) {
    html += `<div class="sub" style="margin-top:20px">终审通过 ${d.verified.length} 个池，下面是可以直接照抄的填法</div>`
    html += d.verified.map(poolBlock).join('')
  }
  html += rankTable(d.passed)
  html += rejectTable(d.rejected)
  $('out').innerHTML = html
}

// ---- 定时监控开关 ----
const fmtTime = (ts) => ts ? new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false }) : '—'

const renderWatch = (w) => {
  const dot = `<span class="wdot ${w.on ? 'on' : 'off'}"></span>`
  $('wtoggle').textContent = w.on ? '停止监控' : '开启监控'
  $('wtoggle').className = w.on ? 'ghost' : ''
  $('wstate').innerHTML = w.on
    ? `${dot}运行中 · 循环 ${w.loops} 个 · 已跑 ${w.rounds} 轮 · 发出 ${w.alerts} 条提示 · 上次 ${fmtTime(w.lastRun)} · 下次 ${fmtTime(w.nextRun)}<br>${w.note}`
    : `${dot}已停止${w.rounds ? ` · 本次会话跑过 ${w.rounds} 轮，发出 ${w.alerts} 条提示` : ''}`
}

const pollWatch = async () => renderWatch(await (await fetch('/api/watch')).json())

$('wtoggle').onclick = async () => {
  const w = await (await fetch('/api/watch')).json()
  const on = w.on ? 0 : 1
  const iv = Math.max(60, Number($('winterval').value || 15) * 60)
  const b = $('budget').value, a = $('available').value
  renderWatch(await (await fetch(`/api/watch?on=${on}&interval=${iv}&budget=${b}&available=${a}`)).json())
}

$('wtest').onclick = async () => {
  await fetch('/api/watch?on=test')
  alert('测试通知已发送，看右下角')
}

$('walerts').onclick = async () => {
  const d = await (await fetch('/api/alerts')).json()
  if (!d.lines.length) return alert('还没有任何提示记录')
  $('out').innerHTML = `<div class="card"><h2>提示记录（最近 ${d.lines.length} 条）</h2>
    <div class="log" style="max-height:400px">${d.lines.join('\n')}</div></div>`
}

pollWatch()
setInterval(pollWatch, 20000)

window.copy = (t) => navigator.clipboard.writeText(t)

// ---- 单币查询 ----
let ltimer = null
let lastLookup = null

const lookupTable = (pools) => {
  if (!pools.length) return ''
  const tr = pools.map((r) => `<tr>
    <td>${r.name}${r.status === '通过' ? '<span class="tag ok">通过</span>' : '<span class="tag no">淘汰</span>'} ${links(r.token, r.pool_addr)}</td>
    <td>${money(r.tvl)}</td><td>${r.turnover.toFixed(1)}</td><td>${r.trades_24h}</td>
    <td class="${cls(r.chg_24h)}">${pct(r.chg_24h)}</td><td>${r.durability.toFixed(0)}%</td>
    <td>$${r.daily_income.toFixed(2)}</td>
    <td style="text-align:left;color:var(--dim);white-space:normal">${r.status === '通过' ? `${r.phase || ''}期 · 资金${r.flow_level || ''}` : (r.reasons || []).join('；')}</td></tr>`).join('')
  return `<table style="margin-top:12px"><thead><tr><th>池子</th><th>流动性</th><th>日换手</th><th>笔数</th><th>24h</th><th>持久度</th><th>日入</th><th style="text-align:left">结论</th></tr></thead><tbody>${tr}</tbody></table>`
}

const renderLookup = (d) => {
  lastLookup = d
  $('ladd').style.display = d.token ? '' : 'none'
  let html = ''
  if (d.note) html += `<div class="alert">${d.note}</div>`
  if (d.candidates && d.candidates.length > 1) {
    html += `<div class="hint" style="margin-top:10px">同名候选（按最大池流动性排序）：</div>` +
      d.candidates.slice(0, 8).map((c) => `<span class="cand" onclick="lookupToken('${c.token}')">${c.symbol} · ${money(c.tvl)} · ${c.pools} 池 · ${c.token.slice(0, 8)}…</span>`).join('')
  }
  if (d.token) html += `<div class="hint" style="margin-top:10px">${d.symbol || ''} 代币地址 <span class="addr" onclick="copy('${d.token}')">${d.token}（点击复制）</span> ${links(d.token, '')}</div>`
  html += lookupTable(d.pools || [])
  if (d.blocks && d.blocks.length) html += d.blocks.map(poolBlock).join('')
  $('lout').innerHTML = html
}

const pollLookup = async () => {
  const s = await (await fetch('/api/lookup_status')).json()
  $('llog').style.display = 'block'
  $('llog').innerHTML = s.log.join('\n')
  $('llog').scrollTop = $('llog').scrollHeight
  if (s.running) return
  clearInterval(ltimer); ltimer = null
  $('lgo').disabled = false
  $('lgo').textContent = '查询'
  if (s.result) renderLookup(s.result)
}

window.lookupToken = async (q) => {
  $('lq').value = q
  const b = $('budget').value, a = $('available').value, v = $('lverify').checked ? 1 : 0
  const r = await (await fetch(`/api/lookup?q=${encodeURIComponent(q)}&budget=${b}&available=${a}&verify=${v}`)).json()
  if (!r.ok) return alert(r.err)
  $('lgo').disabled = true
  $('lgo').textContent = '查询中…'
  $('lout').innerHTML = ''
  $('ladd').style.display = 'none'
  ltimer = setInterval(pollLookup, 1000)
}

// 每个带 data-fold 的板块标题右侧加一个收起键，状态按板块分别记在浏览器里，刷新后保持
document.querySelectorAll('.card[data-fold]').forEach((card) => {
  const key = 'fold:' + card.dataset.fold
  const btn = document.createElement('button')
  btn.className = 'fold'
  btn.title = '收起 / 展开'
  const apply = (folded) => {
    card.classList.toggle('folded', folded)
    btn.textContent = folded ? '展开' : '收起'
    localStorage.setItem(key, folded ? '1' : '0')
  }
  btn.onclick = () => apply(!card.classList.contains('folded'))
  card.querySelector('h2').appendChild(btn)
  apply(localStorage.getItem(key) === '1')
})

$('lgo').onclick = () => lookupToken($('lq').value.trim())
$('lq').onkeydown = (e) => { if (e.key === 'Enter') lookupToken($('lq').value.trim()) }
$('ladd').onclick = async () => {
  if (!lastLookup || !lastLookup.token) return
  const best = (lastLookup.pools || []).find((p) => p.status === '通过') || (lastLookup.pools || [])[0]
  await fetch(`/api/monitor?act=add&token=${lastLookup.token}&symbol=${encodeURIComponent(lastLookup.symbol || '')}&pool=${best ? best.pool_addr : ''}`)
  pollMonitor()
}

// ---- 代币监测器 ----
const fmtPx = (x) => x >= 1 ? x.toFixed(4) : x >= 0.01 ? x.toFixed(5) : x.toPrecision(4)
const FLOWC = { '涌入': 'good', '正常': 'good', '退潮': 'warn', '枯竭': 'bad', '未知': 'dim' }

const spark = (id, series) => {
  const c = document.getElementById(id)
  if (!c || series.length < 2) return
  const w = c.width = c.clientWidth * 2, h = c.height = c.clientHeight * 2
  const ctx = c.getContext('2d')
  const px = series.map((s) => s.price)
  const lo = Math.min(...px), hi = Math.max(...px), span = hi - lo || 1
  const x = (i) => i / (series.length - 1) * (w - 4) + 2
  const y = (p) => h - 4 - (p - lo) / span * (h - 8)
  ctx.clearRect(0, 0, w, h)
  ctx.lineWidth = 2
  ctx.strokeStyle = px[px.length - 1] >= px[0] ? '#35c07f' : '#e05d5d'
  ctx.beginPath()
  series.forEach((s, i) => i ? ctx.lineTo(x(i), y(s.price)) : ctx.moveTo(x(i), y(s.price)))
  ctx.stroke()
  ctx.fillStyle = '#8b93a3'
  ctx.font = '20px Consolas'
  ctx.fillText(fmtPx(hi), 4, 20)
  ctx.fillText(fmtPx(lo), 4, h - 6)
}

const rangeBar = (p) => {
  const lo = p.lo, hi = p.hi, now = p.price
  const span = (hi - lo) || 1
  const pad = span * 0.5
  const min = lo - pad, max = hi + pad
  const pos = (v) => Math.max(0, Math.min(100, (v - min) / (max - min) * 100))
  return `<div class="rng"><div class="band" style="left:${pos(lo)}%;width:${pos(hi) - pos(lo)}%"></div><div class="now ${p.in_range ? '' : 'out'}" style="left:${pos(now)}%"></div></div>
    <div class="mline"><span>仓位 ${p.id} ${p.fee_text}</span><span>区间 ${fmtPx(lo)} ~ ${fmtPx(hi)}</span><span>${p.in_range ? '<b class="pos">在区间内</b>' : '<b class="neg">已出区间</b>'}</span>
    <span>价值 <b>$${p.value.toFixed(0)}</b></span><span>手续费 <b class="pos">$${p.fees_usd.toFixed(2)}</b></span><span>盈亏 <b class="${p.pnl_usd >= 0 ? 'pos' : 'neg'}">$${p.pnl_usd.toFixed(2)}</b></span></div>`
}

const monCard = (e, i) => {
  const s = e.latest
  const sym = e.symbol || e.token.slice(0, 10)
  if (!s) return `<div class="mcard"><div class="mhead"><b>${sym}</b><span class="hint" style="margin:0">等待第一轮</span>${links(e.token, e.pool)}<span class="x" onclick="monRemove('${e.token}')">✕</span></div>${e.error ? `<div class="merr">${e.error}</div>` : ''}</div>`
  const n = s.signals.length
  const lvl = n >= 3 ? 'bad' : n >= 2 ? 'warn' : ''
  const age = Math.round((Date.now() / 1000 - s.ts) / 60)
  return `<div class="mcard ${lvl}">
    <div class="mhead"><b>${sym}</b><span class="px">${fmtPx(s.price)}</span>
      <span class="${cls(s.chg_5m)}" style="font-size:12.5px">5m ${pct(s.chg_5m)}</span>
      <span class="hint" style="margin:0">${age} 分前</span>${links(e.token, s.pool_addr)}<span class="x" title="移出监测" onclick="monRemove('${e.token}')">✕</span></div>
    <div class="mline"><span>1h <b class="${cls(s.chg_1h)}">${pct(s.chg_1h)}</b></span><span>6h <b class="${cls(s.chg_6h)}">${pct(s.chg_6h)}</b></span><span>24h <b class="${cls(s.chg_24h)}">${pct(s.chg_24h)}</b></span>
      <span>换手 <b>${s.turnover.toFixed(1)}</b></span><span>流动性 <b>${money(s.tvl)}</b></span><span>5m 成交 <b>${money(s.vol_5m)}</b></span></div>
    <div style="margin-top:6px"><span class="ptag ${PH[s.phase] || 'dim'}">${s.phase}期</span><span class="ptag ${FLOWC[s.flow] || 'dim'}">资金${s.flow}${s.flow_ratio != null ? ' ' + s.flow_ratio + '×' : ''}</span><span style="font-size:12.5px;color:var(--dim)">${s.phase_play}</span></div>
    <canvas class="spark" id="sp${i}"></canvas>
    <div class="hint" style="margin:0">${s.pool_name} · ${e.series.length} 个样本</div>
    ${s.positions.map(rangeBar).join('')}
    ${n ? `<div class="msig ${lvl}">撤退信号 ${n} 个：${s.signals.join('；')}</div>` : ''}
    ${e.error ? `<div class="merr">最近一次抓取失败：${e.error}（沿用上次读数）</div>` : ''}
  </div>`
}

const renderMonitor = (m) => {
  const dot = `<span class="wdot ${m.on ? 'on' : 'off'}"></span>`
  $('mtoggle').textContent = m.on ? '停止监测' : '开启监测'
  $('mtoggle').className = m.on ? 'ghost' : ''
  if (document.activeElement !== $('minterval')) $('minterval').value = m.interval
  $('msync').checked = !!m.autoSync
  $('mstate').innerHTML = m.on
    ? `${dot}运行中 · ${m.entries.length} 个币 · 已跑 ${m.rounds} 轮 · 上次 ${fmtTime(m.lastRun)} · 下次 ${fmtTime(m.nextRun)} · ${m.note}`
    : `${dot}已停止 · ${m.entries.length} 个币在名单里`
  $('mout').innerHTML = m.entries.length ? `<div class="mon">${m.entries.map(monCard).join('')}</div>` : '<div class="empty">名单为空。上面填地址加入，或点「导入 rh-uni 持仓」。</div>'
  m.entries.forEach((e, i) => spark(`sp${i}`, e.series))
}

const pollMonitor = async () => renderMonitor(await (await fetch('/api/monitor')).json())

window.monRemove = async (t) => {
  if (!confirm('移出监测器？')) return
  renderMonitor(await (await fetch(`/api/monitor?act=remove&token=${t}`)).json())
}

$('mtoggle').onclick = async () => {
  const m = await (await fetch('/api/monitor')).json()
  const iv = Math.max(30, Number($('minterval').value || 60))
  renderMonitor(await (await fetch(`/api/monitor?act=${m.on ? 'off' : 'on'}&interval=${iv}`)).json())
}
$('minterval').onchange = async () => renderMonitor(await (await fetch(`/api/monitor?act=interval&interval=${Math.max(30, Number($('minterval').value || 60))}`)).json())
$('madd').onclick = async () => {
  const t = $('maddr').value.trim()
  const r = await (await fetch(`/api/monitor?act=add&token=${t}`)).json()
  if (r.ok === false) return alert(r.err)
  $('maddr').value = ''
  renderMonitor(r)
}
$('mimport').onclick = async () => renderMonitor(await (await fetch('/api/monitor?act=import')).json())
$('msync').onchange = async () => renderMonitor(await (await fetch(`/api/monitor?act=autosync&on=${$('msync').checked ? 1 : 0}`)).json())

pollMonitor()
setInterval(pollMonitor, 15000)

// 打开页面时若已有上次结果就直接显示
;(async () => {
  const s = await (await fetch('/api/status')).json()
  if (s.running) { $('go').disabled = true; $('go').textContent = '扫描中…'; timer = setInterval(poll, 1200) }
  else if (s.hasResult) render(await (await fetch('/api/result')).json())
})()
