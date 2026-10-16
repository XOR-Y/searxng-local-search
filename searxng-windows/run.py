# -*- coding: utf-8 -*-
"""SearXNG for Windows —— 启动入口

官方推荐的部署方式是 uWSGI，但 uWSGI 不支持 Windows（依赖 Unix 的 fork 等特性）。
这里用 waitress 替代：纯 Python 实现，Windows 上最成熟的 WSGI 服务器。

启动：双击 run.bat，或直接 python run.py

空闲自动退出（等价于原 WSL 的「空闲约 15 分钟回收资源」）：
    内置看门狗 —— 距离**最后一次 HTTP 请求**超过 SEARXNG_IDLE_TIMEOUT 秒
    （默认 900 = 15 分钟）就自动退出，把资源还回系统。
    判定依据是「有没有请求」，不是「启动后过了多久」：只要有请求就一直活着，
    静默满 15 分钟才退；下次调用 searx.py 会自动重新拉起。
    设 SEARXNG_IDLE_TIMEOUT=0 可关闭该行为（常驻）。
"""
import asyncio
import os
import sys
import threading
import time

# Windows 专属修复：curl_cffi（SearXNG 的 HTTP 客户端）默认走 ProactorEventLoop，
# 而它不支持 add_reader，会导致部分引擎请求异常并刷 CurlCffiWarning。
# 换成 SelectorEventLoop 才是 Windows 上的正确姿势。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

HERE = os.path.dirname(os.path.abspath(__file__))

# 把配置路径和源码路径固定到本目录，避免依赖系统环境变量
os.environ.setdefault("SEARXNG_SETTINGS_PATH", os.path.join(HERE, "settings.yml"))
sys.path.insert(0, HERE)
# 切到本目录：SearXNG 启动时会用 git 读取版本号，不在仓库根目录会刷两条无害报错
os.chdir(HERE)

from waitress import serve        # noqa: E402
from searx.webapp import app      # noqa: E402  (import 时即完成 init() 初始化)
from searx import get_setting     # noqa: E402

host = get_setting("server.bind_address")
port = int(get_setting("server.port"))

# ── 空闲看门狗 ────────────────────────────────────────────────────────
IDLE_TIMEOUT = int(os.environ.get("SEARXNG_IDLE_TIMEOUT", "900"))  # 秒；0 = 常驻
_last_activity = time.time()
_activity_lock = threading.Lock()


class _ActivityTracker:
    """WSGI 中间件：每个请求进来就刷新「最后活动时间」。"""

    def __init__(self, wsgi_app):
        self._app = wsgi_app

    def __call__(self, environ, start_response):
        global _last_activity
        with _activity_lock:
            _last_activity = time.time()
        return self._app(environ, start_response)


def _watchdog():
    while True:
        time.sleep(5)
        with _activity_lock:
            idle = time.time() - _last_activity
        if idle >= IDLE_TIMEOUT:
            print(f"\n  [idle] 已空闲 {idle:.0f}s（阈值 {IDLE_TIMEOUT}s），自动退出。\n")
            try:
                sys.stdout.flush()
            except Exception:
                pass
            os._exit(0)


print()
print("  ============================================")
print("   SearXNG 已启动")
print(f"   访问地址:  http://{host}:{port}")
if IDLE_TIMEOUT > 0:
    print(f"   空闲退出:  无请求满 {IDLE_TIMEOUT}s 后自动结束")
else:
    print("   空闲退出:  已关闭（常驻）")
print("   停止:      按 Ctrl+C，或运行 stop.bat")
print("  ============================================")
print()
sys.stdout.flush()  # stdout 重定向到文件时是块缓冲，手动刷一下让横幅及时落盘

if IDLE_TIMEOUT > 0:
    threading.Thread(target=_watchdog, daemon=True).start()

serve(_ActivityTracker(app), host=host, port=port, threads=8, channel_timeout=60)
