# Shopping Deal Agent

Python + Scrapling 的时尚商品价格 / 补货监控 MVP。单进程、CLI + SQLite，**无前端**。

监控目标：Adidas Singapore、Macy's、Ralph Lauren、Alo Yoga、Lululemon 的指定商品页（按各站实际访问许可启用）。
默认示例商品（已验证可抓）：`https://www.aloyoga.com/products/w5473r-high-waist-airbrush-legging-anthracite`
示例告警条件：价格 ≤ S$150、折扣 ≥ 30%、适用的额外促销、XS 或 S 补货。

> **adidas 已从默认监控清单移除。** 实测 `www.adidas.com.sg/robots.txt` 返回 `HTTP 403`，响应体是 Akamai WAF 拦截页（`Server: AkamaiNetStorage`，3.4 KB HTML），**不含任何 robots 指令**。页面原文是 "During high-traffic product releases we have extra security in place to prevent bots entering our site."——限定于高流量新品发售时段的额外安全措施。这是**运行时技术拒绝**（WAF 拦截页），不是服务条款或 `robots.txt` 里的规范性禁令；「没有读到条款明文禁止爬虫」指的是后者，两者并不矛盾。但站点已明确表达反 bot 立场，按「尊重访问限制」的边界就应停止访问。`macys.com` 同样 403；`ralphlauren.com` 是本机 SSL 证书校验失败、`lululemon.com` 是超时，都不等于条款禁止。只有 `aloyoga.com` 的 robots.txt 明确允许公开产品页。

---

## 架构

```
调度 (CLI / APScheduler)
   ↓
合规抓取层  robots.txt 严格校验 · 按域限速 · 指数退避重试 · Scrapling Fetcher
            · 可选普通 Playwright 渲染 JS（不隐身、不求解验证码）
   ↓
解析层      JSON-LD Product/Offer  →  __NEXT_DATA__ / OpenGraph / microdata  →  站点 CSS 适配器
            三策略交叉校验，价格冲突即判歧义
   ↓
验证门      verified / unverified / blocked / error —— 唯一告警入口
   ↓
SQLite      watches · observations · observation_sizes · observation_promotions
            · alerts · fetch_log
   ↓
规则判定    目标价 / 折扣 / 促销 / 补货 / 有意义降价（与上一次 verified 价格比较）
   ↓
告警分发    Telegram + Email · 去重 · 冷却 · 默认 dry-run
```

### 一条不可绕过的规则

告警的唯一入口是 `Observation.can_alert`，必须**同时**满足：

1. `status == verified`
2. `from_cache == False`
3. `source_kind ∈ {product_page, official_api}`
4. 价格非空、币种明确且与监控目标币种一致
5. 抓取时间未超过 `SDA_PAGE_MAX_AGE_SECONDS`（默认 300s）

因此缓存响应、搜索结果页、第三方比价、403 拦截页、价格区间歧义、多策略价格冲突、币种不明、整体缺货 —— 一律落到 `unverified / blocked / error`，只记日志、不发告警。降价对比的历史值也只从 `verified` 行读取。**绝不会仅凭缓存搜索结果报折扣。**

---

## 合规边界（请务必阅读）

- 默认 `SDA_ROBOTS_STRICT=true`：读取并缓存各域 `robots.txt`（TTL 6h），禁止的路径直接跳过；**无法确认 robots 时也拒绝抓取**。
- 使用可识别的 `User-Agent`，请在 `.env` 里换成真实联系方式。
- 每域名最小请求间隔默认 3 秒；只对超时 / 408 / 425 / 429 / 5xx 做带抖动的指数退避。
- **403 / 401 / 验证码 / 登录墙立即短路，不重试、不换传输、不升级到浏览器重试**。
- 明确禁用 `StealthyFetcher`、Cloudflare 求解、代理轮换、指纹伪装。浏览器只用于渲染允许自动访问的 JS 页面。
- Scrapling `Fetcher` 默认 `stealthy_headers=True`（伪造浏览器头 + `Referer: https://www.google.com/`）且默认伪装 Chrome TLS 指纹。本项目显式传 `stealthy_headers=False` + `impersonate=None`，只发送可识别 UA；`tests/test_compliance.py` 有回归测试锁住这一点。
- 优先官方接口 / 结构化数据；不绕过认证、付费墙、CAPTCHA。

