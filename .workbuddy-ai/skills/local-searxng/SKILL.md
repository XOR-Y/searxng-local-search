---
name: local-searxng
description: 用本机自建的 SearXNG 做网页搜索（不限次数、聚合多引擎），并用配套 fetch.py 抓取网页正文。当需要联网搜资料、查最新信息、找文档/论文/代码、读某个网页全文，或想要多引擎交叉结果时使用。也用于排查该服务本身（起不来、结果为空、语言不对）。触发词：本地搜索、SearXNG、搜索一下、帮我查、联网搜、多引擎搜索、看下这个网页、抓正文、读全文。
agent_created: true
---

# 本地 SearXNG 搜索

本机自建的元搜索引擎（SearXNG，**Windows 原生运行**），配合正文抓取器使用，**不限次数、纯本地**。

> 服务端**独立于本仓库**，位于**与本仓库同级目录**的 `searxng-official`（安装方法见 `INSTALL.md`）。

---

## 一、能力

| 能力 | 说明 | 入口 |
|---|---|---|
| 网页搜索 | 多引擎聚合，不限次数。可用引擎见 §5.3 | `searx.bat` |
| 正文抓取 | 从已知 URL 提取正文，输出 Markdown / 纯文本 / HTML / 元信息 | `fetch.bat` |
| 双引擎提取 | `trafilatura` 与 `readability` 同时运行，按「长行占比」自动选优（正文长行占比高，导航与侧栏低） | `fetch.bat`（默认 `--engine auto`） |
| 浏览器渲染 | Playwright 驱动系统 Edge，处理 JS 渲染、反爬、需登录三类页面 | `fetch.bat --render` |
| 登录态持久化 | 持久化浏览器 profile，登录一次后长期有效 | `fetch.bat --login` |
| 服务管理 | 查询、启动、停止 SearXNG 服务 | `searx.bat --status`、`searxng-official\run.bat` / `stop.bat`（§3.5） |

**适用范围**：文章型正文页面。链接密集的索引页与无语义标签的卡片页不在适用范围内，见 §5.2。

---

## 二、运行方式

### 2.1 前置条件

- 操作系统 Windows。
- **SearXNG 服务端为 Windows 原生版**，位于**与本仓库同级目录**的 `searxng-official`（waitress 替代 uWSGI，自带 `.venv`），**不依赖 WSL / Docker / 虚拟机**。安装方法见 `INSTALL.md`。
- Python 环境位于仓库内 `python_embed\`，由 `setup.bat` 首次生成（约 210 MB），**不依赖系统 Python 或 venv**。

### 2.2 首次准备

```bat
setup.bat
```

幂等，可重复执行。

> SearXNG 服务端**无需本仓库安装** —— 它是独立目录 `searxng-official`，自带 `.venv`，双击其中的 `run.bat` 即可运行。

### 2.3 调用约定

- 一律通过仓库根目录的 `searx.bat` / `fetch.bat` 调用；二者以 `%~dp0` 定位仓库内 Python，仓库所在盘符与目录不限。
- 会话工作目录须为仓库根，命令写作 `.\searx.bat` 形式。
- 建议使用 PowerShell 执行（引号不被吞）。使用 Bash 时须以 `cmd //c "..."` 包裹并转义内层引号。
- 本 skill 可能在任意工作区被加载。当前工作区不是本仓库时，改用绝对路径调用（本仓库与 `searxng-official` 为**同级目录**）。

### 2.4 服务自动拉起

`searx.py` 检测到服务未运行时，会自动拉起 Windows 原生 SearXNG（日志写入 `searxng-official\searxng.log`），**不弹出任何窗口**。

- 拉起固定使用 `.venv\Scripts\python.exe` + **`CREATE_NO_WINDOW`** —— 给服务分配一个**隐藏控制台**。**不要**改用 `pythonw.exe`，也**不要**用 `DETACHED_PROCESS`。
- **为什么非要有隐藏控制台**：SearXNG 启动时会调 **4 次 `git`** 读版本号（`searx/version.py` 的 `git show` / `git diff` / `git rev-parse` / `git remote`）。`git` 是控制台程序，**会继承父进程的控制台** —— 有隐藏控制台可继承就不弹窗；父进程若没有控制台（`pythonw.exe` / `DETACHED_PROCESS`），4 个 `git` 就各自新建一个**可见**控制台 → **黑框一闪而过**。
- 窗口是否出现，是判断「自动拉起」还是「手动 `run.bat`」的最快依据：**有窗口 = 有人手动跑的 `run.bat`**。

### 2.5 配置覆盖

端点、安装目录、代理均可环境变量覆盖，无需修改源码。

