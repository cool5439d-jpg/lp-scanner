// 扫描器前端。轮询后台任务状态，扫完渲染排名和三档方案

const $ = (id) => document.getElementById(id)
const money = (x) => '$' + Math.round(x).toLocaleString()
const pct = (x) => (x >= 0 ? '+' : '') + x.toFixed(1) + '%'
const cls = (x) => (x > 1 ? 'pos' : x < -1 ? 'neg' : 'mid')

let timer = null

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
    <div class="hint">代币地址 <span class="addr" onclick="copy('${r.token}')">${r.token}（点击复制）</span></div>
    ${decisionBlock(v.decision)}
    <div class="plans">${ps.map((p, i) => planCard(p, i === 1)).join('')}</div>
    <div class="shared">
      <div class="kv"><span>池选择</span> auto</div>
      <div class="kv"><span>费率</span> ${mid.fee}</div>
      <div class="kv"><span>tick 间距</span> 留空</div>
      <div class="kv"><span>最大偏离</span> 10</div>
      <div class="kv"><span>区间写法</span> 相对现价</div>
      <div class="kv"><span>形状</span> spot</div>
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
    <td>${r.name}</td>
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

window.copy = (t) => navigator.clipboard.writeText(t)

// 打开页面时若已有上次结果就直接显示
;(async () => {
  const s = await (await fetch('/api/status')).json()
  if (s.running) { $('go').disabled = true; $('go').textContent = '扫描中…'; timer = setInterval(poll, 1200) }
  else if (s.hasResult) render(await (await fetch('/api/result')).json())
})()
