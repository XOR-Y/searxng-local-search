# 安装指南

本工具链分**两层**，装完之后目录结构长这样：

```
D:\tools\
├─ searxng-official\           ← ① 搜索层：SearXNG 服务端（独立目录，不随本仓库分发）
└─ searxng-local-search\       ← ② 本仓库：搜索 / 抓取脚本
```

> ⚠️ 两个目录必须是**同级**关系 —— `searx.py` 默认会去「本仓库的同级目录」找 `searxng-official`。
> 想放别处也行，改 `config.bat` 里的 `SEARXNG_DIR` 即可（见 [第三步](#第三步配置可选)）。

---

## 前置条件

| 需要 | 说明 |
|---|---|
| **Windows 10 / 11** | 本工具链只支持 Windows（SearXNG 用的是 Windows 原生移植方案） |
| **系统 Python 3.11+** | **只有 SearXNG 需要**（用它建 venv）。本工具链自己有嵌入式 Python，不依赖系统 Python |
| **代理软件**（强烈建议） | 不配的话 Google / Bing / DuckDuckGo 这类境外引擎会全部超时 |
| **Git** | 用来 clone SearXNG 源码 |

---

## 第一步：安装 SearXNG（Windows 原生移植）

这是整个安装里最麻烦的一步，但**只需要做一次**。

### 1.1 为什么需要"移植"

SearXNG 官方推荐的部署方式是 **uWSGI**，而 uWSGI **不支持 Windows**（依赖 Unix 的 `fork` 等特性）。
另外官方源码里有一处 Linux 专属代码，在 Windows 上会**一加载就崩**。

总共只需要处理三件事：

| # | 问题 | 解决 |
|---|---|---|
| 1 | `searx/valkeydb.py` 顶部有 `import pwd` —— Linux 专属模块，Windows 没有，且写在文件最顶上无条件执行 → **一加载就崩，整个程序起不来** | 加 `try/except` 降级 |
| 2 | uWSGI 不支持 Windows | 用 **waitress** 替代（纯 Python 的 WSGI 服务器） |
| 3 | `curl_cffi` 在 Windows 默认走 `ProactorEventLoop`，它不支持 `add_reader` → 部分引擎请求异常并刷警告 | 切到 `SelectorEventLoop`（`run.py` 里一行） |

后两项**已经由本仓库 `searxng-windows\` 里的文件解决**，你只需要改一处源码（1.4 节）。

### 1.2 获取源码

```bat
cd /d D:\tools
git clone --depth 1 https://github.com/searxng/searxng.git searxng-official
```

> 国内网络可能需要走代理：`git clone` 前先设 `set HTTPS_PROXY=http://127.0.0.1:7897`。

### 1.3 把适配文件复制进去

本仓库 `searxng-windows\` 目录里有 6 个文件，全部复制到 `searxng-official\` 下：

```bat
cd /d D:\tools
copy /Y searxng-local-search\searxng-windows\run.py                searxng-official\
copy /Y searxng-local-search\searxng-windows\run.bat               searxng-official\
copy /Y searxng-local-search\searxng-windows\stop.bat              searxng-official\
copy /Y searxng-local-search\searxng-windows\stop.ps1              searxng-official\
copy /Y searxng-local-search\searxng-windows\limiter.toml          searxng-official\
copy /Y searxng-local-search\searxng-windows\settings.yml.example  searxng-official\
```

各文件的作用见 [`searxng-windows/README.md`](searxng-windows/README.md)。

### 1.4 改一处源码（唯一的源码改动）

打开 `searxng-official\searx\valkeydb.py`，改**两处**：

**① 文件顶部的 `import pwd`（约第 22 行）**

```diff
 import os
 import logging
 import warnings
 
-import pwd
+try:
+    import pwd
+except ImportError:
+    # Windows 没有 pwd 模块。原版这里只在「连不上 valkey 时打印当前用户名」用到，
+    # 属于异常分支的日志装饰，不影响任何实际功能。
+    pwd = None
```

**② 下面用到 `pwd.getpwuid()` 的那处（约第 63 行）**

```diff
     except valkey.exceptions.ValkeyError:
         _CLIENT = None
-        _pw = pwd.getpwuid(os.getuid())
-        logger.exception("[%s (%s)] can't connect valkey DB ...", _pw.pw_name, _pw.pw_uid)
+        if pwd is None:  # Windows: 拿不到 getpwuid，降级成普通日志
+            logger.exception("can't connect valkey DB ...")
+        else:
+            _pw = pwd.getpwuid(os.getuid())
+            logger.exception("[%s (%s)] can't connect valkey DB ...", _pw.pw_name, _pw.pw_uid)
     return False
```

> 💡 因为本方案 `limiter: false`，**SearXNG 根本不需要 Redis/Valkey**，
> 所以这段逻辑永远不会被执行到 —— 改它只是为了让它**能被导入**。

### 1.5 建虚拟环境 + 装依赖

```bat
cd /d D:\tools\searxng-official
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r D:\tools\searxng-local-search\searxng-windows\requirements.txt
```

> 这份依赖清单 = 官方 `requirements.txt` **+ 两个 Windows 必需的包**：
> - `waitress` —— 替代 uWSGI
> - `tzdata` —— Windows 没有系统时区数据库（Linux 有 `/usr/share/zoneinfo`），
>   而 `bilibili` 等引擎要用 `ZoneInfo("Asia/Shanghai")`，缺了会报 `ZoneInfoNotFoundError`

### 1.6 生成配置文件

```bat
cd /d D:\tools\searxng-official
copy settings.yml.example settings.yml
```

然后编辑 `settings.yml`，**至少改这两个值**：

| 配置项 | 怎么改 |
|---|---|
| `server.secret_key` | **必须改**，否则 SearXNG 拒绝启动。随便生成一串随机字符：<br>`python -c "import secrets; print(secrets.token_hex(32))"` |
| `outgoing.proxies` | 改成你自己的代理地址。`7897` 是 Clash Verge 的默认端口（Clash for Windows 常见 `7890`，v2rayN 常见 `10809`） |

### 1.7 启动

双击 `searxng-official\run.bat`，然后浏览器打开 **http://127.0.0.1:8888**。

> 窗口**不要关** —— 关了服务就停了。停止请双击 `stop.bat`。
> 服务有空闲自动退出机制：**连续 15 分钟无请求**会自己退出，下次调用会自动拉起，不用管。

---

## 第二步：安装本工具链

```bat
cd /d D:\tools\searxng-local-search
setup.bat
```

这一步会：下载嵌入式 Python 到 `python_embed\`（约 210 MB）→ 装齐依赖。
**幂等，可以重复跑。** 全程不碰你的系统 Python。

装完试一下：

```bat
searx.bat "test" --lang en -n 3
fetch.bat "https://example.com" --max 500
```

---

## 第三步：配置（可选）

绝大多数情况下**不用配**。只有当你的环境和默认值不一样时，才需要：

```bat
cd /d D:\tools\searxng-local-search
copy config.bat.example config.bat
```

然后编辑 `config.bat`：

| 变量 | 默认值 | 什么时候要改 |
|---|---|---|
| `SEARXNG_DIR` | 本仓库同级目录的 `searxng-official` | SearXNG 装在别处时 |
| `SEARXNG_URL` | `http://127.0.0.1:8888` | 换了 SearXNG 端口时 |
| `SEARXNG_IDLE_TIMEOUT` | `900`（15 分钟） | 想让服务常驻 → 设 `0` |
| `LOCAL_SEARCH_PROXY` | `http://127.0.0.1:7897` | 抓正文时用的代理，改成你自己的 |

> `config.bat` 不会被提交到 git（已在 `.gitignore` 里），你的本机设置不会外泄。

---

## 第四步：验证

```bat
cd /d D:\tools\searxng-local-search

searx.bat --status                    :: 看 SearXNG 服务状态
searx.bat "hello world" --lang en -n 5 :: 搜 5 条
fetch.bat "https://example.com"       :: 抓正文
fetch.bat "https://example.com" --render  :: 需要 JS 渲染时
```

正常的话，第一次跑 `searx.bat` 会自动把 SearXNG 拉起来（无窗口、无弹框）。

---

## 常见问题

| 症状 | 排查 |
|---|---|
| `searx.bat` 报「无法连接」 | 先跑 `searx.bat --status`；再确认代理软件开着 |
| 搜索能出结果但全是超时 | 代理没配好 —— 检查 `settings.yml` 的 `outgoing.proxies` |
| 抓正文失败 / 卡住 | 目标站点可能需要代理；试 `fetch.bat "URL" --proxy http://127.0.0.1:7897` |
| `--render` 报浏览器找不到 | 确认系统装了 **Edge**（本项目用系统 Edge，不需要下载 Chromium） |
| SearXNG 启动报 `KeyError: 'brave'` | 你删引擎时只删了主引擎。`brave.images/videos/news` 引用主引擎，必须**整族一起删**（`settings.yml` 里已经删好了） |
| 启动日志有 `X-Forwarded-For nor X-Real-IP header is set!` | **无害**，只提示一次，本机直连本来就没有反向代理 |

更细的排查见 [`searxng-windows/README.md`](searxng-windows/README.md) 和根目录 `README.md`。
