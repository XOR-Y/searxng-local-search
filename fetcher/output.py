#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""输出层 —— 元信息组装 + 正文选优编排 + Markdown 头 + 截断 + 落盘。

分成两半，便于测试：
  · ``produce()`` —— **纯函数**，只吃参数、只吐字符串，不碰 stdout / 文件系统；
  · ``emit()``    —— 只管 I/O（打印或写文件），返回退出码。
"""
import json
import re
import sys

import trafilatura
from markdownify import markdownify as _md

from .extract import _choose, _strip_md, _via_readability, _via_trafilatura


def _build_meta(html, url, status_code, how):
    """元信息：优先 trafilatura，失败则只留 <title> 兜底与三样固定字段。"""
    try:
        doc = trafilatura.bare_extraction(html, url=str(url), with_metadata=True)
    except Exception as e:
        print(f"[元信息警告] trafilatura.bare_extraction 失败，跳过元信息: "
              f"{type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
        doc = None
    meta = {}
    if doc is not None:
        for k in ("title", "author", "date", "sitename", "description", "url", "hostname"):
            v = getattr(doc, k, None)
            if v:
                meta[k] = v
    if "title" not in meta:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        if m:
            meta["title"] = re.sub(r"\s+", " ", m.group(1)).strip()
    meta["final_url"] = str(url)
    meta["fetched_via"] = how
    meta["http_status"] = status_code
    return meta


def produce(html, url, status_code, how, text_mode, html_mode, meta_mode,
            engine_pref, max_chars):
    """把抓到的 HTML 组装成最终输出文本（纯函数，不写任何东西）。

    三种输出模式互斥，优先级 meta > html > 正文提取。
    """
    meta = _build_meta(html, url, status_code, how)

    if meta_mode:
        out = json.dumps(meta, ensure_ascii=False, indent=2)
    elif html_mode:
        out = html
    else:
        cands = []
        if engine_pref in ("auto", "trafilatura"):
            t = _via_trafilatura(html, str(url), text_mode)
            if t:
                cands.append(("trafilatura", t))
        if engine_pref in ("auto", "readability"):
            rd, rtitle = _via_readability(html, str(url), text_mode)
            if rd:
                cands.append(("readability", rd))
            if rtitle and not meta.get("title"):
                meta["title"] = rtitle

        engine, body = _choose(cands)
        if body:
            head = "" if engine_pref != "auto" or len(cands) < 2 else \
                f"<!-- 引擎选优: {engine}（候选: {', '.join(n for n, _ in cands)}） -->\n\n"
        else:  # 两个引擎都没提取到 → 整页降级
            body = _md(html, heading_style="ATX")
            if text_mode:
                body = _strip_md(body)
            engine = "整页降级"
            head = "<!-- 两个引擎都未提取到正文，已降级为整页转换 -->\n\n"
        meta["engine"] = engine

        header = (f"# {meta.get('title', '(无标题)')}\n\n"
                  f"- 来源: {meta['final_url']}\n"
                  + (f"- 站点: {meta['sitename']}\n" if meta.get("sitename") else "")
                  + (f"- 日期: {meta['date']}\n" if meta.get("date") else "")
                  + (f"- 作者: {meta['author']}\n" if meta.get("author") else "")
                  + (f"- 引擎: {engine}\n" if engine_pref == "auto" else "")
                  + "\n---\n\n")
        out = header + head + body

    if max_chars and len(out) > max_chars:
        out = out[: max_chars] + f"\n\n... [已截断，原文 {len(out)} 字符]"
    return out


def emit(out, out_path, how):
    """把结果交给用户：写文件或打到 stdout。返回退出码（0 正常 / 4 写入失败）。"""
    # 编码保护提到两个分支之前 —— 否则 `if out_path` 分支的 print 在 GBK 控制台下
    # 遇到非 GBK 字符（文件名/路径）会 UnicodeEncodeError
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if out_path:
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(out)
        except OSError as e:
            print(f"[写入失败] {out_path}: {e}", file=sys.stderr)
            return 4
        print(f"已写入 {out_path}  ({len(out)} 字符, 经 {how})")
    else:
        print(out)
    return 0
