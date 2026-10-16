#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP 快路径 —— 代理探测 + httpx 取页（不涉及浏览器）。

默认策略：先直连（国内站快），失败自动改走 Clash 代理（境外站）。
连接类失败才重试代理；若已收到 HTTP 响应，只有 403/429 才换代理重试，
其余 4xx/5xx 直接以真实状态码报错（不白跑代理、不掩盖真实状态）。
"""
import inspect
import socket

import httpx

from .config import PROXY, UA


def _proxy_alive(proxy, timeout=1.0):
    """探测代理端口是否真的能连上（避免 Clash 没开时把本该直连的也拖死）。

    只做 TCP 连通性检查、不发 HTTP 请求 —— 快，且不依赖代理协议细节。
    """
    try:
        host, _, port = proxy.split("//")[-1].rstrip("/").partition(":")
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def _interactive_proxy(no_proxy, explicit):
    """交互类场景（--login / --interactive）的代理选择。

    要人工操作的场景多半是「需登录 / 需过验证」的境外站，默认走 Clash 更省事；
    但先探测端口 —— Clash 没开就退回直连，不把本来能用的直连也拖死。
    """
    if no_proxy:
        return None
    if explicit:
        return explicit
    return PROXY if _proxy_alive(PROXY) else None


# httpx 0.26 起 `proxies=` 改名 `proxy=`。用**签名探测**一次，而不是 try/except TypeError
# —— 后者会把「其它原因引发的 TypeError」也当成版本问题吞掉，掩盖真实错误。
try:
    _HTTPX_NEW_PROXY = "proxy" in inspect.signature(httpx.Client).parameters
except (ValueError, TypeError):
    _HTTPX_NEW_PROXY = True            # 探测不了就按新版走（当前环境 httpx 0.28.x）


def _client(proxy, timeout):
    """兼容 httpx 新旧参数的 Client 构造"""
    kw = dict(timeout=timeout, follow_redirects=True,
              headers={"User-Agent": UA,
                       "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                       "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
    if _HTTPX_NEW_PROXY:
        return httpx.Client(proxy=proxy, **kw)          # httpx >= 0.26
    return httpx.Client(proxies=proxy, **kw)            # 老版本


# ── 反爬墙的 header 信号（零误报：放行的正常响应绝不会带）──
# 依据 wigolo `fetch/tls-tier.ts:427-454`。价值在于：挑战页 / 拦截页常常以
# **HTTP 200** 下发，只看 status_code 会把墙当成正文抓回去。
# ⚠️ 只用「放行响应不携带」的键 —— 例如 `x-datadome-cid` 在正常放行的响应上
#    也会带，拿它当判据会满世界误报。
def _wall_from_headers(headers):
    """响应头里是否带反爬墙标记。命中返回厂商说明，否则 None。"""
    h = {str(k).lower(): str(v) for k, v in headers.items()}
    cf = h.get("cf-mitigated", "").strip().lower()
    if cf in ("challenge", "block"):
        return f"Cloudflare（cf-mitigated: {cf}）"
    if h.get("x-dd-b", "").strip() not in ("", "0"):
        return "DataDome（x-dd-b）"
    if h.get("x-datadome", "").strip().lower() in ("protected", "blocked"):
        return "DataDome（x-datadome）"
    # AWS WAF —— 本机实测来源：Brave 搜索的 HTML 端点（405 + captcha，
    # 换美国节点也一样，因为拦的是数据中心 IP 而不是地区）
    aws = h.get("x-amzn-waf-action", "").strip().lower()
    if aws in ("captcha", "challenge", "block"):
        return f"AWS WAF（x-amzn-waf-action: {aws}）"
    return None


def fetch(url, proxy=None, timeout=30):
    """返回 (response, 说明, 错误)。

    proxy=None → 直连失败自动改走代理；False → 只直连；给了地址 → 只用该代理。

    错误分两类处理：
      · 连接类异常（DNS 解析失败 / 超时 / 连接被拒 / 代理不可用）→ 换下一条路径重试；
      · 已收到 HTTP 响应 → 只有 403/429（疑似按 IP 封锁或限流）才值得换代理再试，
        其余 4xx/5xx 直接用**真实状态码**报错，不白跑一遍代理、也不让代理的错误
        盖掉服务器返回的真实信息。
    """
    if proxy is False:
        attempts = [(None, "直连")]
    elif proxy:
        attempts = [(proxy, f"代理 {proxy}")]
    else:
        attempts = [(None, "直连"), (PROXY, f"代理 {PROXY}")]

    RETRY_STATUS = {403, 429}          # 疑似 IP 封锁/限流 → 换代理再试一次
    errs = []
    wall_hit = False
    for i, (p, label) in enumerate(attempts):
        last = i == len(attempts) - 1
        try:
            with _client(p, timeout) as c:
                r = c.get(url)
        except Exception as e:                      # 连接类异常 → 换下一条路径
            errs.append(f"{label} → {type(e).__name__}: {str(e)[:120]}")
            continue
        # ① 反爬墙（header 信号，零误报）—— 状态码可能是 200，别当正文交出去
        wall = _wall_from_headers(r.headers)
        if wall:
            wall_hit = True
            errs.append(f"{label} → 反爬墙：{wall}")
            if last:
                break
            continue                                # 换下一条路径再试（换 IP 有时能过）

        if r.status_code < 400:
            return r, label, None
        errs.append(f"{label} → HTTP {r.status_code}")
        if r.status_code not in RETRY_STATUS or last:
            break                                   # 真实 HTTP 状态码，不必再跑代理

    detail = "；".join(errs)
    if wall_hit:
        detail += ("。检测到反爬墙 —— HTTP 快路径过不去（挑战要执行 JS），"
                   "试试加 --render 用浏览器渲染")
    return None, None, detail
