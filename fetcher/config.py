#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可配置常量 —— 全部可用环境变量覆盖，换机 / 换代理时不必改源码。

⚠️ ``BASE_DIR`` 必须指向**仓库根**（即 fetch.py 所在目录），不是本文件所在目录：
   ``browser_data/`` 等运行期路径都挂在它下面。本文件位于 ``fetcher/`` 子目录，
   所以这里要多剥一层 —— 若日后再挪动本文件，务必同步调整，否则 profile 会
   悄悄换到 ``fetcher/browser_data/``，登录态全丢且脚本不报错。
"""
import os

# 仓库根 = 本文件（fetcher/config.py）的上一级的上一级
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── HTTP 路径 ────────────────────────────────────────────────
PROXY = os.environ.get("LOCAL_SEARCH_PROXY", "http://127.0.0.1:7897")
UA = os.environ.get(
    "LOCAL_SEARCH_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# ── 渲染路径 ─────────────────────────────────────────────────
PROFILE_DIR = os.path.join(BASE_DIR, "browser_data")   # 持久化 profile（登录态在这）
# ⚠️ EDGE_UA 带浏览器大版本号，会随 Edge 升级而过时 —— 可用环境变量覆盖，不必改源码
EDGE_UA = os.environ.get(
    "LOCAL_SEARCH_EDGE_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36 Edg/154.0.0.0")
