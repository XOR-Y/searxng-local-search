#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
本地 SearXNG 查询助手

用法:
    python searx.py "关键词"                       # 基本查询
    python searx.py "关键词" --lang en             # 指定语言
    python searx.py "关键词" --cat it              # 分类: general/it/science/news/images/files
    python searx.py "关键词" --range week          # 时效: day/week/month/year
    python searx.py "关键词" -n 20                 # 返回条数
    python searx.py "关键词" --json                # 输出原始 JSON
    python searx.py "关键词" --timeout 120         # 单次查询超时秒数（默认 60）
    python searx.py "关键词" --no-start            # 服务没跑时不自动拉起，直接报错
    python searx.py --status                       # 只看服务状态

配置: 以下项可用**环境变量覆盖**，换机 / 换端口时不必改源码 ——
      SEARXNG_URL（默认 http://127.0.0.1:8888）
      SEARXNG_DIR（默认 = 本仓库「同级目录」下的 searxng-official）

特点: 服务没跑会自动拉起来（Windows 原生 SearXNG），不用手动开窗口。
      服务端内置空闲看门狗：无请求满 15 分钟会自动退出（见 run.py）。
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ── 可配置项（环境变量可覆盖，默认值 = 当前实测可用值）──────────────
BASE = os.environ.get("SEARXNG_URL", "http://127.0.0.1:8888").rstrip("/")
# Windows 原生版 SearXNG 的安装目录（原 WSL 版已随虚拟化失效而废弃）。
# 默认 = 本仓库「同级目录」下的 searxng-official，可用 SEARXNG_DIR 覆盖。
_REPO_DIR = os.path.dirname(os.path.abspath(__file__))
SEARXNG_DIR = os.environ.get(
    "SEARXNG_DIR",
    os.path.join(os.path.dirname(_REPO_DIR), "searxng-official"),
)
SEARXNG_PY = os.path.join(SEARXNG_DIR, ".venv", "Scripts", "python.exe")
SEARXNG_ENTRY = os.path.join(SEARXNG_DIR, "run.py")

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 本地服务绕过任何代理


def start_service():
    """拉起 Windows 原生版 SearXNG（后台、脱离本进程）。返回 (成功?, 说明)。

    只负责把进程拉起来，不等待就绪 —— 就绪与否由 ensure_up() 轮询端口判定。
    """
    # 用 python.exe + CREATE_NO_WINDOW（见下方 flags 注释）——
    # ⚠️ 不要改用 pythonw.exe：它没有控制台，服务启动时调用的 git 子进程
    #    会各自新建一个可见控制台，黑框一闪而过（2026-10-09 实测 4 个）。
    launcher = SEARXNG_PY
    if not os.path.exists(launcher):
        return False, f"未找到 Windows 版解释器: {SEARXNG_PY}"
    if not os.path.exists(SEARXNG_ENTRY):
        return False, f"未找到启动脚本: {SEARXNG_ENTRY}"
    try:
        log_path = os.path.join(SEARXNG_DIR, "searxng.log")
        logf = open(log_path, "a", encoding="utf-8")
        flags = 0
        if os.name == "nt":
            # CREATE_NO_WINDOW：给服务分配一个**隐藏的控制台**（有控制台，但不显示窗口）。
            #
            # 关键：SearXNG 启动时会用 `git` 读取版本号（`searx/version.py` 里共 4 处：
            # git show / git diff / git rev-parse / git remote）。git 是控制台程序，
            # **会继承父进程的控制台** —— 父进程只要有隐藏控制台可继承，它就永不弹窗。
            #
            # ⚠️ 两条反面教材（都实测踩过，别再走回头路）：
            #   · DETACHED_PROCESS：venv 的 Scripts\python.exe 只是「重定向器」
            #     (venvlauncher)，创建标志**不传递**，真解释器（venv 基础环境里的
            #     python.exe）照样新建可见控制台 → 常驻空白窗口。
            #   · pythonw.exe：GUI 子系统，服务**根本没有控制台**，于是 4 个 git
            #     子进程只能各自新建一个可见控制台 → 黑框「一闪而过」。
            # 另：脱离父进程靠 CREATE_NEW_PROCESS_GROUP + 父进程退出不杀子进程；
            #     空闲自动退出由 run.py 内置看门狗负责。
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            [launcher, SEARXNG_ENTRY],
            cwd=SEARXNG_DIR,
            stdin=subprocess.DEVNULL,
            stdout=logf,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            close_fds=True,
        )
        return True, f"已拉起（日志: {log_path}）"
    except Exception as e:
        return False, f"拉起失败: {type(e).__name__}: {e}"


