#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""正文提取 —— trafilatura 与 readability 双引擎，按「长行占比」自动选优。

设计约束：本模块**不依赖网络、不依赖浏览器**，只吃 ``(html, url, text_mode)``，
因此可以脱离真实抓取单独测试。

⚠️ 这是**质量优先**的一层：默认 auto 会把两个引擎都跑一遍再择优，
   而不是「谁先出结果就用谁」—— 目的是让 AI 拿到更接近原文献的正文。
"""
import re
import sys

import trafilatura
from markdownify import markdownify as _md

try:
    from readability import Document as _ReadabilityDoc
except ImportError:  # 没装就退回只用 trafilatura
    _ReadabilityDoc = None


def _score(text):
    """返回 (长行占比, 长行总字数)。

    真文章句子长；导航栏/侧栏/标签列表全是短行。
    实测: mcmod 侧栏 4% vs 正文 64%；维基 86% vs 94%。
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return 0.0, 0
    long_lines = [l for l in lines if len(l) >= 30]
    return len(long_lines) / len(lines), sum(len(l) for l in long_lines)


def _strip_md(s):
    """Markdown → 纯文本（--text 模式用）。

    ⚠️ 只处理**成对**的强调标记，绝不无差别删 `*` / `_` / 反引号。
    旧实现是 `re.sub(r"[*_`]{1,3}", "", s)` —— 会把 `snake_case` 删成 `snakecase`、
    `__init__` 删成 `init`，属**内容级损坏**（技术文档 / 代码片段是重灾区）。
    代价：单个 `*斜体*` 会残留星号 —— 比误删正文好得多。
    """
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)           # 图片
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)       # 链接 → 保留锚文本
    s = re.sub(r"^\s{0,3}#{1,6}\s*", "", s, flags=re.M)  # 标题
    s = re.sub(r"^\s{0,3}>\s?", "", s, flags=re.M)       # 引用
    s = re.sub(r"^\s*[-*+]\s+", "", s, flags=re.M)       # 列表符号
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)               # **粗体**（成对）
    s = re.sub(r"`([^`\n]+)`", r"\1", s)                 # `行内代码`（成对）
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _via_trafilatura(html, url, text_mode):
    """trafilatura 提取正文。畸形 HTML 可能让它抛异常 —— 捕获后返回 ""，
    由调用方自动降级到 readability（或整页转换），不让整个程序 traceback 退出。"""
    try:
        return trafilatura.extract(
            html, url=url, output_format="txt" if text_mode else "markdown",
            include_links=not text_mode, include_images=False,
            include_formatting=not text_mode, with_metadata=False,
        ) or ""
    except Exception as e:
        print(f"[提取警告] trafilatura 失败，改用其它引擎: "
              f"{type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
        return ""


def _via_readability(html, url, text_mode):
    """Mozilla Readability 选正文 → markdownify 转 Markdown。

    这套流水线来自 MarkDownload 扩展（Readability + Turndown），
    在 wiki / 结构化页面上比 trafilatura 强很多。
    """
    if _ReadabilityDoc is None:
        return "", None
    try:
        doc = _ReadabilityDoc(html)
        content = doc.summary(html_partial=True) or ""
        if not content:
            return "", None
        out = _md(content, heading_style="ATX")
        if text_mode:
            out = _strip_md(out)
        try:
            title = doc.short_title()
        except Exception:
            title = None
        return out, title
    except Exception as e:
        print(f"[提取警告] readability 失败，改用其它引擎: "
              f"{type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
        return "", None


def _choose(cands):
    """多引擎结果选优。cands = [(引擎名, 文本), ...]"""
    if not cands:
        return None, None
    if len(cands) == 1:
        return cands[0]
    stats = [(n, t) + _score(t) for n, t in cands]   # (名, 文本, 占比, 长行字数)
    mx = max(s[3] for s in stats)
    floor = max(200, int(0.6 * mx))                  # 内容量太小的直接淘汰
    solid = [s for s in stats if s[3] >= floor]
    pool = solid or stats
    pool.sort(key=lambda s: (-s[2], -s[3]))
    return pool[0][0], pool[0][1]
