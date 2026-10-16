#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""浏览器慢路径 —— Playwright + 系统 Edge（持久化 profile + 反检测注入）。

解决三类 HTTP 抓不到的页面：反爬 / JS 渲染 / 需登录。

要点：
  · 驱动**系统已装的 Edge**（``channel="msedge"``）—— 零浏览器下载；
  · **持久化 profile**（``browser_data/``）—— 登录态 / cookie 跨次保留；
  · 反爬判据在 ``challenge`` 模块里，本模块只负责「取页 + 何时收工」。
"""
import os
import sys

from .challenge import (
    _BLOCK_MSG,
    _CHALLENGE_FAIL_TMPL,
    _looks_like_block,
    _looks_like_challenge,
    _post_check,
    _wait_for_challenge,
)
from .config import EDGE_UA, PROFILE_DIR

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # 没装 playwright 就用不了 --render
    sync_playwright = None

# 反检测注入脚本 —— 抽成**外部文件**（同目录 anti_detect.js），可自行编辑。
# 抽出来的目的是「允许自定义」：想加自己的伪装，改那个 .js 即可，不必动 Python。
_ANTI_DETECT_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "anti_detect.js")


def _load_anti_detect():
    """读取外部反检测脚本；文件缺失 / 读失败时返回 None（跳过注入，不报错）。

    不缓存 —— 这样编辑 anti_detect.js 后下次 --render 立即生效。
    文件很小，每次渲染读一次的成本可以忽略。
    """
    try:
        with open(_ANTI_DETECT_FILE, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


class _CIHeaders(dict):
    """大小写不敏感的 headers —— 冒充 httpx.Headers（后者也是大小写不敏感的）。

    旧实现直接用普通 dict：`r.headers.get("Content-Type")` 取不到值。
    当前 main() 里恰好写的是小写键才没出事，属脆弱点。
    """

    def __init__(self, *args, **kwargs):
        super().__init__({str(k).lower(): v for k, v in dict(*args, **kwargs).items()})

    def get(self, key, default=None):
        return super().get(str(key).lower(), default)

    def __getitem__(self, key):
        return super().__getitem__(str(key).lower())

    def __contains__(self, key):
        return super().__contains__(str(key).lower())


class _Resp:
    """让渲染结果冒充 httpx.Response，下游提取代码不用改。"""

    def __init__(self, text, url, status, goto_error=None):
        self.text = text
        self.url = url
        self.status_code = status
        self.headers = _CIHeaders({"content-type": "text/html"})
        self.goto_error = goto_error   # 非 None = 导航未正常完成（超时/中断）


def _auto_scroll(page, max_scrolls=5):
    """滚到底触发懒加载，再滚回顶部。"""
    try:
        for _ in range(max_scrolls):
            prev = page.evaluate("document.body.scrollHeight")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(800)
            if page.evaluate("document.body.scrollHeight") <= prev:
                break
        page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass


# ── settle（等页面稳定）────────────────────────────────────────────
# 替代原先的「固定等 N 秒」：慢站等不够、快站白等。参数取自 wigolo
# `settle.ts` 的实测值（那里也是「单 deadline，三路竞速」）。
_SETTLE_POLL_MS = 400            # 轮询间隔
_SETTLE_STABLE_N = 2             # 连续 N 次「几乎没增长」→ 认定停止增长
_SETTLE_DELTA_CHARS = 40         # 单次增长 < 40 字符
_SETTLE_DELTA_RATIO = 0.02       # 且 < 2%
_SETTLE_NETWORKIDLE_MS = 2000    # 先 best-effort 等网络空闲（限时，超时不算错）

# 正文探针 —— 算正文时**排除页面框架**（nav / header / aside / footer / 侧栏）。
#
# ⚠️ 这一步是 settle 能不能用的关键，别省（抄自 wigolo hydration-probe.ts:88-129
#    的实测）：react.dev 的侧栏光链接文字就 >1200 字符、还带好几个 <p>。
#    若不排除，**侧栏一挂载就会被误判「已水合」**，于是抓到的全是导航壳。
#
# 水合判据 = 正文够长 **且** 有结构。`<pre>/<code>` 那一支是给代码密集型文档
# 留的（react.dev / VitePress 这类常常没有 3 个 <p>，正文全在代码块里）。
_SETTLE_PROBE = r"""
() => {
  const CHROME = 'nav,header,aside,footer,[role=navigation],[role=banner],[role=contentinfo],.sidebar,#sidebar,.toc,.menu';
  const pick = document.querySelector('main,article,[role=main]') || document.body;
  if (!pick) return { text: 0, p: 0, code: 0, tr: 0, hydrated: false };
  const total = (pick.innerText || '').trim().length;
  let chrome = 0;
  pick.querySelectorAll(CHROME).forEach(el => { chrome += (el.innerText || '').length; });
  const text = Math.max(0, total - chrome);
  const p = pick.querySelectorAll('p').length;
  const code = pick.querySelectorAll('pre,code').length;
  const tr = pick.querySelectorAll('tr').length;
  const hydrated = text > 500 && (p >= 3 || code >= 2 || (p >= 1 && p + code + tr >= 4));
  return { text, p, code, tr, hydrated };
}
"""


def _settle(page, budget_ms):
    """等页面「稳定下来」—— 单 deadline，三路竞速。

      ① 水合探针   —— 识别到正文**即刻返回**，不等满预算
      ② 稳定性轮询 —— 每 400ms 读一次正文指标，连续 2 次增长 < 40 字符且 < 2%
                       → 认定「停止增长」
      ③ 预算上限   —— 到点就走

    返回 (是否水合, 实际等待秒数)。全程不抛异常 —— 探针失败就当没水合继续等。
    """
    try:
        page.wait_for_load_state("networkidle", timeout=_SETTLE_NETWORKIDLE_MS)
    except Exception:
        pass                                   # 限时 best-effort，超时不算错

    waited_ms = 0
    prev = None
    stable = 0
    while waited_ms < budget_ms:
        m = None
        try:
            m = page.evaluate(_SETTLE_PROBE)
        except Exception:
            pass                               # 页面正在导航等 → 当作没读到
        if m:
            if m.get("hydrated"):
                return True, waited_ms / 1000
            cur = m.get("text", 0)
            if prev is not None:
                delta = cur - prev
                if 0 <= delta < _SETTLE_DELTA_CHARS and \
                        delta / max(prev, 1) < _SETTLE_DELTA_RATIO:
                    stable += 1
                    if stable >= _SETTLE_STABLE_N:
                        return False, waited_ms / 1000
                else:
                    stable = 0                 # 还在长 → 重新计数
            prev = cur
        page.wait_for_timeout(_SETTLE_POLL_MS)
        waited_ms += _SETTLE_POLL_MS
    return False, waited_ms / 1000


def _new_context(p, headed, proxy):
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=PROFILE_DIR,
        channel="msedge",                      # 用系统 Edge，零下载
        headless=not headed,
        viewport={"width": 1280, "height": 800},
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        user_agent=EDGE_UA,                    # 关键：抹掉 HeadlessChrome 字样
        proxy={"server": proxy} if proxy else None,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    )
    script = _load_anti_detect()
    if script:
        ctx.add_init_script(script)
    return ctx


def _close_quietly(ctx):
    """关上下文但别让关闭异常盖掉真正的错误（窗口已崩 / 已被用户关掉）。"""
    try:
        ctx.close()
    except Exception:
        pass


def render_page(url, headed=False, wait=None, scroll=True, proxy=None, timeout=30,
                interactive=False, challenge_timeout=None):
    """用真实浏览器渲染页面，返回 (_Resp, 错误)。

    四种取页方式：
      ① 常规        —— 导航后**等页面稳定**（水合探针 / 稳定性轮询 / 预算上限
                       三路竞速，见 `_settle`）；`wait` 是**预算上限**而非固定
                       等待（默认 6s），通常用不满
      ② 拦截页      —— 命中 WAF 拦截特征（Access Denied / Error 1020…）→ **立刻收工**，
                       返回 (None, 说明)。等也不会放行，别浪费用户时间。
      ③ 遇到挑战页  —— 自动改为「等页面真正变掉」，最长 challenge_timeout 秒。
                       默认值按模式走：**有头 180s**（你手点验证需要时间）／
                       **无头 20s**（看不到窗口、没法手动操作，等太久纯属浪费）。
                       超时仍未通过 → **直接收工**，返回 (None, 说明)，不返回 CF 中间页。
      ④ --interactive —— 有头打开，等你在终端按回车后才抓取；适合「必须人工
                       操作、且脚本猜不到何时结束」的场景（登录、复杂验证）。
                       此模式由你全权决定何时抓，故**不做拦截/挑战判定**。

    交付前统一过后置校验（``challenge._post_check``）：命中浏览器错误页
    （DNS 失败 / 连接被拒 / 导航超时）或 SPA 空壳时**直接收工、不交付内容**
    —— 与「不把 CF 中间页当正文」同一条原则。③ 与 ① 的区别：① 是站点拒绝
    你，③ 是**压根没打开**或**打开了但没内容**。
    """
    if sync_playwright is None:
        return None, "未安装 playwright —— 跑 pip install playwright"
    if wait is None:                   # 未显式指定 → 用默认「预算上限」
        wait = 6.0
    if challenge_timeout is None:      # 未显式指定 → 按有无窗口给默认值
        challenge_timeout = 180 if headed else 20
    os.makedirs(PROFILE_DIR, exist_ok=True)
    try:
        with sync_playwright() as p:
            ctx = _new_context(p, headed or interactive, proxy)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            status = 0
            goto_err = None
            try:
                resp = page.goto(url, wait_until="domcontentloaded",
                                 timeout=timeout * 1000)
                status = resp.status if resp else 0
            except Exception as e:
                # goto 超时/中断：内容可能已部分加载，继续提取，但**留痕**
                # （下游据此区分「页面抓到但正文为空」与「压根没导航成功」）
                goto_err = f"{type(e).__name__}: {str(e)[:120]}"

            if interactive:
                print("浏览器已打开 —— 请在窗口里完成操作（登录 / 点验证 …），"
                      "完成后回终端按【回车】继续。", file=sys.stderr)
                try:
                    input()
                except (EOFError, KeyboardInterrupt):
                    pass
                if not ctx.pages:
                    return None, "浏览器窗口已关闭，没有可读取的页面"
            else:
                # ② settle：单 deadline 三路竞速，替代原先的「固定等 wait 秒」
                hydrated, waited = _settle(page, int(max(0.5, wait) * 1000))
                print(f"[渲染] 等待 {waited:.1f}s"
                      f"（{'已水合' if hydrated else '内容已稳定'}）", file=sys.stderr)

                # ① 拦截页（WAF / Access Denied）—— 等多久都不放行，立刻收工
                if _looks_like_block(page):
                    print(f"[拦截页] {_BLOCK_MSG}", file=sys.stderr)
                    _close_quietly(ctx)
                    return None, _BLOCK_MSG

                # ② 挑战页：固定等待没用（计时从挑战页起算），改成等页面真正变掉
                if _looks_like_challenge(page):
                    hint = ("" if headed else
                            "（当前无头模式，看不到窗口、无法手动操作；建议加 --headed）")
                    print(f"[验证页] 检测到人机验证/挑战页，最多等待 "
                          f"{challenge_timeout}s{hint}…", file=sys.stderr)
                    ok, waited = _wait_for_challenge(page, challenge_timeout)
                    if ok:
                        print(f"[验证页] 已通过（等待 {waited}s），继续提取正文。",
                              file=sys.stderr)
                        # 过完挑战仍可能落到拦截页 —— 再确认一次，别把垃圾当正文
                        if _looks_like_block(page):
                            print(f"[拦截页] {_BLOCK_MSG}", file=sys.stderr)
                            _close_quietly(ctx)
                            return None, _BLOCK_MSG
                    else:
                        msg = _CHALLENGE_FAIL_TMPL.format(sec=challenge_timeout)
                        print(f"[验证页] {msg}", file=sys.stderr)
                        _close_quietly(ctx)
                        return None, msg

                if scroll:
                    _auto_scroll(page)

                # ③ 后置校验 —— 别把「浏览器错误页 / SPA 空壳」当正文交出去。
                #    ⚠️ 不能靠内容长度判断：错误页的 HTML 能有 300+ KB，
                #    提取器照样能从里面刨出几百字（见 challenge._post_check）。
                verdict = _post_check(page)
                if verdict:
                    print(f"[后置校验] {verdict}", file=sys.stderr)
                    _close_quietly(ctx)
                    return None, verdict

            try:
                html, final_url = page.content(), page.url
            except Exception as e:
                return None, f"读取页面内容失败：{type(e).__name__}: {str(e)[:120]}"
            _close_quietly(ctx)
            return _Resp(html, final_url, status, goto_err), None
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:200]}"


def do_login(url="about:blank", proxy=None):
    """打开有头浏览器供手动登录；登录态存进持久化 profile。关窗口即结束。

    proxy 由 main() 决定（默认探测 Clash 是否可达）—— 登录境外站必须走代理才打得开。
    """
    if sync_playwright is None:
        print("未安装 playwright —— 跑 pip install playwright", file=sys.stderr)
        return 1
    os.makedirs(PROFILE_DIR, exist_ok=True)
    print(f"profile 目录: {PROFILE_DIR}")
    print(f"代理: {proxy}" if proxy else "代理: 无（直连）")
    print("浏览器即将打开 —— 请登录你需要的网站，**完成后直接关闭浏览器窗口**。")
    with sync_playwright() as p:
        ctx = _new_context(p, headed=True, proxy=proxy)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if url and url != "about:blank":
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                pass
        try:
            while ctx.pages:
                page.wait_for_timeout(1000)
        except KeyboardInterrupt:
            pass
        except Exception:
            pass          # 用户关闭窗口 → TargetClosedError，属正常结束
        try:
            ctx.close()
        except Exception:
            pass
    print("浏览器已关闭，登录态已保存到 profile。")
    return 0
