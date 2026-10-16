#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch 工具链的内部实现模块。

对外入口是仓库根目录的 ``fetch.py``（CLI），本包只负责把实现拆开：

    config      可配置常量（环境变量可覆盖）
    net         HTTP 快路径：代理探测 + httpx 取页
    challenge   反爬识别：挑战页 / WAF 拦截页的判据与等待
    browser     浏览器慢路径：Playwright + 系统 Edge
    extract     正文提取：trafilatura / readability 双引擎择优
    output      元信息组装 + 正文编排 + 输出格式化 + 落盘

依赖方向（单向，无环）::

    fetch.py ─→ net ──────┐
             ├→ browser ──┼→ challenge
             ├→ extract   │
             └→ output ───┘
                  ↑
                config（所有人共用）

``challenge`` 刻意保持零依赖（只吃一个 duck-type 的 page 对象），
因此它可以脱离真实浏览器单独测试。
"""
