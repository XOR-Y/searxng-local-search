#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网页正文抓取器 —— 搜索结果的"下一步"

把网页抓下来并**提取正文**（去掉导航/广告/页脚），默认输出 Markdown。

用法:
    python fetch.py <url>                     # 正文 → Markdown
    python fetch.py <url> --text              # 正文 → 纯文本
    python fetch.py <url> --html              # 原始 HTML
    python fetch.py <url> --meta              # 只要元信息（标题/作者/日期/摘要）
    python fetch.py <url> -o out.md           # 存文件
    python fetch.py <url> --no-proxy          # 强制直连
    python fetch.py <url> --proxy http://...  # 指定代理
    python fetch.py <url> --max 20000         # 截断字符数
    python fetch.py <url> --timeout 60        # 抓取/渲染超时秒数（默认 30）
    python fetch.py <url> --engine readability # 指定提取引擎

    # ── 浏览器渲染（要浏览器才看得到的页面）──
    python fetch.py <url> --render            # 用真实浏览器渲染后再提取
    python fetch.py <url> --render --headed   # 有头模式（能看见窗口，可手动过人机验证）
    python fetch.py --login                   # 打开浏览器手动登录，登录态存进 profile
    python fetch.py <url> --render --wait 12  # 慢站：把渲染等待的预算上限提到 12 秒
    python fetch.py <url> --render --interactive   # 等你在窗口里操作完、按回车再抓

默认策略: 先直连（国内站快），失败自动改走 Clash 代理（境外站）—— **HTTP 与 --render 一致**。
          连接类失败才重试代理；若已收到 HTTP 响应，只有 403/429 才换代理重试，
          其余 4xx/5xx 直接以真实状态码报错（不白跑代理、不掩盖真实状态）。
          --login / --interactive 这类要人工操作的场景：先探测 Clash 端口，可达就直接用它
          （多半是境外站），不可达则直连 —— 避免 Clash 没开时把本来能用的直连也拖死。
配置: 以下常量可用**环境变量覆盖**，换机 / 换代理时不必改源码 ——
      LOCAL_SEARCH_PROXY（默认 http://127.0.0.1:7897）
      LOCAL_SEARCH_UA / LOCAL_SEARCH_EDGE_UA（后者含浏览器大版本号，会随 Edge 升级过时）
提取引擎: 默认 auto —— trafilatura 与 readability 都跑，按「长行占比」自动选优。
          （readability 在结构化/wiki 页面上明显更强，见 SKILL.md）
渲染模式: 走 Playwright + 系统 Edge，**持久化 profile**（登录态/cookie 跨次保留）+
          反检测注入 + 自动滚动。解决反爬 / JS 渲染 / 需登录三类页面。
          渲染后**等页面稳定**再抓（水合探针 / 稳定性轮询 / 预算上限三路竞速），
          不再固定等死时间；算正文时会排除导航栏与侧栏，避免被侧栏文字骗到。
          ⚠️ 遇到 Cloudflare 等「人机验证中间页」会自动转为「等页面真正变掉」
             （最长 --challenge-timeout 秒；默认有头 180 / 无头 20）；配 --headed
             可在窗口里手动点完验证，脚本会接住通过后的页面，而不是到点就关窗。
          ⚠️ 但**真进不去就立刻收工**：命中 WAF 拦截页（Access Denied / Error 1020）
             不等；挑战页等到超时仍未通过也不返回 —— 两种情况都直接报错退出，
             **不把 CF 中间页当正文交给你**。请自己用浏览器打开该网址查看。