> 实测分化：`www.adidas.com.sg`、`www.macys.com` 连 `robots.txt` 都返回 **403 安全拦截页**，strict 模式直接短路为 `blocked` 且**不发告警**——这是预期行为，不是故障。两站已从默认监控清单移除。`www.aloyoga.com` 的 robots.txt 明确允许公开产品页，真实链路已验证，见下方「真实抓取验证」。
>
> 使用前请自行确认各站点服务条款与 robots 指令是否允许此类访问，并把请求频率调到站点可接受的水平。

---

## 项目结构

```
Shopping-deal-agent/
├── pyproject.toml · requirements.txt · .env.example · .gitignore
├── config/watchlist.example.json
├── scripts/setup.sh              幂等环境安装（自动避开 macOS 自带 Python 3.8）
├── src/shopping_deal_agent/
│   ├── models.py money.py config.py clock.py logging_setup.py
│   ├── verify.py store.py rules.py notify.py agent.py scheduler.py cli.py __main__.py
│   ├── fetch/
│   │   ├── protocol.py          注入协议与异常
│   │   ├── compliance.py        robots / 限速 / 重试 / 拦截识别
│   │   ├── scrapling_fetcher.py HTTP(Scrapling) + 普通 Playwright
│   │   └── fixture_fetcher.py   离线夹具传输
│   └── parse/
│       ├── dom.py common.py    Scrapling Selector 封装 / 归一化
│       ├── jsonld.py embedded.py css.py promotions.py
│       └── parser.py           策略链 + 冲突检测
├── tests/                      10 个离线测试模块 + 6 个 HTML 夹具
└── data/                       SQLite、日志（运行时生成，已 gitignore）
```

---

## 本地安装

> ⚠️ **先确认解释器**：本项目要求 **Python ≥ 3.11**。macOS 自带的 `/usr/bin/python3`
> 是 **3.8.2**，用它执行 `python3 -m venv .venv` 会报
> `Command '['.../ensurepip', '--upgrade', '--default-pip']' returned non-zero exit status 1`，
> 且 3.8 无法运行项目代码（用了 3.10+ 类型语法）。

```bash
python3 -V && which python3   # 必须 ≥ 3.11，且不能是 /usr/bin/python3
```

一键版（自动挑选 ≥3.11 的解释器、幂等修复已有 venv、装好 editable 包）：

```bash
./scripts/setup.sh       # 等价于下面整段手动步骤
```

手动版 —— 用明确的 3.11+ 解释器创建环境（本机实测 3.13.12 + Scrapling 0.5）：

```bash
cd /Users/shidiyang/WorkBuddy/Shopping-deal-agent
PYTHON=/Users/shidiyang/.workbuddy/binaries/python/versions/3.13.12/bin/python3
"$PYTHON" -m venv .venv       # 或简写：python3.13 -m venv .venv
source .venv/bin/activate
python -V                      # 再次确认：Python 3.13.12
pip install -e ".[dev]"

# 仅在启用 SDA_BROWSER_FALLBACK=true 时需要：
playwright install chromium

cp .env.example .env          # 按需修改，尤其 User-Agent 与凭证
python -m shopping_deal_agent init-db
python -m shopping_deal_agent watch import --file config/watchlist.example.json
```

`pip install -e .` 之后可用 `sda` 代替 `python -m shopping_deal_agent`；
未做 editable install 时需设置 `PYTHONPATH=src`，否则会报
`ModuleNotFoundError: No module named 'shopping_deal_agent'`。

注意 `pip install scrapling` **不含 fetchers**，必须装 `scrapling[fetchers]`（pyproject 已声明）。