def is_up():
    """HTTP < 400 即视为在跑 —— 与 start-searxng.sh 里 `curl -f` 的判定标准保持一致。

    ⚠️ 必须显式接住 `HTTPError`：`urllib` 的 `open()` 对 **4xx / 5xx 会抛异常**，
    而不是返回 response 对象 —— 不接的话 `r.status < 400` 只在 2xx 时才执行得到，
    等于「仅 2xx 算 up」，注释里写的语义根本没实现出来。
    （带 Location 的 3xx 会被 HTTPRedirectHandler 自动跟随、通常落到 2xx；
      无 Location 的 3xx 才走这里的 HTTPError 分支 —— 2026-09-29 实测确认。）
    """
    try:
        with _opener.open(BASE + "/", timeout=3) as r:
            return r.status < 400
    except urllib.error.HTTPError as e:
        return e.code < 400
    except Exception:
        return False


def ensure_up():
    """确保 SearXNG 在跑；没跑就拉起（Windows 原生版）。返回 (ok, msg)"""
    if is_up():
        return True, "已在运行"
    ok, msg = start_service()
    if not ok:
        return False, msg
    for _ in range(45):
        if is_up():
            return True, "已自动启动"
        time.sleep(1)
    tail = ""
    try:
        with open(os.path.join(SEARXNG_DIR, "searxng.log"), encoding="utf-8", errors="replace") as f:
            tail = "".join(f.readlines()[-15:]).strip()
    except Exception:
        pass
    return False, "启动超时（45s），日志: " + (tail or "(空)")


def search(q, lang=None, cat=None, time_range=None, page=1, timeout=60):
    params = {"q": q, "format": "json"}
    if lang:
        params["language"] = lang
    if cat:
        params["categories"] = cat
    if time_range:
        params["time_range"] = time_range
    if page and page > 1:
        params["pageno"] = page
    url = BASE + "/search?" + urllib.parse.urlencode(params)
    with _opener.open(url, timeout=timeout) as r:
        return json.load(r)


def main():
    # Windows 控制台默认 GBK，搜索结果里的非 GBK 字符会 UnicodeEncodeError —— 与 fetch.py 一致
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("query", nargs="?", help="搜索关键词")
    ap.add_argument("--lang", help="语言，如 en / zh-CN")
    ap.add_argument("--cat", help="分类: general/it/science/news/images/files")
    ap.add_argument("--range", dest="trange", help="时效: day/week/month/year")
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("-n", "--num", type=int, default=10, help="显示条数，默认 10")
    ap.add_argument("--json", action="store_true", help="输出原始 JSON")
    ap.add_argument("--status", action="store_true", help="只看服务状态")
    ap.add_argument("--timeout", type=float, default=60, help="单次查询超时秒数（默认 60）")
    ap.add_argument("--no-start", action="store_true",
                    help="服务没跑时不自动拉起，直接报错")
    a = ap.parse_args()

    if a.status:
        print("SearXNG:", "运行中" if is_up() else "未运行")
        return 0
    if not a.query:
        ap.print_help()
        return 1

    if a.no_start:
        if not is_up():
            print(f"[错误] SearXNG 未运行，且已指定 --no-start: {BASE}", file=sys.stderr)
            return 2
        ok, msg = True, "已在运行"
    else:
        ok, msg = ensure_up()
    if not ok:
        print(f"[错误] {msg}", file=sys.stderr)
        return 2

    try:
        d = search(a.query, a.lang, a.cat, a.trange, a.page, timeout=a.timeout)
    except Exception as e:
        print(f"[查询失败] {e}", file=sys.stderr)
        return 3

    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return 0

    if not isinstance(d, dict):
        print(f"[查询失败] 服务返回的 JSON 顶层不是对象: {type(d).__name__}", file=sys.stderr)
        return 3
    rs = d.get("results", [])
    print(f"查询: {d.get('query')}")
    print(f"结果: {len(rs)} 条" + (f"  (服务{msg})" if msg != "已在运行" else ""))
    bad = d.get("unresponsive_engines") or []
    if bad:
        # 每项形如 [引擎名, 原因]，但元素个数/类型不保证 —— 逐项防御，避免 IndexError
        items = []
        for x in bad:
            if isinstance(x, (list, tuple)):
                if len(x) >= 2:
                    items.append(f"{x[0]}({x[1]})")
                elif x:
                    items.append(str(x[0]))
                else:
                    items.append("(空项)")
            else:
                items.append(str(x))
        print("异常引擎: " + ", ".join(items))
    print("-" * 72)
    for i, r in enumerate(rs[: a.num], 1):
        print(f"{i:>2}. {r.get('title', '')}")
        print(f"    {r.get('url', '')}")
        print(f"    [{r.get('engine', '?')}] {(r.get('content') or '')[:150]}")
        print()
    if d.get("answers"):
        print("直接答案:", d["answers"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