本文件是 CLI 入口；具体实现拆在 fetcher/ 包里（config / net / challenge /
browser / extract / output），此处只做参数解析与流程编排。
"""
import argparse
import os
import sys

# ⚠️ 必须放在 `from fetcher import ...` 之前。
# 嵌入式 Python（python_embed/）带 python313._pth，处于**隔离模式**：
# sys.path 完全由该文件决定，**不会**自动把「脚本所在目录」加进来 ——
# 于是 `import fetcher` 会直接 ModuleNotFoundError。这里显式补上仓库根。
# 用系统 Python 跑时脚本目录本来就在 sys.path 里，所以这句是幂等的无害操作。
_BASE = os.path.dirname(os.path.abspath(__file__))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from fetcher import output
from fetcher.browser import do_login, render_page
from fetcher.config import PROXY
from fetcher.net import _interactive_proxy, _proxy_alive, fetch


def _parse_args():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("url", nargs="?", help="目标网址（--login 时可省略）")
    ap.add_argument("--text", action="store_true", help="输出纯文本（默认 Markdown）")
    ap.add_argument("--html", action="store_true", help="输出原始 HTML")
    ap.add_argument("--meta", action="store_true", help="只输出元信息 JSON")
    ap.add_argument("--no-proxy", action="store_true",
                    help="强制直连（HTTP / --render / --login 通用）")
    ap.add_argument("--proxy", help="指定代理地址（HTTP / --render / --login 通用）")
    ap.add_argument("--timeout", type=float, default=30,
                    help="抓取/渲染超时秒数（默认 30）。⚠️ 渲染模式下它只作用于页面导航"
                         "（goto），--wait 与自动滚动的耗时另计")
    ap.add_argument("--max", type=int, default=0, help="截断字符数，0=不截断")
    ap.add_argument("--engine", choices=["auto", "trafilatura", "readability"],
                    default="auto", help="提取引擎，默认 auto（两者都跑，选优）")
    ap.add_argument("--render", action="store_true",
                    help="用真实浏览器渲染（解决反爬/JS渲染/需登录）")
    ap.add_argument("--headed", action="store_true", help="配合 --render：显示浏览器窗口")
    ap.add_argument("--wait", type=float, default=None,
                    help="配合 --render：渲染等待的**预算上限**秒数（默认 6）。"
                         "脚本会自己判断页面何时稳定（水合探针 + 稳定性轮询），"
                         "通常用不满；慢站可加大")
    ap.add_argument("--interactive", action="store_true",
                    help="配合 --render：有头打开，等你在窗口里操作完、回终端按回车后再抓取")
    ap.add_argument("--challenge-timeout", type=int, default=None,
                    help="渲染模式遇到验证/挑战页的最长等待秒数"
                         "（默认：有头 180 / 无头 20 —— 无头看不到窗口、"
                         "没法手动过验证，等太久没意义）")
    ap.add_argument("--no-scroll", action="store_true", help="渲染时不自动滚动")
    ap.add_argument("--login", action="store_true",
                    help="打开浏览器手动登录，登录态存进 profile（url 可选）")
    ap.add_argument("-o", "--out", help="输出到文件")
    a = ap.parse_args()

    # --login 时 url 可省略，其余情况必须给
    if not a.login and not a.url:
        ap.error("缺少 url（或用 --login 打开浏览器登录）")
    return a


def main():
    a = _parse_args()

    if a.login:
        return do_login(a.url or "about:blank",
                        proxy=_interactive_proxy(a.no_proxy, a.proxy))

    # ── 渲染类参数只在 --render 下生效：单独出现时**明确报错**，不静默忽略 ──
    # （旧行为是直接无视 —— 敲了 `--headed` 却忘了 `--render`，什么都不会发生）
    if not a.render:
        stray = []
        if a.headed:                 stray.append("--headed")
        if a.interactive:            stray.append("--interactive")
        if a.no_scroll:              stray.append("--no-scroll")
        if a.wait is not None:       stray.append("--wait")
        if a.challenge_timeout is not None: stray.append("--challenge-timeout")
        if stray:
            print(f"[参数错误] {'、'.join(stray)} 只在 --render 模式下生效，"
                  f"当前没加 --render，这些参数会被静默忽略。\n"
                  f"           要用浏览器渲染请补上 --render；不要的话请去掉它们。",
                  file=sys.stderr)
            return 2

    proxy = False if a.no_proxy else (a.proxy or None)
    if a.render:
        # 与 HTTP 路径同一套策略：先直连，失败再换 Clash 代理。
        # ⚠️ 旧实现把 a.proxy（默认 None）直接传进去 —— 等于渲染**永远直连**，
        #    需要代理的境外站必挂，且 --no-proxy 形同虚设。
        if a.no_proxy:
            attempts = [None]
        elif a.proxy:
            attempts = [a.proxy]
        else:
            attempts = [None, PROXY]
        if a.interactive:
            # 交互模式要弹窗给人操作，失败重试会连弹两个窗口 → 只试一次；
            # 未显式指定代理时，Clash 可达就直接用它（要人工介入的多是境外站）
            attempts = ([PROXY] if len(attempts) > 1 and _proxy_alive(PROXY)
                        else attempts[:1])

        r = err = None
        for i, px in enumerate(attempts):
            r, err = render_page(a.url, headed=a.headed, wait=a.wait,
                                 scroll=not a.no_scroll,
                                 proxy=px, timeout=a.timeout,
                                 interactive=a.interactive,
                                 challenge_timeout=a.challenge_timeout)
            if r is not None:
                break
            if i < len(attempts) - 1:
                nxt = attempts[i + 1]
                print(f"[渲染] {err} —— 换 {'代理 ' + nxt if nxt else '直连'} 重试…",
                      file=sys.stderr)
        how = "浏览器渲染(Edge)"
    else:
        r, how, err = fetch(a.url, proxy=proxy, timeout=a.timeout)
    if r is None:
        print(f"[抓取失败] {err}", file=sys.stderr)
        return 2

    if getattr(r, "goto_error", None):
        print(f"[渲染警告] 页面导航未正常完成（超时/中断），内容可能不完整: "
              f"{r.goto_error}", file=sys.stderr)

    html = r.text
    ctype = r.headers.get("content-type", "")
    if "html" not in ctype and "xml" not in ctype and "text" not in ctype:
        print(f"[非文本内容] content-type: {ctype}  ({len(html)} 字符)", file=sys.stderr)
        return 3

    out = output.produce(html, r.url, r.status_code, how,
                         text_mode=a.text, html_mode=a.html, meta_mode=a.meta,
                         engine_pref=a.engine, max_chars=a.max)
    return output.emit(out, a.out, how)


if __name__ == "__main__":
    sys.exit(main())
