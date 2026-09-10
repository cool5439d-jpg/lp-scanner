# 桌面提示：弹窗加音效
#
# 提示门槛刻意设得很严，宁缺毋滥。门槛低了你会被无关提示淹没，
# 然后开始忽略它们，那这套东西就白做了。
#
# 两级提示：
#   机会级 - 决策卡判定为"投"（七项无红灯且总分达标）才响
#   预警级 - 持仓池出现两个以上撤退信号
#
# 同一个池只提示一次。判定发生变化才会再提示，否则同一个机会响八遍你就麻木了。

import json
import os
import subprocess
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SEEN_FILE = os.path.join(HERE, "notified.json")
ALERT_LOG = os.path.join(HERE, "alerts.log")

# 已提示记录过期时间：超过这么久再出现同一个池，重新算新机会
SEEN_TTL_SEC = 6 * 3600


def _load_seen():
    if not os.path.exists(SEEN_FILE):
        return {}
    try:
        with open(SEEN_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_seen(d):
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except OSError:
        pass


# PowerShell 5 不支持 [type]::new()，必须用 New-Object。
# CreateToastNotifier 需要一个 AppID，随便给个已注册的即可，不影响显示。
TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime]
$tpl = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$t = $tpl.GetElementsByTagName('text')
[void]$t.Item(0).AppendChild($tpl.CreateTextNode($env:LP_TITLE))
[void]$t.Item(1).AppendChild($tpl.CreateTextNode($env:LP_BODY))
$n = New-Object Windows.UI.Notifications.ToastNotification $tpl
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('MSEdge').Show($n)
[System.Media.SystemSounds]::Exclamation.Play()
Start-Sleep -Milliseconds 600
"""


def _toast(title, body):
    env = dict(os.environ, LP_TITLE=title[:80], LP_BODY=body[:160])
    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", TOAST_PS],
                       env=env, capture_output=True, timeout=25)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _log(line):
    try:
        with open(ALERT_LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {line}\n")
    except OSError:
        pass


# 机会提示。verified 是终审通过的池，只对判定为"投"的发通知
def opportunities(verified):
    seen = _load_seen()
    now = time.time()
    seen = {k: v for k, v in seen.items() if now - v.get("ts", 0) < SEEN_TTL_SEC}
    sent = []

    for v in verified:
        r, dec, pl = v["pool"], v["decision"], v["plans"]
        if dec["verdict"] != "投":
            continue

        key = r["pool_addr"]
        # 判定没变就不重复提示
        prev = seen.get(key)
        if prev and prev.get("verdict") == dec["verdict"] and prev.get("score") == dec["score"]:
            continue

        best = pl[1]
        title = f"LP 机会 {r['name']}"
        body = (f"{dec['score']}/14 分 · {r.get('phase','')}期 · 资金{r.get('flow_level','')} · "
                f"建议 {best['budget']} USDG · 区间 {best['range']} · 形状 {best['shape']}")
        _toast(title, body)
        _log(f"[机会] {r['name']}  {dec['score']}/14  {r.get('phase')}期  "
             f"建议 {best['budget']} USDG 区间 {best['range']}  {r['token']}")
        seen[key] = {"ts": now, "verdict": dec["verdict"], "score": dec["score"]}
        sent.append(r["name"])

    _save_seen(seen)
    return sent


# 持仓预警。pools 是你正持仓的池在本轮扫描里的记录
def warnings(pools, min_signals=2):
    seen = _load_seen()
    now = time.time()
    sent = []

    for r in pools:
        sigs = r.get("exit_signals") or []
        if len(sigs) < min_signals:
            continue

        key = "warn:" + r["pool_addr"]
        prev = seen.get(key)
        if prev and prev.get("n") == len(sigs):
            continue

        title = f"持仓预警 {r['name']}"
        body = f"{len(sigs)} 个撤退信号：{sigs[0][:70]}"
        _toast(title, body)
        _log(f"[预警] {r['name']}  {len(sigs)} 个信号：{'；'.join(sigs)}")
        seen[key] = {"ts": now, "n": len(sigs)}
        sent.append(r["name"])

    _save_seen(seen)
    return sent


# 从 rh-uni 读当前持仓所在的池子 id，用来做持仓预警。读不到就返回空集合，
# 上层据此跳过预警，不会因为 rh-uni 没开就报错。
# 按池匹配而不是按代币：同一个代币常有好几个费率的池，你只持有其中一个，
# 其它池的撤退信号和你无关。rh-uni 的仓位里带 poolId，和扫描行的 pool_addr 同源可直接比对。
RH_UNI_API = "http://127.0.0.1:3000/api/positions"


def held_pools():
    try:
        req = urllib.request.Request(RH_UNI_API, headers={"User-Agent": "lp-scanner/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode("utf-8"))
        return {str(p.get("poolId", "")).lower() for p in (d.get("positions") or []) if p.get("poolId")}
    except Exception:
        return set()


def test():
    ok = _toast("LP 扫描器测试", "这是一条测试提示，收到说明通知正常")
    print("测试通知已发送" if ok else "通知发送失败")
