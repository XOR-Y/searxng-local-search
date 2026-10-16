"""从「无语义标签」的卡片 / 列表页提取条目。

背景：fetch.py 的 readability / trafilatura 靠 <article>/<p> 等语义标签判定正文。
      Tailwind / React 生成的纯 <div> 卡片页（如 latentbox.com）没有任何语义标签，
      两个引擎都会失效 —— 渲染后 DOM 有 1.4 MB 内容，却只提出 232 字符。

典型流程（两步）：
    1. 先拿渲染后的完整 DOM（静态抓多半是空壳）：
         python fetch.py <url> --render --wait 12 --html -o page.html
    2. 再按 class 关键词提取条目：
         python extract_cards.py page.html
         python extract_cards.py page.html --class-keyword font-bold --section-tag h2

依赖：lxml（trafilatura 的依赖，项目嵌入式 Python 里已带）
用法：python extract_cards.py <html文件> [--class-keyword KW] [--section-tag TAG] [--max-len N]
"""
import argparse
from lxml import html as LH


def clean(el):
    return " ".join(el.text_content().split())


def main():
    ap = argparse.ArgumentParser(description="从无语义标签的卡片页提取条目")
    ap.add_argument("html_file", help="渲染后保存的 HTML 文件")
    ap.add_argument("--class-keyword", default="font-bold",
                    help="条目元素的 class 关键词（默认 font-bold）")
    ap.add_argument("--section-tag", default="h2",
                    help="章节标题标签（默认 h2）")
    ap.add_argument("--max-len", type=int, default=120,
                    help="单条文本长度上限，超长的丢弃（默认 120）")
    args = ap.parse_args()

    try:
        doc = LH.parse(args.html_file)
    except OSError as e:
        print(f"[错误] 读不到文件：{e}")
        return 1

    root = doc.getroot()
    for bad in root.xpath("//script|//style|//noscript|//svg"):
        p = bad.getparent()
        if p is not None:
            p.remove(bad)

    sections, order, cur = {}, [], "(未分组)"
    sections[cur] = []
    for el in root.iter():
        if el.tag == args.section_tag:
            t = clean(el)
            if t and len(t) < 40 and t not in sections:
                cur = t
                sections[cur] = []
                order.append(cur)
        elif el.tag == "div" and args.class_keyword in (el.get("class") or ""):
            t = clean(el)
            if t and len(t) < args.max_len:
                sections.setdefault(cur, []).append(t)

    total = 0
    for s in order:
        items = sections.get(s, [])
        total += len(items)
        print(f"\n## {s}  ({len(items)})")
        for i in items:
            print("  -", i)
    print(f"\n=== 合计 {total} 条 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
