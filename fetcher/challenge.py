#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""反爬识别 —— 挑战页（等一等可能自己过）、WAF 拦截页（等多久都没用），
以及渲染后的「壳 / 错误页」后置校验。

设计约束：本模块**零依赖** —— 不 import browser / playwright，
只接收一个 duck-type 的 ``page`` 对象（需要 ``title()`` / ``evaluate()`` /
``wait_for_timeout()``）。这样判据可以脱离真实浏览器单独测试：
扩充厂商判据（DataDome / PerimeterX 等）时不必先起一个浏览器。

想加新厂商的判据，直接往下面几张表里加即可。
"""

# ── 验证页识别（Cloudflare JS 挑战 / Turnstile 等「可等待的中间页」）──
# 想加新厂商的判据，直接往下面两张表里加即可（本模块零依赖，加完可脱离浏览器单测）。
_CHALLENGE_TITLE_MARKERS = (
    "just a moment", "请稍候", "安全验证", "正在验证",
    "checking your browser",
    "verifying you are human", "verifying your browser",
    "robot or human?",                 # PerimeterX / HUMAN Security
)
_CHALLENGE_SELECTORS = (
    "#challenge-running", "#cf-challenge-running", "#challenge-stage",
    "iframe[src*='challenges.cloudflare.com']",
    "iframe[src*='turnstile']",
    "div[class*='cf-challenge']",
    "#turnstile-wrapper",
    # CF 的 Turnstile 控件会插一个隐藏 response input —— SteamDB 实测靠它
    # 才认得出是挑战页（那页的标题/其它 ID 都是自定义的，认不出来）
    "input[id*='cf-chl-widget']",
    # ── DataDome（G2 / Reddit / Walmart / StockX… 常把挑战页以 **HTTP 200** 下发，
    #    光看状态码看不出来）──
    "#cmsg", "[class*='dd-loader']",
    # ── PerimeterX / HUMAN Security ──
    "#px-captcha",
    # ⚠️ 判据要**厂商模板专属**，别拿「正常页也带」的东西当判据：
    #    例如 `window._px` 这类 sensor 脚本正常页面也加载，写进来会满世界误报。
)

# ── WAF 拦截页识别 ─────────────────────────────────────────────────
# 与「挑战页」的关键区别：挑战页**可能**自己过（等就有机会），
# 拦截页等多久都不会放行 → 必须立刻收工，别浪费用户时间。
#
# ⚠️ 判据要**够特异**：`access denied` 这种词在正常文章标题/正文里满地都是
#    （2026-09-29 实测：一篇《为什么我会遇到 Access Denied？》的排查文章
#     被误判成拦截页）。所以分两档：
#      · 子串匹配 —— 只放 CF 专属短语，出现即拦截页；
#      · 整条匹配 —— 通用短标题（Access Denied / 403 Forbidden）必须**标题就是它**。
_BLOCK_TITLE_SUBSTR = (
    "attention required",              # Cloudflare 拦截页标题（Access Denied / Error 1020）
    "you have been blocked",
)
_BLOCK_TITLE_EXACT = (
    "access denied", "403 forbidden", "forbidden", "blocked",
)
_BLOCK_SELECTORS = (
    # ⚠️ 只能用 #cf-error-details（ID，CF 错误页专属）。
    #    别用 div[class*='cf-error'] —— 2026-09-29 实测：CF **挑战页**里
    #    就有 <div class="muted-panel cf-error">，会把挑战页误判成拦截页，
    #    直接掐掉「--headed 手动过验证」的机会。
    "#cf-error-details",
)
# 正文话术同样只要 CF 专属的 —— 通用的 "access denied" 交给标题档去管
_BLOCK_TEXT_MARKERS = (
    "you have been blocked", "has been blocked",
    "error 1020", "error 1015", "error 1006",
)

# 拦截页 / 挑战超时的统一收工话术：**不返回任何内容**，让用户自己去浏览器看。
# 理由（用户 2026-09-29 定）：把 CF 的中间页/拦截页当成「正文」返回，
# 比直接失败更糟 —— 下游会拿一堆垃圾当真内容用。
_BLOCK_MSG = ("站点拒绝访问（WAF 拦截页，如 Cloudflare Access Denied / Error 1020）"
              " —— 等待不会放行，请自己用浏览器打开该网址查看")
_CHALLENGE_FAIL_TMPL = ("人机验证 / 挑战页等待 {sec}s 仍未通过"
                        "（强反爬，即使带 Cookie 也进不去）"
                        " —— 请自己用浏览器打开该网址查看")


def _looks_like_challenge(page) -> bool:
    """当前页面是不是「验证 / 挑战」中间页。"""
    try:
        title = (page.title() or "").strip().lower()
        if any(m in title for m in _CHALLENGE_TITLE_MARKERS):
            return True
        return bool(page.evaluate(
            "(sels) => sels.some(s => document.querySelector(s))",
            list(_CHALLENGE_SELECTORS),
        ))
    except Exception:
        return False


def _looks_like_block(page) -> bool:
    """WAF 的「拦截 / 拒绝」页 —— 等多久都没用，应立刻收工。

    判定顺序：标题特征 → CF 专属 DOM 选择器 → 「正文很短且含拒绝话术」。
    最后一条带**长度闸门**：WAF 拦截页几乎只有一行错误、正文极短；
    而正常帖子/文档里出现 "access denied" 时正文往往很长 —— 不该误杀。
    """
    try:
        title = (page.title() or "").strip().lower()
        if any(m in title for m in _BLOCK_TITLE_SUBSTR):
            return True
        if title in _BLOCK_TITLE_EXACT:
            return True
        if page.evaluate("(sels) => sels.some(s => document.querySelector(s))",
                         list(_BLOCK_SELECTORS)):
            return True
        text = page.evaluate(
            "() => (document.body ? document.body.innerText : '')"
            ".slice(0, 5000).toLowerCase()"
        )
        if len(text) > 2000:            # 正文够长 → 是真页面，不是 WAF 拦截页
            return False
        return any(m in text for m in _BLOCK_TEXT_MARKERS)
    except Exception:
        return False


def _wait_for_challenge(page, challenge_timeout=180):
    """挑战页专用等待：等页面**真正不再是挑战页**。返回 (是否通过, 已等待秒数)。

    ⚠️ 期间**绝不 close** —— 这正是原实现的病根：到点就抓、抓完就关，
       用户还没点完验证，窗口就已经没了。
    ⚠️ 判据只认「连续 2 秒不再是挑战页」，**不看 URL 变没变**：
       SteamDB 实测踩到过 —— CF 挑战过程中 URL 会带 token 变化
       （`?__cf_chl_rt_tk=…`），若把「URL 变了」当通过，会抓到 CF 的
       中间页/拦截页，还谎报「已通过」。谎报成功比直接失败更糟。
    """
    waited = 0
    clear = 0
    while waited < challenge_timeout:
        page.wait_for_timeout(1000)
        waited += 1
        try:
            if _looks_like_challenge(page):
                clear = 0
            else:
                clear += 1
                if clear >= 2:          # 连续 2 秒都不是挑战页 → 认定通过
                    return True, waited
        except Exception:
            return False, waited        # 窗口被关掉了
    return False, waited


# ── 渲染后置校验：区分「抓到正文」与「抓到壳」────────────────────────
# 与上面两张表是同一条原则的延伸：**不把壳当正文交给用户**。
#
# 2026-10-13 实测（这是设计依据，别凭直觉改）：
#   · 导航失败时浏览器会渲染**内置错误页**，它的 HTML 高达 **315 KB** ——
#     「内容短 = 错误页」这个直觉完全不成立，提取器照样能从里面刨出几百字
#     当「正文」交出去。干净可靠的判据是 URL 协议 `chrome-error://`
#     与 DOM 里的 `#main-frame-error`。
#   · 导航超时 / DNS 失败时页面可能停在 about:blank、content() 直接抛异常，
#     那种情况由调用方的异常处理兜住，不走这里。
_EMPTY_TEXT_MIN = 80        # body 可见文本少于这么多字符 → 视为空壳


def _post_check(page):
    """渲染后的后置校验。返回 None = 通过；否则返回收工说明。

    调用方拿到说明后应当**直接收工、不交付内容** —— 理由同 `_BLOCK_MSG`：
    把壳当正文交出去，比直接失败更糟（下游会拿垃圾当真内容用）。
    """
    try:
        if (page.url or "").startswith("chrome-error://") or page.evaluate(
                "() => !!document.querySelector('#main-frame-error')"):
            return ("页面未能打开（浏览器错误页：DNS 失败 / 连接被拒 / 导航超时）"
                    " —— 请检查网址与代理是否可达")
        text = (page.evaluate(
            "() => (document.body ? document.body.innerText : '')") or "").strip()
        if len(text) < _EMPTY_TEXT_MIN:
            return (f"页面几乎是空的（可见文本仅 {len(text)} 字符，疑似 SPA 壳 / "
                    f"正文未渲染）—— 可加大 --wait，或用 --interactive 手动操作后抓取")
    except Exception:
        return None             # 校验自身出错不阻断：宁可放过，不可误杀
    return None
