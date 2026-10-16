# ☆*: .｡. o(≧▽≦)o .｡.:*☆

> 本仓库是自建的**本地搜索工具链**：不限次数的网页搜索 + 正文抓取，全部跑在自己机器上。

## 快速开始

> 🔧 **首次安装请看 [`INSTALL.md`](INSTALL.md)** —— 含 SearXNG 的 Windows 原生移植教程。
> 下面这几步假设你已经装好 SearXNG 服务端。

```bat
setup.bat                                   :: 首次准备（只做一次，生成 python_embed\）
searx.bat "关键词" --lang zh-CN -n 5         :: 1) 搜
fetch.bat "https://..." --max 4000          :: 2) 读正文
fetch.bat "https://..." --render            :: 需要 JS 渲染时走浏览器慢路径
```

> ⚠️ **`searx.bat` 建议显式传 `--lang`**，否则结果语言会跟随代理出口节点（出口在日本就会返回日文结果）。
> 📦 **SearXNG 服务端不随本仓库分发**，它是独立目录 `searxng-official`（与本仓库**同级**，**Windows 原生版**，自带 `.venv`）：双击其中的 `run.bat` 即可启动，**无需 WSL / Docker / 虚拟机**。
> ⚠️ **Python 环境就在本仓库**：`python_embed\`（由 `setup.bat` 首次生成，约 210MB，**不进 git**）。不需要系统 Python、不需要 venv。

---

**三层结构**

```
①  SearXNG（元搜索引擎，Windows 原生运行，独立目录 searxng-official）  ← 搜索层，不限次数、多引擎聚合
        ↑ HTTP :8888
②  searx.py（调用助手，服务没跑会以脱离进程方式自动拉起 ①）  ← 搜索入口
        ↓ 拿到 URL 列表
③  fetch.py（正文抓取器）                                 ← 读取入口
        ├─ HTTP 快路径：httpx 直连→代理 + 双引擎提正文（trafilatura / readability 自动选优）
        └─ 浏览器慢路径：--render（Playwright + 系统 Edge + 持久化 profile + 反检测）
```

## 文件说明

**入口脚本**

| 文件 | 说明 |
|---|---|
| `setup.bat` | **首次运行**。下载嵌入式 Python 到 `python_embed\` 并装齐依赖。幂等，可重复跑 |
| `searx.bat` / `fetch.bat` | 搜索 / 抓正文入口，`%~dp0` 相对调用仓库内的 Python |
| `config.bat.example` | 本机配置模板（SearXNG 目录 / 端点 / 代理）。复制成 `config.bat` 后自动生效，**真身不入库** |

**现役工具链**（搜索 + 抓正文）

| 文件 | 说明 |
|---|---|
| `searx.py` | 搜索入口。调用 Windows 原生 SearXNG（`searxng-official`），服务没跑会以脱离进程方式自动拉起 |
| `fetch.py` | 正文抓取器 **CLI 入口**（参数解析 + 流程编排）。HTTP 快路径 + 浏览器慢路径（`--render`） |
| `fetcher/` | `fetch.py` 的**内部实现模块**：`config`（配置）/ `net`（HTTP 取页）/ `challenge`（反爬判据）/ `browser`（Playwright）/ `extract`（双引擎提正文）/ `output`（输出组装）。**无独立入口**，只由 `fetch.py` 导入。其中 `anti_detect.js` 是**可自行编辑**的反检测注入脚本（改完下次 `--render` 生效） |
| `python_embed/` | 嵌入式 Python 3.13（`setup.bat` 生成，约 210MB）。**已 gitignore** |
| `browser_data/` | Playwright 持久化 profile（登录态 / cookie 在这）。**已 gitignore** |

**SearXNG 服务端**（独立目录，**不在本仓库**）

| 路径 | 说明 |
|---|---|
| `searxng-official\`（与本仓库同级） | **Windows 原生 SearXNG**：`run.bat` 启动 / `stop.bat` 停止 / `run.py` 入口（waitress）/ `settings.yml` 配置。自带 `.venv` |
| `searxng-official\README-Windows.md` | Windows 适配说明（含「空闲 15 分钟自动退出」） |

> 🗑️ **已移除**：`wsl/` 目录、`docs/searxng-deploy.md`、`.wslconfig.example` 是**旧 WSL2 部署方案**（2026-10-09 WSL 虚拟化失效后报废），已于 2026-10-13 从仓库删除。如需查阅，从 git 历史取回：`git show cf443ea --stat`。

**文档与配置**

| 文件 | 说明 |
|---|---|
| `README.md` | 本文件。整个「本地搜索」体系的总览入口 |
| `requirements.txt` | 工具链依赖，按 2026-09-28 实测版本冻结 |

## 参数

| 参数 | 作用 |
|---|---|
| `searx.py --timeout N` | 单次查询超时秒数（默认 60） |
| `searx.py --no-start` | 服务没跑时**不**自动拉起，直接报错（想手动管理服务时用） |
| `fetch.py --timeout N` | 抓取 / 渲染超时秒数（默认 30） |
| `fetch.py --proxy URL` / `--no-proxy` | 指定代理 / 强制直连 |

**环境变量覆盖** —— 下面这些常量都能用环境变量改，换机 / 换端口 / 换代理时**不必动源码**：

| 环境变量 | 默认值 | 影响 |
|---|---|---|
| `SEARXNG_URL` | `http://127.0.0.1:8888` | SearXNG 端点 |
| `SEARXNG_DIR` | `<仓库同级>\searxng-official` | Windows 原生版安装目录（自动拉起时用） |
| `SEARXNG_IDLE_TIMEOUT` | `900` | 服务端空闲自动退出秒数，`0` = 常驻 |
| `LOCAL_SEARCH_PROXY` | `http://127.0.0.1:7897` | Clash 代理 |
| `LOCAL_SEARCH_UA` / `LOCAL_SEARCH_EDGE_UA` | 见源码 | 请求 UA（Edge 那份含浏览器大版本号，会随 Edge 升级过时） |

---

## 关于浏览器渲染（`--render`）

`fetch.py --render` 走 **Playwright + 系统 Edge**（`fetcher/browser.py` 里 `channel="msedge"`）。

> ✅ **不需要** `playwright install chromium` —— 直接用你系统里已装的 Edge，**零浏览器下载**。

如果你想改用 Playwright 自带的 Chromium：

```bat
python_embed\python.exe -m playwright install chromium
```

然后把 `fetcher/browser.py` 里的 `channel="msedge"` 去掉即可。

---

## 许可证

[Apache License 2.0](LICENSE) © 2026 XOR-Y