### 环境故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `ensurepip ... returned non-zero exit status 1` | `python3` 指向 macOS 自带 3.8.2 | 用上文的 `$PYTHON` 绝对路径，或 `python3.13` |
| `.venv/lib` 里同时有 `python3.8` 和 `python3.13` | 用两个不同解释器先后建过同一个 venv | `"$PYTHON" -m venv --clear .venv && pip install -e ".[dev]"` |
| `pyvenv.cfg` 的 `version` 与 `.venv/bin/python -V` 不一致 | 同上，配置与二进制错位 | 用同一个解释器重跑 `"$PYTHON" -m venv .venv`，不会丢已装依赖 |
| `ModuleNotFoundError: shopping_deal_agent` | 未做 editable install 且未设 `PYTHONPATH` | `pip install -e ".[dev]"` |

**不要在已存在的 `.venv` 上换解释器重跑 venv**；环境已就绪时只需 `source .venv/bin/activate`。

---

## 一键离线演示（推荐先跑这个）

```bash
python -m shopping_deal_agent demo
```

不联网、强制 dry-run、使用 `tests/fixtures/adidas_verified_deal.html` 这个**本地 HTML 夹具**跑全链路。
夹具只是离线测试数据（文件名沿用早期调研时的商品），不代表当前监控目标；真实监控清单见 `config/watchlist.example.json`。
演示数据写入**独立库 `data/demo.sqlite3` 并在每次运行时重置**，因此不会污染正式数据，
重复运行也不会被告警去重吞掉输出。预期输出：`verified`、`S$42.00`、原价 `S$65.00`、
折扣 `35.38%`，并生成 3 条 dry-run 告警（目标价、折扣、额外促销）。随后可查看：

```bash
python -m shopping_deal_agent --db data/demo.sqlite3 alerts
python -m shopping_deal_agent --db data/demo.sqlite3 history 1
```

显式传入 `--db` 时 demo 会尊重该路径、不自动重置。

---

## 真实抓取验证（合规允许的站点）

`www.aloyoga.com/robots.txt` 返回 200，开头明确写着「Public product … HTML is crawlable」，并附带
`agents.md` / UCP 条款（其中的身份认证要求针对购物车与结算动作；只读价格监控不触碰这些端点）。
实测用 Scrapling `Fetcher` 抓到了真实商品页并跑通全链路：

```bash
URL="https://www.aloyoga.com/products/w5473r-high-waist-airbrush-legging-anthracite"
python -m shopping_deal_agent --db data/alo.sqlite3 init-db
python -m shopping_deal_agent --db data/alo.sqlite3 watch add \
  --url "$URL" --target 200 --currency SGD --size XS --size S
python -m shopping_deal_agent --db data/alo.sqlite3 check
python -m shopping_deal_agent --db data/alo.sqlite3 history 1
python -m shopping_deal_agent --db data/alo.sqlite3 alerts
```

默认清单把目标价设为 **S$150**（当前 S$165，等降价才告警）；下面用 S$200 是为了当场触发一次告警验证链路。

实测结果：`HTTP 200`、504 KB HTML、`verified`、**S$165.00 / SGD**，
策略 `embedded`（meta 结构化数据，置信度 0.8），命中目标价并写入 1 条 dry-run 告警。
站点会 302 到 `/en-sg/products/...`，币种因此是 **SGD** 而不是 USD。

局限：该页静态 HTML 没有可靠的尺码/库存结构化标记，解析得到 `sizes={}` /
`availability=UNKNOWN`，所以补货检测在这类页面上暂不可用；价格链路不受影响。

---

## 日常使用

```bash
python -m shopping_deal_agent verify-config                 # 查看生效配置
python -m shopping_deal_agent check                         # 单次检查全部监控项
python -m shopping_deal_agent check --fixture path/to.html --url "https://..."  # 本地夹具
python -m shopping_deal_agent run --interval 60             # 常驻定时（Ctrl-C 停止）
python -m shopping_deal_agent watch list
python -m shopping_deal_agent watch disable <id>          # 停止监控但保留历史
python -m shopping_deal_agent watch enable <id>
python -m shopping_deal_agent watch remove <id> --yes     # 连同价格/告警历史永久删除
python -m shopping_deal_agent history 1 --limit 20
python -m shopping_deal_agent alerts --limit 20
```