| 环境变量 | 默认值 | 作用 |
|---|---|---|
| `SEARXNG_URL` | `http://127.0.0.1:8888` | SearXNG 端点 |
| `SEARXNG_DIR` | `<仓库同级目录>\searxng-official` | Windows 原生版安装目录 |
| `LOCAL_SEARCH_PROXY` | `http://127.0.0.1:7897` | Clash 代理 |
| `SEARXNG_IDLE_TIMEOUT` | `900` | 服务端空闲退出秒数，`0` = 常驻（见 §3.5） |

亦可在仓库根放置 `config.bat`（模板为 `config.bat.example`），`searx.bat` / `fetch.bat` 自动加载。

### 2.6 相关文件

| 路径 | 内容 |
|---|---|
| `..\searxng-official\`（与本仓库同级） | SearXNG 服务端（Windows 原生版）：`run.bat` / `stop.bat` / `run.py` / `settings.yml` / `searx\` |
| `browser_data\` | 持久化浏览器 profile（登录态、Cookie） |
| `fetcher\` | `fetch.py` 的实现模块（`config` / `net` / `challenge` / `browser` / `extract` / `output`）。**无独立入口**，只由 `fetch.py` 导入。其中 `anti_detect.js` 是**可自行编辑**的反检测注入脚本 |
| `python_embed\` | 嵌入式 Python 运行时 |

---

## 三、操作方法

### 3.1 搜索

```powershell
.\searx.bat "关键词" --lang zh-CN -n 10
```

| 参数 | 说明 |
|---|---|
| `--lang en` / `zh-CN` | 语言。**须显式指定**，理由见 §4 |
| `--cat it` | 分类：`general` / `it` / `science` / `news` / `images` / `files` |
| `--range week` | 时效：`day` / `week` / `month` / `year` |
| `--page 2` | 翻页 |
| `-n 20` | 返回条数 |
| `--json` | 输出原始 JSON |
| `--status` | 仅查询服务状态 |
| `--timeout N` | 单次查询超时，默认 60 秒 |
| `--no-start` | 服务未运行时**不**自动拉起，直接报错 |

### 3.2 抓取正文

```powershell
.\fetch.bat "https://..." --max 4000
```

| 参数 | 说明 |
|---|---|
| （默认） | 正文 → Markdown，含标题 / 来源 / 日期 / 作者头 |
| `--text` | 正文 → 纯文本 |
| `--html` | 原始 HTML |
| `--meta` | 仅元信息 JSON |
| `-o out.md` | 输出到文件 |
| `--max 4000` | 截断字符数，`0` 表示不截断。长文须指定 |
| `--timeout N` | 抓取 / 渲染超时，默认 30 秒 |
| `--engine auto` | 提取引擎：`auto`（默认）/ `trafilatura` / `readability` |
| `--no-proxy` | 强制直连 |
| `--proxy URL` | 指定代理 |

**请求策略**：优先直连（国内站点更快），失败后自动改走 Clash `127.0.0.1:7897`。HTTP 与渲染模式使用同一套策略。

### 3.3 浏览器渲染

```powershell
.\fetch.bat --login                      # 首次：手动登录，登录态写入 profile
.\fetch.bat <url> --render               # 带登录态渲染抓取
.\fetch.bat <url> --render --headed      # 显示浏览器窗口
.\fetch.bat <url> --render --wait 12     # 慢站：把渲染等待的预算上限提到 12 秒
.\fetch.bat <url> --render --no-scroll   # 禁止自动滚动
.\fetch.bat <url> --render --interactive # 有头打开，手动操作后回车再抓取
```

| 参数 | 说明 |
|---|---|
| `--render` | 使用真实浏览器渲染 |
| `--headed` | 显示浏览器窗口，用于调试或手动过验证 |
| `--interactive` | 有头打开，等待用户在窗口中操作完毕后回车再抓取。此模式不做拦截与挑战判定 |
| `--wait N` | 渲染等待的**预算上限**秒数（默认 6）。脚本自己判断页面何时稳定（水合探针 + 稳定性轮询），通常用不满；慢站可加大 |
| `--no-scroll` | 禁止自动滚动 |
| `--challenge-timeout N` | 挑战页最长等待秒数。默认有头 180 秒、无头 20 秒；显式传值（含 `0`）以传入值为准 |
| `--login` | 打开浏览器手动登录，登录态写入持久化 profile |

**实现**：Playwright + 系统 Edge（`channel="msedge"`，无需下载浏览器）+ 持久化 profile `.\browser_data`。反检测注入脚本独立存放于 `fetcher\anti_detect.js`，**可直接编辑自定义**（改完下次 `--render` 生效）；当前只处理自动化环境确实异常的项 —— 抹除 `navigator.webdriver`、补齐 `window.chrome`、对齐 `languages` / `plugins`，**不再伪造 WebGL 指纹与 Canvas 噪声**（逐项 JS 造假容易互相矛盾、反而成为新指纹）。UA 已覆盖以去除 `HeadlessChrome`。

**代理**：渲染与登录模式同样接受 `--proxy` / `--no-proxy` / `LOCAL_SEARCH_PROXY`。`--login` 与 `--interactive` 默认先探测 Clash 端口，可用则使用，否则直连。

**挑战页处理**：命中 Cloudflare / DataDome / PerimeterX 挑战页特征时自动转入等待，等待期间不关闭页面。通过判据为「连续 2 秒不再是挑战页」，不依据 URL 变化。等待超时后报错退出，不返回中间页。

**交付前校验**：渲染结果统一过后置校验 —— 命中**浏览器错误页**（DNS 失败 / 连接被拒 / 导航超时）或 **SPA 空壳**（可见文本极少）时**直接报错退出、不交付内容**。⚠️ 不能靠内容长度判断：错误页的 HTML 可能高达 300+ KB，提取器照样能从中刨出几百字当「正文」。

### 3.4 直接调用 API

```bash
curl -s "http://127.0.0.1:8888/search?q=关键词&format=json&language=en&categories=it"
```

返回结构：`{query, number_of_results, results:[{url,title,content,engine,...}], answers, infoboxes, unresponsive_engines}`

### 3.5 服务管理

服务端位于**独立目录**（与本仓库同级的 `searxng-official`）：

| 操作 | 做法 |
|---|---|
| 启动（人工） | 双击 `searxng-official\run.bat`（前台运行，**会弹控制台窗口**，窗口即保活，不要关） |
| 停止 | 双击 `searxng-official\stop.bat`，或在启动窗口按 `Ctrl+C` |
| 查状态 | `searx.bat --status` |
| 自动拉起 | `searx.py` 自动完成（`python.exe` + `CREATE_NO_WINDOW`，控制台**隐藏**，**无窗口**），一般无需手动干预 |

**两条路径的区别**：`run.bat` 用 `python.exe`、**有可见窗口**、日志在窗口里；自动拉起也是 `python.exe`，但控制台是**隐藏的**、日志在 `searxng.log`。`stop.ps1` 同时匹配两种解释器，任一路径起的进程都能停。

**空闲自动退出**：服务连续 **15 分钟**无请求会自动退出，把资源还给系统
（等价于原 WSL 的「空闲约 15 分钟回收发行版」）。判定依据是**有没有请求**，不是「启动后多久」——
只要有请求就一直活着，静默满 15 分钟才退；下次调用 `searx.py` 会自动重新拉起。

- 改时长 / 常驻：启动前设 `SEARXNG_IDLE_TIMEOUT`（秒；`0` = 常驻）

---

## 四、警告

| # | 警告 |
|---|---|
| 1 | **搜索必须显式传 `--lang`**。省略时结果语言跟随代理出口节点，将返回非预期语言的搜索结果。 |
| 2 | **渲染类参数仅在带 `--render` 时生效**。`--headed` / `--wait` / `--interactive` / `--no-scroll` / `--challenge-timeout` 单独使用时直接报参数错误并以退出码 2 退出，不再静默忽略。 |
| 3 | **`browser_data\` profile 为独占资源**。两个进程同时执行 `--render` 会互相抢占，必须串行执行。 |
| 4 | **SearXNG 空闲 15 分钟会自动退出**。这是设计行为，**不是故障**；`searx.py` 会自动重新拉起。想让它常驻，设 `SEARXNG_IDLE_TIMEOUT=0`。 |
| 5 | **Clash 未运行时搜索全部超时**。服务经本机 Clash 出网（`127.0.0.1:7897`）。 |
| 6 | **`.bat` 文件不得包含中文**。UTF-8 无 BOM 配合 cmd 解析期会导致乱码，提示文本一律使用英文。 |
| 7 | **带 percent-encoding（`%E6...`）的 URL 不可用 PowerShell 传递**，会被安全策略误判为 cmd 环境变量语法并拦截。改用 Bash，或直接传中文 URL。 |
| 8 | **登录须在独立 profile 内完成**，不可使用日常浏览器。判定标准：目标域名的 Cookie 中存在登录令牌（知乎为 `z_c0`；仅有 `_xsrf` / `d_c0` / `BEC` 表示未登录）。 |
| 9 | **渲染不等于万能**。知乎、贴吧等站点即使渲染仍被拦截，须先执行 `--login` 建立真实会话。 |
| 10 | **`fetch.py` 的定位是文章正文提取**。链接密集型页面与无语义标签的卡片页不在适用范围内，见 §5.2。 |

---

## 五、已知错误

### 5.1 抓取失败

| 现象 | 原因 | 处置 |
|---|---|---|
| 输出 `[非文本内容] content-type: application/pdf`，退出码 3，`-o` 不落盘 | `fetch.py` 不支持 PDF | 自行下载后用 pymupdf 抽取文字 |
| `UnicodeEncodeError: 'ascii' codec can't encode` | URL 含中文，`urllib.request` 无法编码 | 先用 `urlsplit` 拆分，对 `path` 执行 `quote(path, safe="/%")` 后拼回 |
| `TargetClosedError: ... launch_persistent_context ... has been closed` | 多进程并发 `--render` 抢占同一 profile | 渲染任务串行执行 |
| 静态 HTML 仅数 KB，所有链接为 `javascript:;` | 目标为 SPA，如方正系期刊站 | 使用 `--render --wait 8` |
| 正文仅数百字符且含「安全验证」/「Checking your browser」 | 命中 Cloudflare JS 挑战页 | 使用 `--render`，脚本会自动识别并转入等待；必要时加 `--wait` |
| 正文为「请稍候…」挑战页，页面含 `turnstile` 字样 | Cloudflare Turnstile 强反爬 | **无法绕过**。脚本等待超时后报错退出，不返回中间页。该站的 API 端点通常同样被拦截 |
| HTTP 403 等 4xx | 站点反爬，非浏览器请求被拒 | 知乎 / 贴吧类站点先 `--login`；渲染无效时改用其他工具 |
| 提示「反爬墙：Cloudflare / DataDome / AWS WAF」 | HTTP 响应头带厂商反爬标记（挑战页常以 **200** 下发，光看状态码看不出） | 改用 `--render`。⚠️ AWS WAF 拦的是数据中心 IP，渲染也无效 |
| 报错「页面未能打开（浏览器错误页）」 | DNS 失败 / 连接被拒 / 导航超时 | 检查网址与代理是否可达 |
| 报错「页面几乎是空的（疑似 SPA 壳）」 | 渲染完成但正文未挂载 | 加大 `--wait`，或用 `--interactive` 手动操作后抓取 |
| 正文为「你似乎来到了没有知识存在的荒原」 | 知乎内容不存在页（约等于 404），非拦截 | 更换有效链接后重试 |

