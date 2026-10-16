# SearXNG for Windows —— 原生移植层

让**官方 SearXNG** 在 Windows 上直接跑起来 —— **不需要 WSL、不需要 Docker、不需要虚拟机**。

> 本目录**不是** SearXNG 源码，而是**适配层**：几个启动脚本 + 配置模板 + 说明。
> 你需要自己 clone 官方源码，然后把本目录的文件放进去。
> 完整步骤见仓库根目录的 **[`../INSTALL.md`](../INSTALL.md)**。

---

## 为什么需要"移植"

官方推荐的部署方式是 **uWSGI**，但它不支持 Windows（依赖 Unix 的 `fork` 等特性）。
另外官方源码里有一处 Linux 专属代码，在 Windows 上会**一加载就崩**。

总共只需要处理三件事：

| # | 问题 | 解决 |
|---|---|---|
| 1 | `searx/valkeydb.py` 顶部有 `import pwd` —— Linux 专属模块，Windows 没有，且写在文件最顶上无条件执行 → **一加载就崩，整个程序起不来** | 加 `try/except` 降级（见下） |
| 2 | uWSGI 不支持 Windows | 用 **waitress** 替代（纯 Python 实现的 WSGI 服务器，Windows 上最成熟） |
| 3 | `curl_cffi`（SearXNG 的 HTTP 客户端）在 Windows 默认走 `ProactorEventLoop`，而它不支持 `add_reader` → 部分引擎请求异常并刷警告 | 切到 `SelectorEventLoop`（`run.py` 里一行） |

---

## 唯一的源码改动

只改一个文件：`searx/valkeydb.py`。**两处**，都在异常日志分支里 —— 对功能零影响。

### 改动 1：顶部导入降级

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

### 改动 2：用到 `pwd` 的那行加平台判断

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

> 补充说明：**因为 `limiter` 关闭，SearXNG 根本不需要 Redis/Valkey**，
> 所以这段逻辑在本方案里永远不会被执行到 —— 改它只是为了让它**能被导入**。

---

## 本目录内容

| 文件 | 作用 |
|---|---|
| `run.py` | 启动入口：waitress 服务器 + 空闲自动退出看门狗 + Windows 事件循环修复 |
| `run.bat` | 双击启动（**前台运行，窗口即保活**，关窗口 = 停服务） |
| `stop.bat` / `stop.ps1` | 停止服务（按命令行匹配 `run.py` 进程） |
| `settings.yml.example` | 配置模板 —— **复制成 `settings.yml` 再改** |
| `limiter.toml` | 限流配置（本方案 `limiter: false`，实际不生效，但 SearXNG 启动会读它） |
| `requirements.txt` | 依赖清单：官方依赖 + `waitress` + `tzdata` |

---

## 两条启动路径，别搞混

| | 手动启动（人） | 自动拉起（脚本） |
|---|---|---|
| **怎么触发** | 双击 `run.bat` | 跑 `searx.py` 查询时服务没在跑，自动拉起 |
| **用什么解释器** | `.venv\Scripts\python.exe` | 同一个 `python.exe`，但加 `CREATE_NO_WINDOW` |
| **有没有窗口** | **有**，一个控制台窗口（故意留的，方便看日志） | **没有**：控制台存在，但是**隐藏的** |
| **窗口要关吗** | **不要关**，关了服务就停了 | 不存在窗口 |
| **日志去哪看** | 就在这个窗口里 | `searxng.log` |

> **为什么自动拉起不弹窗口？** 关键在 `CREATE_NO_WINDOW` —— 给服务分配一个**隐藏控制台**。
>
> 为什么非要「有控制台」？因为 **SearXNG 启动时会调用 `git` 读取版本号**
> （`searx/version.py` 里共 4 处：`git show` / `git diff` / `git rev-parse` / `git remote`）。
> `git` 是**控制台程序，会继承父进程的控制台**：
> - 父进程**有**（隐藏）控制台 → `git` 继承它，**永不弹窗** ✅
> - 父进程**没有**控制台 → `git` 只能各自新建一个**可见**控制台 → **黑框一闪而过** ❌
>
> ⚠️ 两条别走的回头路（都实测踩过）：
> - **`DETACHED_PROCESS`**：venv 的 `Scripts\python.exe` 只是「重定向器」(venvlauncher)，
>   创建标志**不传递**，真解释器照样新建可见控制台 → 常驻空白窗口。
> - **`pythonw.exe`**：GUI 子系统，服务**完全没有控制台**，于是 4 个 `git` 子进程
>   各建一个可见窗口 → 黑框一闪而过。
>
> `stop.ps1` 同时匹配 `python.exe` / `pythonw.exe`，两条路径的进程都能停。

---

## 空闲自动退出

服务**不需要常驻**：连续 **15 分钟**没有任何请求，它会自动退出，把内存还给系统。

判定依据是「**有没有请求**」，不是「启动后过了多久」—— 只要有请求就一直活着，
静默满 15 分钟才退。

| 想做什么 | 怎么做 |
|---|---|
| 改时长 | 启动前设环境变量 `SEARXNG_IDLE_TIMEOUT=<秒>` |
| 常驻不退出 | 设 `SEARXNG_IDLE_TIMEOUT=0` |

> 实现：`run.py` 里一个 WSGI 中间件记录「最后一次请求时间」，配一个后台看门狗线程，
> 每 5 秒检查一次，超时即 `os._exit(0)`。

---

## 怎么增删搜索引擎

两种方式：

1. **网页上改**：打开 http://127.0.0.1:8888 → 右上角 **首选项** → 引擎列表里勾选 / 取消
2. **配置文件改**：编辑 `settings.yml`，在 `engines:` 下加条目

> ⚠️ 注意：**网页上的勾选存在浏览器 cookie 里**，用脚本（`searx.py`）调用时不带 cookie，
> 所以想让脚本也生效，请改 `settings.yml`。

---

## 出问题怎么办

| 症状 | 排查 |
|---|---|
| 双击 `run.bat` 闪一下就没了 | 在命令行进这个目录，手动跑 `.venv\Scripts\python.exe run.py`，能看到具体报错 |
| 首页打不开 | 看启动窗口有没有报错；确认 8888 端口没被别的程序占用 |
| 搜不出结果 | 确认**代理软件开着**（`settings.yml` 里 `outgoing.proxies` 指向的端口）；看启动窗口有没有引擎报错 |
| 想换端口 | 改 `settings.yml` 里的 `server.port` |
| 跑 `searx.py` 后**弹出空白 python 窗口**（常驻不关） | 不该出现。说明拉起用了 `DETACHED_PROCESS`，检查 `searx.py` 里 `flags` 是否为 `CREATE_NO_WINDOW \| CREATE_NEW_PROCESS_GROUP`。窗口空白是因为 stdout 被重定向到 `searxng.log` |
| 跑 `searx.py` 时**黑框一闪而过**（可能好几个） | 是 `git.exe` 的窗口。说明服务**没有控制台**（误用了 `pythonw.exe`）→ 见上方「为什么自动拉起不弹窗口」 |

---

## 已知的无害提示

启动日志里可能出现这条，**不用管**：

```
ERROR:searx.botdetection: X-Forwarded-For nor X-Real-IP header is set!
```

这是 SearXNG 在检查「前面有没有反向代理」。本机直连当然没有，它**只提示一次**，不影响任何功能。

---

## 备份建议

`settings.yml` 是你的全部个性化配置。想调整前先复制一份，
万一改坏了直接覆盖回去就能恢复。