也可用系统 cron 代替常驻进程：

```cron
*/30 * * * * cd /path/to/Shopping-deal-agent && ./.venv/bin/python -m shopping_deal_agent check >> data/cron.log 2>&1
```

添加其它站点商品：

```bash
python -m shopping_deal_agent watch add \
  --url "https://www.macys.com/shop/product/..." \
  --target 80 --currency USD --size XS --size S
```

`parse/css.py` 已为五个目标站点预置 CSS 适配器；主路径始终优先 JSON-LD / 官方结构化数据，CSS 只是最后兜底。

---

## 告警配置

编辑 `.env`（进程环境变量优先于 `.env`）：

| 变量 | 说明 |
|---|---|
| `SDA_DRY_RUN` | 默认 `true`，只写本地记录；设 `false` 才真正发送 |
| `SDA_TELEGRAM_BOT_TOKEN` / `SDA_TELEGRAM_CHAT_ID` | Telegram Bot API |
| `SDA_SMTP_HOST` / `SDA_SMTP_PORT` / `SDA_SMTP_USER` / `SDA_SMTP_PASSWORD` | SMTP |
| `SDA_EMAIL_FROM` / `SDA_EMAIL_TO` | 邮件收发地址 |
| `SDA_ALERT_COOLDOWN_MINUTES` | 同条件冷却，默认 60 |

告警按 `(监控项, 条件, 价格/尺码/促销状态)` 去重；状态变化后可再次触发。
Telegram 使用纯文本发送（无 `parse_mode`），页面标题不会被当作标记解析。

其它常用配置：`SDA_BROWSER_FALLBACK`、`SDA_MIN_REQUEST_INTERVAL_SECONDS`、
`SDA_MAX_RETRIES`、`SDA_ROBOTS_STRICT`、`SDA_PAGE_MAX_AGE_SECONDS`、`SDA_DATABASE_PATH`。

---

## 测试

标准环境：

```bash
pytest -q
```

全部 67 个测试完全离线，使用本地 HTML 夹具 + 内存 SQLite，不发起任何网络请求。
覆盖：金额/币种歧义、三套解析策略、促销适用性、验证门全部拒绝路径、
五类告警规则、存储去重与历史、告警渠道与 dry-run、robots/限速/重试、端到端补货与降价链路。

若运行环境限制临时目录（例如某些沙箱拦截临时文件删除，导致 `tempfile` 报
`No usable temporary directory found`），改用：

```bash
mkdir -p data/pytest-tmp
PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/data/pytest-tmp" \
./.venv/bin/python -c '
import os, sys, tempfile
tempfile.tempdir = os.environ["TMPDIR"]
import pytest
sys.exit(pytest.main(["-p", "no:cacheprovider", "-s", "-q"]))
'
```

---

## 已知限制

- `adidas.com.sg` / `macys.com` 当前返回 403（含 `robots.txt`），真实抓取按合规要求记为 `blocked`；两站已从默认监控清单移除，没有授权数据源就不会产生价格告警。`aloyoga.com` 已验证可抓，见「真实抓取验证」。
- Alo Yoga 静态 HTML 拿不到尺码/库存结构化数据（`sizes={}` / `availability=UNKNOWN`），补货检测暂不可用；接官方商品接口后补齐。
- CSS 适配器是通用选择器，站点改版后需按实际 DOM 校准；生产使用应优先接入官方商品接口并填入 `SourceKind.OFFICIAL_API`。
- 促销适用性判定偏保守：只有页面明确写出 `sitewide / all orders / no exclusions` 等 unrestricted 表述才算 applicable；会员限定、最低消费、排除条款一律不算。
- 颜色偏好目前只做宽松筛选（页面未声明颜色时不阻断），不确定颜色不会把价格标记为 unverified。
- 单进程、无前端、无分布式调度；SQLite 适合单机 MVP，规模化需换 PostgreSQL。