### 5.2 提取结果不适用

| 场景 | 现象 | 处置 |
|---|---|---|
| 链接密集型页面（索引站、资源列表页） | 超长 URL 占满字符预算，提取结果无价值 | 改用 `--html` 自行解析 |
| 无语义标签的卡片页（Tailwind / React 生成） | 无 `<article>` / `<p>` 结构，引擎无法识别正文，静态抓取仅约 232 字符 | 先确认是否为 SSG（内容通常在原始 HTML 内）；若是则直接解析 HTML，**无需渲染** |

**诊断顺序（三步）**

1. 执行 `curl <url> -o page.html`，或 `fetch.py --html`，**不加** `--render`。
2. 以 `grep` 检索页面中已知的关键词。
3. 有内容 → 属提取问题，更换解析方式；无内容 → 才需 `--render`（CSR）或直接调用站点接口。

辅助工具：`tools\extract_cards.py`（仓库自带）。依赖 lxml，嵌入式 Python 已包含，无需额外安装。

```
python_embed\python.exe tools\extract_cards.py page.html [--class-keyword KW] [--section-tag TAG]
```

该脚本配套 `fetch.py` 使用：先以 `fetch.py <url> --render --wait 12 --html -o page.html` 取得渲染后的完整 DOM，再提取条目。

### 5.3 引擎可用性

> 2026-10-09 在 Windows 原生版实测（`OpenAI GPT-5` en 24 条 / `Python asyncio` cat=it 74 条）。

| 状态 | 引擎 |
|---|---|
| 稳定可用 | google cse、github、stackoverflow、superuser、askubuntu、mdn、docker hub、gentoo、wikipedia、arxiv、semantic scholar、pubmed、europepmc、google news、bing news、bing images、google videos、wikinews |
| 不稳定 | duckduckgo（时通时报「验证码」） |
| 不可用 | brave（HTTP 错误）、google scholar（403）、reuters（401） |

---

> 安装与部署详见 `INSTALL.md`；SearXNG 服务端的移植细节见 `searxng-windows\README.md`。
