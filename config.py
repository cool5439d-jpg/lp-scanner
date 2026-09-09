# 扫描器配置。阈值都放这里，改这一个文件就能调整选池标准

# 链与协议：只看这个工具真正能做的池（Uniswap v4 + USDG 计价）
NETWORK = "robinhood"
DEX_ID = "uniswap-v4-robinhood"
QUOTE = "USDG"
USDG_ADDRESS = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"

# 抓取多少页，每页 20 个池，按 24 小时成交量降序
PAGES = 7
PAGE_SLEEP = 5

# 默认预算，用来估算日收入
DEFAULT_BUDGET = 500

# 硬性淘汰线：不满足直接出局，不参与排名
MIN_TVL = 50_000          # 流动性太低撤退时卖不掉，会被滑点吃掉本金
MAX_TVL = 3_000_000       # 流动性太厚，小资金占比过低，收益被稀释
MIN_VOL_24H = 200_000     # 日成交太低说明池子没人用
MIN_TRADES_24H = 1_500    # 笔数太少可能是刷量或几笔大单撑起来的
MIN_TURNOVER = 2.0        # 日换手 = 日成交 / 流动性，这是收入的根本来源

# 行情形态淘汰线：数字本身不差，但当下不是进场时机
MAX_PUMP_24H = 30.0       # 24 小时涨超过这个数，进场会在半山腰把币卖光
DUMP_24H = -20.0          # 24 小时跌幅
DUMP_6H = -10.0           # 且 6 小时仍在跌，说明跌势未止，进场就是接刀
MIN_DURABILITY = 40.0     # 持久度 = 实际24h成交 / (1h成交 x 24)，低于此说明是一小时的烟花

# 波动分档，用于给出区间建议
CALM_24H = 5.0            # 24 小时波动小于此，算低波动（代币化股票通常在这一档）
WILD_24H = 25.0           # 大于此算高波动

# 观察模式
HISTORY_FILE = "history.jsonl"
REPORT_FILE = "report.txt"
WATCH_INTERVAL_SEC = 1800  # 观察模式的扫描间隔，半小时一次
