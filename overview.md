# 交付概览

## 完成内容

在 `/Users/shidiyang/WorkBuddy/Shopping-deal-agent` 交付了可运行的 **Shopping Deal Agent** MVP：
Python 3.11+ / Scrapling 0.5 / click CLI / pydantic / SQLite / APScheduler，无前端。

- 合规抓取层：robots.txt 严格校验与缓存、按域限速、瞬时错误指数退避重试、403/CAPTCHA 立即短路。
- 解析层：JSON-LD → 内嵌结构化数据（__NEXT_DATA__/OpenGraph/microdata）→ 五站点 CSS 适配器；三策略价格冲突检测。
- 验证门：`verified / unverified / blocked / error` 分级，唯一告警入口 `Observation.can_alert`。
- 存储：7 张表，价格/尺码/促销历史、告警去重、抓取日志，Decimal 以 TEXT 精确存储。
- 规则：目标价、折扣率、适用促销、XS/S 补货、有意义降价（只与历史 verified 价格比较）。
- 告警：Telegram + SMTP、去重 + 冷却、默认 dry-run。
- CLI、定时调度、JSON 结构化日志、70 个全离线自动化测试。

## 关键决策

1. **Scrapling 0.5 没有 `DynamicFetcher`**（只有 `Fetcher` 与 `StealthyFetcher`）。因用户明确禁止绕过认证/CAPTCHA，不使用带反检测与 Cloudflare 求解的 `StealthyFetcher`；JS 渲染改用普通 Playwright headless，HTML 仍交给 Scrapling `Selector` 解析。
2. **站点访问分化**：`adidas.com.sg` / `macys.com` 连 `robots.txt` 都返回 403 WAF 拦截页，合规行为是标记 `blocked` 且不告警；两站已从默认监控清单移除。`aloyoga.com` 的 robots.txt 明确允许公开产品页，已跑通真实抓取链路并设为默认示例商品。离线 fixture 演示（`demo`）用于不依赖外网地验证全链路。注意：403 是技术层面的访问拒绝，不等于「站点条款明文禁止爬虫」。
3. **不使用任何缓存/搜索结果参与判定**：`from_cache`、`source_kind` 为 search_result/cache/third_party 一律 unverified；历史价格只取 verified 行。
4. **保守判定**：币种不明、价格区间、多策略价格冲突、页面缺货、页面过期、低置信度 → 全部 unverified。促销只有页面明确 unrestricted 才算 applicable。
5. 抓取层惰性导入 + `PageFetcher` 协议注入，使全部核心逻辑与测试不依赖浏览器或外网。

## 验证结果

- 70 个自动化测试全部通过（`pytest -q` 标准命令直接通过，exit 0）。
- CLI 端到端冒烟：`demo` 解析出 S$42.00 / 原价 S$65.00 / 35.38% 折扣，命中 3 个条件并写入 dry-run 告警；`alerts`、`history 1`、`verify-config`、`sda --help` 均正常。
- 38 个 Python 文件 AST 校验通过，`scripts/setup.sh` 通过 `bash -n`。

## 本地环境修复（2026-11-06）

用户执行 `python3 -m venv .venv` 时遇到 `ensurepip ... returned non-zero exit status 1`。根因是
终端里的 `python3` 指向 macOS 自带 `/usr/bin/python3` (**3.8.2**)，低于项目要求的 ≥3.11 且其
ensurepip 无法完成；`.venv` 中因此残留了 `python3.8` 与 `python3.13` 两套 lib 目录、
`pyvenv.cfg` 与 `bin/python3` 版本错位。

处理：

- 用 3.13.12 解释器重跑 venv（幂等修正 `pyvenv.cfg`，不丢已装依赖），清理空的 `lib/python3.8`。
- 执行 `pip install -e ".[dev]"`，使激活环境后可直接 `python -m shopping_deal_agent` / `sda`，无需 `PYTHONPATH`。
- 新增 `scripts/setup.sh`：自动挑选 ≥3.11 解释器、显式排除 `/usr/bin/python3`、幂等修复 venv 并安装项目；`PYTHON=...` 可覆盖。
- `README.md` 补充版本前置检查与「环境故障排查」表。
- `demo` 改用独立库 `data/demo.sqlite3` 且每次重置，不再污染正式数据，重复运行也不会被去重吞掉 3 条告警。

## 真实网络抓取验证（2026-11-06）

用户要求用 Scrapling 实抓。核对 GitHub 主线与 PyPI（最新 0.5.1，本机 0.5）后结论：

- 正面对照：`quotes.toscrape.com` → HTTP 200，`Selector` 解析出 10 条数据，抓取/解析链路本身可用。
- 站点分化：`adidas.com.sg`、`macys.com` 的 robots.txt 返回 403（页面文案明确写「prevent bots entering our site」）；`ralphlauren.com` 本机 SSL 证书校验失败；`lululemon.com` 超时；`aloyoga.com` 返回 200 且 `Allow: /`，并附带 `agents.md` / UCP 条款（身份认证要求针对购物车与结算，只读价格监控不触碰）。
- 合规修正：Scrapling `Fetcher` 默认 `stealthy_headers=True`（伪造浏览器头 + `Referer: google.com`）且默认伪装 Chrome TLS 指纹。已显式改为 `stealthy_headers=False` + `impersonate=None`，只发送可识别 UA，并新增回归测试。
- 真实跑通：Alo Yoga `w5473r-high-waist-airbrush-legging-anthracite` → 200 / 504 KB HTML → `verified` / `S$165.00 SGD` / 策略 `embedded` → 1 条 dry-run 告警 → 落库。
- 未启用 `StealthyFetcher`（其官方 README 明示 anti-bot bypass 与 Cloudflare Turnstile 求解），未使用代理轮换、UCP/MCP 购物车或结算端点。

Alo Yoga 静态 HTML 无可靠尺码/库存结构化标记（`sizes={}` / `availability=UNKNOWN`），补货检测在该站暂不可用，价格链路正常。

## 移除 adidas 监控项（2026-11-06）

用户要求「拿掉 adidas」。先澄清前提：并无「站点条款明文禁止爬虫」的证据，实测到的只是
WAF 403 拦截（连 robots.txt 都拿不到）；`macys.com` 同样 403，`ralphlauren.com` 是本机
SSL 校验失败、`lululemon.com` 是超时，均不等于条款禁止。按用户最初定的「尊重访问限制」边界，
403 已足以停止访问。

改动：

- 新增 `Store.delete_watch`（显式级联清理 observations / observation_sizes /
  observation_promotions / alerts / fetch_log，因 `PRAGMA foreign_keys=ON`）与 CLI
  `watch remove <id> [--yes]`；新增 2 个回归测试（级联清理、不影响其它 watch）。
- 主库 `data/deals.sqlite3` 删除 adidas watch #1 及其全部历史，删除前用 SQLite backup API
  备份到 `data/deals.sqlite3.bak-20261106`。
- `config/watchlist.example.json` 改用已验证可抓的 Alo Yoga 商品，目标价 S$150 / SGD
  （当前 S$165，等降价才告警），并导入主库成为唯一监控项。
- 修正 README 里 `watch import` 的错误用法（实际需要 `--file`）。
- 离线夹具 `tests/fixtures/adidas_*.html` 与 `demo` **保留**：它们是测试数据，不是监控目标。

验证：真实 `check` 只请求 `aloyoga.com`，主库 `watches` / `fetch_log` 中 adidas 记录均为 0；
70 个测试全绿。

## 主要文件

`README.md`（架构、合规边界、安装、CLI、测试、限制）、`scripts/setup.sh`、`src/shopping_deal_agent/`、`tests/`、`config/watchlist.example.json`、`.env.example`。

## 后续可做

接入站点官方授权商品接口并标注 `SourceKind.OFFICIAL_API`；按真实 DOM 校准 CSS 适配器；规模化时迁移 PostgreSQL 与分布式调度。
