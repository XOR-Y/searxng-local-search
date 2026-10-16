import asyncio
import os
import sys
import time
from urllib.parse import quote_plus
from mcp.server.fastmcp import FastMCP
from playwright.async_api import async_playwright, Page, BrowserContext

# ─────────────────────────────────────────────
#  初始化 FastMCP
# ─────────────────────────────────────────────
mcp = FastMCP("LocalBrowserCommander")

# ─────────────────────────────────────────────
#  路径 & 代理配置
# ─────────────────────────────────────────────
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
USER_DATA_DIR  = os.path.join(BASE_DIR, "browser_data")
DEBUG_SHOT_DIR = os.path.join(BASE_DIR, "_debug_shots")   # 调试截图落点（不入库）

# Clash Verge 代理。环境变量 LOCAL_SEARCH_PROXY 可覆盖；
# 设为空串 / none / off / 0 则禁用代理（与 fetch.py 共用同一套变量名）。
PROXY = os.environ.get("LOCAL_SEARCH_PROXY", "http://127.0.0.1:7897").strip()
if PROXY.lower() in ("", "none", "off", "0"):
    PROXY = None

# 浏览器 UA —— 走 msedge 通道，UA 必须带 Edg/ 才与真实浏览器自洽。
# ⚠️ 大版本号会随 Edge 升级过时，用环境变量覆盖即可，不必改源码。
EDGE_UA = os.environ.get(
    "LOCAL_SEARCH_EDGE_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36 Edg/154.0.0.0",
)

for d in [USER_DATA_DIR]:
    os.makedirs(d, exist_ok=True)

# ─────────────────────────────────────────────
#  全局状态
# ─────────────────────────────────────────────
state: dict = {
    "playwright": None,
    "context":    None,
    "pages":      [],       # 所有 tab
    "active_idx": 0,        # 当前活跃 tab 索引
}

# 并发保护：MCP 工具可能被并发调用。若两个协程同时发现 context 为 None，
# 会各自启动一个浏览器去抢同一个 profile（browser_data/ 是独占锁）→ 用锁把
# 「检查 + 启动 + 崩溃重启」这一段串行化。后续页面操作不占锁，仍可并发。
_state_lock = asyncio.Lock()


# ═══════════════════════════════════════════════════════════════
#  内部辅助函数
# ═══════════════════════════════════════════════════════════════

async def _launch_browser() -> None:
    """启动浏览器（持久化 context）。"""
    state["playwright"] = await async_playwright().start()
    proxy_config = {"server": PROXY} if PROXY else None
    ctx: BrowserContext = await state["playwright"].chromium.launch_persistent_context(
        user_data_dir=USER_DATA_DIR,
        # 走系统 Edge，零下载 —— 与 fetch.py 保持一致。
        # 若用默认 chromium，本机须先 `playwright install chromium`（版本随 playwright 走，
        # 缓存里没有对应 revision 就会报 Executable doesn't exist）。
        channel="msedge",
        headless=False,
        proxy=proxy_config,
        viewport={"width": 1280, "height": 800},
        user_agent=EDGE_UA,
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        permissions=["geolocation"],
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ],
    )
    await ctx.set_geolocation({"latitude": 39.125595, "longitude": 117.201186})

    # 注入反检测脚本
    await ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
        window.chrome = { runtime: {} };

        // 伪造 WebGL 信息，规避高级反爬检测
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {
            if (parameter === 37445) return 'Intel Open Source Technology Center';
            if (parameter === 37446) return 'Mesa DRI Intel(R) HD Graphics 520 (Skylake GT2)';
            return getParameter.apply(this, arguments);
        };

        // Canvas 指纹扰动，规避 WAF（Cloudflare / 阿里云防火墙）指纹检测
        const originalGetImageData = CanvasRenderingContext2D.prototype.getImageData;
        CanvasRenderingContext2D.prototype.getImageData = function() {
            const res = originalGetImageData.apply(this, arguments);
            res.data[0] = res.data[0] + (Math.random() > 0.5 ? 1 : -1);
            return res;
        };
    """)

    state["context"] = ctx

    # ── 注入 Google 搜索偏好 Cookie
    await _inject_search_cookies(ctx)

    existing = ctx.pages
    if existing:
        state["pages"] = list(existing)
    else:
        state["pages"] = [await ctx.new_page()]
    state["active_idx"] = 0


async def _inject_search_cookies(ctx: BrowserContext) -> None:
    """
    注入 Google 搜索偏好 Cookie：中文界面、关闭个性化推荐。
    每次调用都会覆盖旧值，保证以干净状态开始。
    """
    search_cookies = [
        {
            # Google 界面语言：简体中文
            "name":   "PREF",
            "value":  "ID=1111111111:FF=0:LD=zh-CN:TM=1736900000:LM=1736900000:S=zY_abcdefghij",
            "domain": ".google.com",
            "path":   "/",
            "secure": True,
            "httpOnly": False,
            "sameSite": "None",
        },
        {
            # 语言/地区：zh-CN
            "name":   "hl",
            "value":  "zh-CN",
            "domain": ".google.com",
            "path":   "/",
            # RFC6265bis：SameSite=None 必须搭配 Secure=True，否则 Chrome/CDP
            # 会拒收。add_cookies 是整批提交，一个非法会导致整批失败（含 PREF）。
            "secure": True,
            "httpOnly": False,
            "sameSite": "None",
        },
    ]
    try:
        await ctx.add_cookies(search_cookies)
    except Exception as e:
        print(f"[warn] 注入 Google 搜索偏好 Cookie 失败：{e}", file=sys.stderr)


# Google 搜索历史相关 Cookie 名称（退出时清除）
_GOOGLE_HISTORY_COOKIES = {
    "NID",
    "SID",
    "HSID",
    "SSID",
    "APISID",
    "SAPISID",
    "CONSENT",
    "OGPC",
    "AEC",
    "__Secure-1PSID",
    "__Secure-3PSID",
}


async def ensure_browser() -> Page:
    """确保浏览器已启动，返回当前活跃页面。崩溃后自动重启。"""
    async with _state_lock:
        try:
            if state["context"] is None:
                await _launch_browser()
            # 同步 context.pages 到 state，捕捉点击导致的新标签页
            state["pages"] = list(state["context"].pages)
            # 用户可能手动关掉【所有】标签页（浏览器窗口还开着）→ pages 为空。
            # 这种情况要新建页面，不能当成"浏览器崩溃"走重启分支 —— 那会连带
            # 执行 _cleanup()，把已打开的页面和全部 Cookie 一起搞掉。
            if not state["pages"]:
                state["pages"] = [await state["context"].new_page()]
                state["active_idx"] = 0
            page = _active_page()
            # 检测页面是否还活着
            await page.evaluate("1")
            return page
        except Exception:
            # 浏览器/页面已崩溃，重新启动
            await _cleanup()
            await _launch_browser()
            return _active_page()


def _active_page() -> Page:
    pages = state["pages"]
    # 下限夹到 0，避免 active_idx 为负时取到倒数的页
    idx   = min(max(state["active_idx"], 0), len(pages) - 1)
    return pages[idx]


async def _cleanup() -> None:
    """关闭浏览器前，先清除搜索历史相关 Cookie，再关闭所有资源。"""
    # 清除搜索历史 Cookie（只清 google 域白名单内的那几个）
    try:
        ctx = state["context"]
        if ctx:
            all_cookies = await ctx.cookies(["https://www.google.com"])
            to_clear = [
                c for c in all_cookies
                if c["name"] in _GOOGLE_HISTORY_COOKIES
            ]
            if to_clear:
                # ⚠️ 绝对不要用 ctx.clear_cookies()：无参调用会清空【所有域】的
                # Cookie，把知乎 / bilibili / GitHub 等站点的登录态一并摧毁，
                # 且不可逆（回填的只有 google 域）。Playwright 没有「删除单个
                # Cookie」的 API，正确做法是把目标 Cookie 的 expires 置为过去
                # 时间戳，覆盖写入即等价于删除。
                await ctx.add_cookies([
                    {
                        "name":    c["name"],
                        "value":   "",
                        "domain":  c["domain"],
                        "path":    c.get("path", "/"),
                        "expires": 1,        # 1970-01-01，已过期 → 立即失效
                    }
                    for c in to_clear
                ])
                await _inject_search_cookies(ctx)
    except Exception as e:
        print(f"[warn] 清理 Google 搜索历史 Cookie 失败：{e}", file=sys.stderr)

    try:
        if state["context"]:
            await state["context"].close()
    except Exception:
        pass
    try:
        if state["playwright"]:
            await state["playwright"].stop()
    except Exception:
        pass
    state.update({"playwright": None, "context": None, "pages": [], "active_idx": 0})


def _clean_text(raw: str, max_chars: int = 4000) -> str:
    """清理网页文本：去除多余空白行、合并连续空白。"""
    lines = raw.splitlines()
    cleaned = []
    prev_blank = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if not prev_blank:
                cleaned.append("")
            prev_blank = True
        else:
            cleaned.append(stripped)
            prev_blank = False
    result = "\n".join(cleaned).strip()
    if len(result) > max_chars:
        result = result[:max_chars] + f"\n\n…（内容已截断，共 {len(result)} 字符，已显示前 {max_chars} 字符）"
    return result


async def _auto_scroll_page(page: Page, max_scrolls: int = 3) -> None:
    """
    自动滚动页面加载更多内容（用于无限滚动的页面如社交媒体、信息流）。
    """
    for _ in range(max_scrolls):
        prev_height = await page.evaluate("document.body.scrollHeight")
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(0.8)
        new_height = await page.evaluate("document.body.scrollHeight")
        if new_height <= prev_height:
            break  # 已到底部


async def _extract_main_content(page: Page, max_chars: int = 4000, auto_scroll: bool = True) -> str:
    """
    智能提取页面主要正文内容，过滤导航栏、广告、页脚等干扰元素。
    
    参数：
      auto_scroll - 是否自动滚动加载更多内容（默认 True，适合无限滚动页面）
    """
    if auto_scroll:
        await _auto_scroll_page(page)
    
    content = await page.evaluate(r"""
        () => {
            // 删除明显无用的元素
            const removeSelectors = [
                'nav', 'header', 'footer', 'aside',
                '.ad', '.ads', '.advertisement', '.banner',
                '.nav', '.navbar', '.sidebar', '.menu',
                '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]',
                'script', 'style', 'noscript', 'iframe',
                '.cookie', '.popup', '.modal', '.overlay'
            ];
            removeSelectors.forEach(sel => {
                document.querySelectorAll(sel).forEach(el => el.remove());
            });

            // 优先尝试语义化主内容区域
            const mainSelectors = [
                'article', 'main', '[role="main"]',
                '.article-content', '.post-content', '.entry-content',
                '.content', '#content', '.main-content', '#main-content',
                '.article', '#article', '.post-body', '.article-body'
            ];
            for (const sel of mainSelectors) {
                const el = document.querySelector(sel);
                if (el && el.innerText && el.innerText.trim().length > 200) {
                    return el.innerText;
                }
            }
            // 回退到 body
            return document.body ? document.body.innerText : '';
        }
    """)
    return _clean_text(content or "", max_chars)


def _save_screenshot(png_bytes: bytes, prefix: str = "shot") -> str:
    """把截图字节写入 _debug_shots/，返回落盘路径。"""
    os.makedirs(DEBUG_SHOT_DIR, exist_ok=True)
    fname = (
        f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}"
        f"_{int(time.time() * 1000) % 1000:03d}.png"
    )
    fpath = os.path.join(DEBUG_SHOT_DIR, fname)
    with open(fpath, "wb") as f:
        f.write(png_bytes)
    return fpath


async def _take_debug_screenshot(page: Page) -> str:
    """发生错误时自动截图并落盘，返回文件路径。

    ⚠️ 不要返回 base64 片段 —— LLM 无法从几百个字符还原出图像，
    「视觉调试」等于形同虚设。落盘 + 给路径，调用方（人或 AI）才能真正看到图。
    """
    try:
        png_bytes = await page.screenshot(full_page=False)
        title = await page.title()
        fpath = _save_screenshot(png_bytes, prefix="debug")
        return (
            f"\n📸 [自动调试截图] 当前页面：{title}\n"
            f"图像大小：{len(png_bytes)} 字节\n"
            f"已存至：{fpath}"
        )
    except Exception as se:
        return f"\n⚠️ 自动截图也失败了：{se}"


async def _get_page_outline(page: Page) -> list:
    """提取页面所有的 H1-H3 标签和带 ID 的元素，生成可供 AI 决策的索引。"""
    return await page.evaluate("""() => {
        return Array.from(document.querySelectorAll('h1, h2, h3, [id]'))
            .filter(el => el.innerText && el.innerText.trim().length > 0)
            .map(el => ({
                tag:  el.tagName,
                text: el.innerText.trim().slice(0, 80),
                id:   el.id || ''
            }))
            .slice(0, 30);
    }""")


async def _html_to_simple_md(page: Page, max_chars: int = 4000, auto_scroll: bool = True) -> str:
    """
    在 JS 端做 HTML → Markdown 转换，支持：
      - h1~h6 → # ~ ######
      - a → [text](url)，自动处理脚注、站内链接
      - img → ![alt](src)，处理懒加载 data-src
      - ul/ol → 列表（支持嵌套）
      - table → Markdown 表格（处理 th、合并单元格）
      - pre/code → ```语言\ncode\n```
      - blockquote → > 引用
      - details/summary → 折叠面板语法
      - figure/figcaption → 图片 + 标题
      - 懒加载图片 src 处理
    """
    if auto_scroll:
        await _auto_scroll_page(page)
    
    raw = await page.evaluate(r"""
        () => {
            // 克隆 body 避免影响页面 DOM
            const clone = document.body.cloneNode(true);

            // 移除干扰元素
            const removeSelectors = [
                'nav', 'header', 'footer', 'aside',
                '.ad', '.ads', '.advertisement', '.banner',
                '.nav', '.navbar', '.sidebar', '.menu',
                '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]',
                'script', 'style', 'noscript', 'iframe',
                '.cookie', '.popup', '.modal', '.overlay',
                '.share', '.social', '.comment', '.comments',
                '.related', '.recommend', '.sidebar', '.widget'
            ];
            removeSelectors.forEach(sel => {
                clone.querySelectorAll(sel).forEach(el => el.remove());
            });

            // 预处理：处理懒加载图片
            clone.querySelectorAll('img[data-src], img[data-lazy-src], img[data-srcset]').forEach(img => {
                const src = img.getAttribute('data-src') || img.getAttribute('data-lazy-src');
                if (src && !img.src) img.src = src;
            });

            // 预处理：展开 details 元素
            clone.querySelectorAll('details').forEach(details => {
                details.open = true;
            });

            // 预处理：收集所有 footnote/sup 引用
            const footnotes = new Map();
            clone.querySelectorAll('[id^="fn"], [id^="footnote"], .footnote, .Footnotes li').forEach((fn, i) => {
                footnotes.set(fn.id || ('fn-' + i), fn.innerText.trim());
            });

            // 优先取语义化主内容区域
            const mainSelectors = [
                'article', 'main', '[role="main"]',
                '.article-content', '.post-content', '.entry-content',
                '.content', '#content', '.main-content', '#main-content',
                '.article', '#article', '.post-body', '.article-body',
                '.wiki-content', '.mw-parser-output'  // MC百科等 Wiki 站
            ];
            let root = null;
            for (const sel of mainSelectors) {
                const el = clone.querySelector(sel);
                if (el && el.innerText && el.innerText.trim().length > 200) {
                    root = el;
                    break;
                }
            }
            if (!root) root = clone;

            // 检测代码语言
            function detectCodeLang(el) {
                const classList = el.className || '';
                const langMatch = classList.match(/language-(\w+)|highlight-(\w+)|brush:(\w+)/i);
                if (langMatch) return langMatch[1] || langMatch[2] || langMatch[3];
                const parent = el.parentElement;
                if (parent && parent.className) {
                    const pMatch = parent.className.match(/highlight-(\w+)|brush:(\w+)/i);
                    if (pMatch) return pMatch[1] || pMatch[2];
                }
                return '';
            }

            // 清理文本：规范化空白
            function normalizeSpace(text) {
                return text.replace(/\s+/g, ' ').trim();
            }

            // 递归转换
            function convert(node, depth) {
                if (depth > 15) return '';  // 防止过深递归
                if (!node) return '';

                // 文本节点
                if (node.nodeType === 3) {
                    const text = node.textContent.replace(/\s+/g, ' ');
                    return text.startsWith(' ') || text.endsWith(' ') ? ' ' + text.trim() + ' ' : text;
                }
                if (node.nodeType !== 1) return '';

                const tag = node.tagName.toLowerCase();
                const children = () => Array.from(node.childNodes).map(c => convert(c, depth+1)).join('');

                switch(tag) {
                    // 标题
                    case 'h1': return '\n\n# ' + normalizeSpace(children()).trim() + '\n\n';
                    case 'h2': return '\n\n## ' + normalizeSpace(children()).trim() + '\n\n';
                    case 'h3': return '\n\n### ' + normalizeSpace(children()).trim() + '\n\n';
                    case 'h4': return '\n\n#### ' + normalizeSpace(children()).trim() + '\n\n';
                    case 'h5': return '\n\n##### ' + normalizeSpace(children()).trim() + '\n\n';
                    case 'h6': return '\n\n###### ' + normalizeSpace(children()).trim() + '\n\n';

                    // 段落与换行
                    case 'p': {
                        const text = normalizeSpace(children()).trim();
                        return text ? '\n\n' + text + '\n\n' : '';
                    }
                    case 'br': return '  \n';
                    case 'hr': return '\n\n---\n\n';

                    // 强调
                    case 'strong': case 'b': return '**' + children() + '**';
                    case 'em': case 'i': return '*' + children() + '*';
                    case 'u': return '<u>' + children() + '</u>';
                    case 's': case 'del': return '~~' + children() + '~~';
                    case 'mark': return '===' + children() + '===';
                    case 'kbd': return '`' + children() + '`';
                    case 'sup': {
                        const text = children().trim();
                        // 检查是否是脚注引用
                        const supId = node.id || '';
                        if (supId.match(/^fn|^footnote/i) || text.match(/^\d+$/)) {
                            return '[^' + text.replace(/\D/g, '') + ']';
                        }
                        return '^' + text + '';
                    }
                    case 'sub': return '~' + children() + '~';

                    // 链接与图片
                    case 'a': {
                        const href = node.getAttribute('href') || '';
                        const text = normalizeSpace(children()).trim();
                        if (!text) return '';
                        // 脚注链接
                        if (href.match(/^#fn|^#footnote/i)) {
                            return text;
                        }
                        // 站外链接显示域名
                        if (href && !href.startsWith('#') && !href.startsWith('/')) {
                            try {
                                const domain = new URL(href, 'http://x').hostname.replace('www.', '');
                                return '[' + text + '](' + href + ' ' + domain + ')';
                            } catch { return '[' + text + '](' + href + ')'; }
                        }
                        return href ? '[' + text + '](' + href + ')' : text;
                    }
                    case 'img': {
                        const src = node.getAttribute('src') || node.getAttribute('data-src') || '';
                        const alt = node.getAttribute('alt') || node.getAttribute('title') || '';
                        const title = node.getAttribute('title') || '';
                        if (!src) return '';
                        // 跳过小图标/空 alt 图片
                        if (!alt && node.width && node.width < 32) return '';
                        const imgMd = alt ? '![' + alt.replace(/\s+/g, ' ').trim() + '](' + src + ')' : '![](' + src + ')';
                        return title ? imgMd + ' "' + title + '"' : imgMd;
                    }

                    // 列表
                    case 'ul': {
                        const items = Array.from(node.querySelectorAll(':scope > li'));
                        if (!items.length) return '';
                        const lines = items.map(li => {
                            const prefix = '  '.repeat(depth) + '- ';
                            const content = convert(li, depth+1).trim();
                            // 嵌套列表
                            const nested = li.querySelector(':scope > ul, :scope > ol');
                            if (nested) {
                                return prefix + convert(li.querySelector('p, :scope > *:not(ul):not(ol)'), depth+1).trim() + '\n' + convert(nested, depth+1);
                            }
                            return prefix + content;
                        });
                        return '\n' + lines.join('\n') + '\n';
                    }
                    case 'ol': {
                        const items = Array.from(node.querySelectorAll(':scope > li'));
                        if (!items.length) return '';
                        const lines = items.map((li, i) => {
                            const prefix = '  '.repeat(depth) + (i + 1) + '. ';
                            const content = convert(li, depth+1).trim();
                            const nested = li.querySelector(':scope > ul, :scope > ol');
                            if (nested) {
                                return prefix + convert(li.querySelector('p, :scope > *:not(ul):not(ol)'), depth+1).trim() + '\n' + convert(nested, depth+1);
                            }
                            return prefix + content;
                        });
                        return '\n' + lines.join('\n') + '\n';
                    }
                    case 'li': return children();

                    // 代码
                    case 'pre': {
                        const code = node.querySelector('code');
                        const lang = code ? detectCodeLang(code) : detectCodeLang(node);
                        const text = (code || node).textContent.trim();
                        if (lang) return '\n```' + lang + '\n' + text + '\n```\n\n';
                        return '\n```\n' + text + '\n```\n\n';
                    }
                    case 'code':
                        if (node.parentElement && node.parentElement.tagName.toLowerCase() === 'pre') {
                            return node.textContent;
                        }
                        // 行内代码
                        return '`' + node.textContent.replace(/`/g, '\\`') + '`';

                    // 表格
                    case 'table': {
                        const rows = Array.from(node.querySelectorAll('tr'));
                        if (!rows.length) return '';
                        
                        let md = '\n';
                        rows.forEach((row, ri) => {
                            const cells = Array.from(
                                ri === 0 
                                    ? row.querySelectorAll('th, td') 
                                    : row.querySelectorAll('td, th')
                            );
                            
                            // 首行作表头，紧接着补 GFM 分隔行。
                            // 规范要求表格第 2 行必须是 | --- |，否则整张表渲染不出来。
                            if (ri === 0) {
                                const head = cells.map(c =>
                                    convert(c, depth+1).trim().replace(/\n+/g, ' ').replace(/\s+/g, ' ')
                                );
                                md = '\n| ' + head.join(' | ') + ' |\n';
                                md += '| ' + cells.map(() => '---').join(' | ') + ' |\n';
                            }
                            
                            // 单元格文本；colspan>1 时用空单元格补齐，保持每行列数一致。
                            // ⚠️ 不能写成 text + '|' + ' |'.repeat(pad) —— 普通单元格
                            // (colspan=1) 时 pad=0，会退化成 text + '|'，给每个格子都
                            // 多插一根竖线，整行列数错乱。
                            const cellTexts = [];
                            cells.forEach(c => {
                                cellTexts.push(
                                    convert(c, depth+1).trim().replace(/\n+/g, ' ').replace(/\s+/g, ' ')
                                );
                                const colspan = parseInt(c.getAttribute('colspan') || '1', 10) || 1;
                                for (let k = 1; k < colspan; k++) cellTexts.push('');
                            });
                            // 表头行已在上方写入，这里只追加数据行
                            if (ri !== 0) md += '| ' + cellTexts.join(' | ') + ' |\n';
                        });
                        return md + '\n';
                    }

                    // 引用与折叠
                    case 'blockquote': {
                        const inner = children().trim();
                        if (!inner) return '';
                        const lines = inner.split('\n');
                        return '\n' + lines.map(l => '> ' + l).join('\n') + '\n\n';
                    }
                    case 'details': {
                        const summary = node.querySelector('summary');
                        const summaryText = summary ? normalizeSpace(summary.innerText).trim() : '';
                        const content = node.innerHTML.replace(/<summary[^>]*>[\s\S]*?<\/summary>/i, '').trim();
                        const tempDiv = document.createElement('div');
                        tempDiv.innerHTML = content;
                        const contentText = convert(tempDiv, depth+1).trim();
                        if (!contentText) return '';
                        return '\n\n<details>\n<summary>' + summaryText + '</summary>\n\n' + contentText + '\n\n</details>\n\n';
                    }
                    case 'summary': return children();

                    // 语义化容器
                    case 'figure': {
                        const figcaption = node.querySelector('figcaption');
                        const figText = figcaption ? normalizeSpace(figcaption.innerText).trim() : '';
                        const img = node.querySelector('img');
                        const imgMd = img ? convert(img, depth+1) : '';
                        return '\n\n' + imgMd + (figText ? '\n*' + figText + '*' : '') + '\n\n';
                    }
                    case 'figcaption': return children();

                    // 视频
                    case 'video': {
                        const src = node.getAttribute('src') || '';
                        const poster = node.getAttribute('poster') || '';
                        if (src) return '\n[视频](' + src + ')\n';
                        if (poster) return '\n[视频封面](' + poster + ')\n';
                        return '';
                    }

                    // 分割线与容器
                    case 'div': case 'section': case 'article': case 'main': {
                        const text = children().trim();
                        return text ? '\n\n' + text + '\n\n' : '';
                    }
                    case 'span': case 'font': case 'small': {
                        return children();
                    }

                    default: return children();
                }
            }

            let md = convert(root, 0);
            
            // 脚注区域（如果有引用的话）
            if (footnotes.size > 0) {
                md += '\n\n---\n\n## 参考资料\n\n';
                footnotes.forEach((text, id) => {
                    md += '[^' + id.replace(/\D/g, '') + ']: ' + text + '\n';
                });
            }

            // 清理多余空行（超过3个连续换行合并为2个）
            md = md.replace(/\n{4,}/g, '\n\n\n').trim();
            return md;
        }
    """)
    return _clean_text(raw or "", max_chars)


def _sync_tabs() -> None:
    """同步 context 中的标签页到 state，捕获新开的标签页。"""
    state["pages"] = list(state["context"].pages)


# ═══════════════════════════════════════════════════════════════
#  MCP 工具定义
# ═══════════════════════════════════════════════════════════════

# ───────────────────────────────
#  1. 搜索（返回链接清单，供 LLM 筛选）
# ───────────────────────────────
@mcp.tool()
async def search(
    query: str,
    site_selector: str = "",
    button_selector: str = "",
) -> str:
    """
    搜索工具：在搜索框输入关键词 → 点击搜索 → 返回前 10 个链接清单。
    本工具只返回链接列表（编号 + 标题 + 一句话摘要），不抓取正文内容。
    LLM 应根据清单判断哪些链接有用，然后用 open_url 工具打开有用的链接读取正文。

    参数：
      query           - 搜索关键词
      site_selector   - 页面内搜索框的 CSS 选择器（如 '#searchInput'）。
                       传入此参数时，在当前页面的搜索框内输入并搜索（站内搜索模式）。
                       不传则默认打开 Google 搜索。
      button_selector - 搜索按钮的 CSS 选择器（如 'button.search-btn'）。
                       与 site_selector 配合使用，为空则按 Enter 提交。

    使用流程：
      1. 调用 search(query="你的问题")  →  得到 10 个链接清单
      2. LLM 判断哪些链接有用
      3. 调用 open_url(url="有用的链接")  →  打开并读取正文

    使用示例：
      search(query="Python 教程")                                   # Google 搜索
      search(query="红石电路", site_selector="#searchInput")        # MC百科站内搜索
      search(query="react", site_selector="input[name='q']", button_selector=".search-btn")
    """
    page = await ensure_browser()

    try:
        # ── 模式 A：页面内搜索（site_selector 模式）
        if site_selector:
            await page.wait_for_selector(site_selector, state="visible", timeout=8000)
            await page.click(site_selector)
            await asyncio.sleep(0.3)
            await page.fill(site_selector, query)
            await asyncio.sleep(0.5)

            if button_selector:
                await page.wait_for_selector(button_selector, state="visible", timeout=5000)
                await page.click(button_selector)
            else:
                await page.keyboard.press("Enter")

            await asyncio.sleep(2.0)
            _sync_tabs()

            # 站内搜索：提取页面上的链接列表
            links = await page.evaluate("""() => {
                return Array.from(document.querySelectorAll('a[href]'))
                    .map(a => {
                        const title = (a.innerText || '').trim();
                        const href = a.href || '';
                        const parent = a.closest('.result, .item, .search-result, .b_algo, li');
                        const snippet = parent
                            ? (parent.querySelector('.desc, .summary, .snippet, .abstract, .b_caption p') || {}).innerText || ''
                            : '';
                        return { title, link: href, snippet: snippet.trim() };
                    })
                    .filter(l => l.link.startsWith('http') && l.title.length > 2 && l.title.length < 100)
                    .slice(0, 10);
            }""")

            if not links:
                title = await page.title()
                return f"🔍 站内搜索：{query}\n⚠️ 未提取到有效链接。当前页面：{title}\n{page.url}"

            output = [f"🔍 站内搜索：{query}", f"{'─'*40}", ""]
            output.append("📋 搜索结果链接清单（请判断哪些有用，用 open_url 打开）：\n")
            for i, item in enumerate(links, 1):
                snippet_line = f"  {item['snippet']}" if item.get("snippet") else ""
                output.append(f"[{i}] {item['title']}")
                output.append(f"    🔗 {item['link']}")
                if snippet_line:
                    output.append(snippet_line)
                output.append("")
            return "\n".join(output)

        # ── 模式 B：Google 搜索（直接 URL 导航，跳过首页）
        encoded_query = quote_plus(query)
        search_url = f"https://www.google.com/search?q={encoded_query}&hl=zh-CN&gl=cn"
        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        # Google 有持续的广告/跟踪请求，永远不会真正 networkidle，改用 domcontentloaded + 固定延迟
        await asyncio.sleep(2.0)

        # 处理可能的 Google 同意页（Consent）
        try:
            consent_btn = await page.query_selector("button[id^='accept'], form[action*='consent'] button")
            if consent_btn:
                await consent_btn.click()
                await asyncio.sleep(0.5)
        except Exception:
            pass

        title = await page.title()
        # Google 验证检测
        if any(kw in title.lower() for kw in ["captcha", "verify", "unusual traffic", "sorry"]):
            return (
                "⚠️ 检测到 Google 验证页面！\n"
                "请查看弹出的浏览器窗口，手动完成验证后重试。\n"
                f"当前页面标题：{title}"
            )

        results = await page.evaluate("""() => {
            // 尝试多种选择器匹配 Google 搜索结果（DOM 结构可能有多个版本）
            let items = [];
            
            // 方法1: 经典 div.g 选择器（较旧版本）
            const method1 = document.querySelectorAll('div.g');
            if (method1.length > 0) {
                items = Array.from(method1);
            }
            if (items.length === 0) {
                // 方法2: 新版 Google 的 div[data-hl] 或 article
                const method2 = document.querySelectorAll('div[data-hl], article');
                if (method2.length > 0) {
                    items = Array.from(method2);
                }
            }
            if (items.length === 0) {
                // 方法3: 查找包含链接的 h3 的父容器
                const h3s = document.querySelectorAll('h3');
                h3s.forEach(h3 => {
                    const parent = h3.closest('div');
                    if (parent && parent.textContent.length > 50) {
                        items.push(parent);
                    }
                });
            }
            
            const results = [];
            const seenLinks = new Set();
            
            items.slice(0, 15).forEach((el, i) => {
                // 查找链接（跳过 Google 内部链接）
                const allLinks = el.querySelectorAll('a[href]');
                let linkEl = null;
                for (const a of allLinks) {
                    const href = a.href;
                    // 跳过 Google 搜索结果的站内链接
                    if (href && !href.includes('google.com') && 
                        !href.includes('accounts.google') &&
                        !href.includes('support.google') &&
                        href.startsWith('http') &&
                        !seenLinks.has(href)) {
                        linkEl = a;
                        seenLinks.add(href);
                        break;
                    }
                }
                
                if (!linkEl) return;
                
                // 获取标题
                const titleEl = el.querySelector('h3') || linkEl;
                const title = titleEl.innerText.trim() || linkEl.innerText.trim();
                
                // 获取摘要 - 尝试多种选择器
                let snippet = '';
                const snippetSelectors = [
                    '[data-sncf]', '[data-content-field]', 
                    '.VwiC3b', '.IsZvec', '.st', '.s',  // 旧版
                    '[data-hl]', '.GBjbYe', '.IZSchuber',  // 新版
                    '[style*="line-clamp"]', 
                    'span[style*="webkit-line-clamp"]',
                    '.mraWU', '.F4eCZf',  // 另一个版本
                ];
                for (const sel of snippetSelectors) {
                    const selEl = el.querySelector(sel);
                    if (selEl && selEl.innerText.trim()) {
                        snippet = selEl.innerText.trim();
                        break;
                    }
                }
                // 回退：取链接后面紧跟的文本
                if (!snippet) {
                    const sibling = linkEl.nextElementSibling;
                    if (sibling) snippet = sibling.innerText.trim().slice(0, 200);
                }
                
                results.push({
                    idx: results.length + 1,
                    title: title.slice(0, 100),
                    link: linkEl.href,
                    snippet: snippet.slice(0, 300)
                });
            });
            
            return results.slice(0, 10);
        }""")

        if not results:
            # 回退：直接从所有链接提取（排除 Google 内部链接）
            results = await page.evaluate("""() => {
                return Array.from(document.querySelectorAll('a[href]'))
                    .filter(a => {
                        const href = a.href;
                        return href && 
                               !href.includes('google.com') &&
                               !href.includes('accounts.google') &&
                               !href.includes('support.google') &&
                               !href.includes('maps.google') &&
                               href.startsWith('http') &&
                               a.innerText.trim().length > 5;
                    })
                    .map(a => ({ 
                        link: a.href, 
                        title: a.innerText.trim().slice(0, 80) 
                    }))
                    .slice(0, 10);
            }""")

        if not results:
            return f"⚠️ 搜索 '{query}' 未返回标准结果。页面标题：{title}"

        output = [f"🔍 Google 搜索：{query}", f"{'─'*40}", ""]
        output.append("📋 搜索结果链接清单（请判断哪些有用，用 open_url 打开）：\n")
        for r in results:
            output.append(f"[{r['idx']}] {r['title']}")
            output.append(f"    🔗 {r['link']}")
            if r.get("snippet"):
                output.append(f"    {r['snippet']}")
            output.append("")
        output.append("💡 下一步：根据上方清单判断哪些链接与你的问题相关，")
        output.append("   然后调用 open_url(url=\"选中的链接\") 打开并读取正文。")

        _sync_tabs()
        return "\n".join(output)

    except Exception as e:
        debug_shot = await _take_debug_screenshot(page)
        return f"❌ 搜索失败：{e}{debug_shot}"


# ───────────────────────────────
#  2. 打开 URL 并读取正文（配合 search 使用）
# ───────────────────────────────
@mcp.tool()
async def open_url(
    url: str,
    wait_seconds: float = 2.5,
    max_chars: int = 5000,
    as_markdown: bool = True,
    auto_scroll: bool = True,
) -> str:
    """
    打开指定 URL 并读取页面正文内容。
    设计用途：在 search 返回链接清单后，LLM 筛选出有用的链接，用本工具直接粘贴 URL 打开并读取。

    参数：
      url          - 目标网址（直接粘贴搜索结果中的链接即可）
      wait_seconds - 页面加载后额外等待秒数（默认 2.5，SPA 页面建议 3-5）
      max_chars    - 正文内容最大字符数（默认 5000）
      as_markdown  - 是否将正文转为 Markdown 格式（默认 True）
      auto_scroll  - 是否自动滚动加载更多内容（默认 True，适合无限滚动页面）

    使用流程：
      1. search(query="你的问题")  →  得到链接清单
      2. LLM 筛选有用链接
      3. open_url(url="选中的链接")  →  读取正文
    """
    page = await ensure_browser()
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(max(0.5, wait_seconds))

        status = resp.status if resp else "unknown"
        final  = page.url
        title  = await page.title()

        if as_markdown:
            content = await _html_to_simple_md(page, max_chars=max_chars, auto_scroll=auto_scroll)
        else:
            content = await _extract_main_content(page, max_chars=max_chars, auto_scroll=auto_scroll)

        return (
            f"🌐 页面已打开\n"
            f"   标题  ：{title}\n"
            f"   最终URL：{final}\n"
            f"   状态码 ：{status}\n"
            f"{'─'*40}\n"
            f"📄 正文内容：\n"
            f"{content}"
        )

    except Exception as e:
        debug_shot = await _take_debug_screenshot(page)
        return f"❌ 打开页面失败：{e}{debug_shot}"


# ───────────────────────────────
#  2. 读取当前页面内容
# ───────────────────────────────
@mcp.tool()
async def read_page(
    max_chars: int = 6000,
    use_raw: bool = False,
    auto_scroll: bool = True,
) -> str:
    """
    读取当前浏览器页面的内容。

    参数：
      max_chars   - 返回内容最大字符数（默认 6000）
      use_raw     - True 返回原始 innerText，False 使用智能提取（默认 False）
      auto_scroll - 是否自动滚动加载更多内容（默认 True）
    """
    page = await ensure_browser()
    try:
        title = await page.title()
        url   = page.url
        if use_raw:
            raw     = await page.evaluate("document.body.innerText")
            content = _clean_text(raw, max_chars)
        else:
            content = await _extract_main_content(page, max_chars, auto_scroll=auto_scroll)
        return (
            f"📄 当前页面\n"
            f"   标题：{title}\n"
            f"   URL ：{url}\n"
            f"{'─'*40}\n"
            f"{content}"
        )
    except Exception as e:
        return f"❌ 读取页面失败：{e}"


# ───────────────────────────────
#  3. 页面交互操作
# ───────────────────────────────
@mcp.tool()
async def page_action(
    action: str,
    selector: str = "",
    text: str = "",
    key: str = "",
    wait_after: float = 1.5,
) -> str:
    """
    在当前页面执行各种交互操作。

    参数：
      action     - 操作类型（见下方）
      selector   - CSS 选择器 或 XPath 表达式（xpath_click 时传入 XPath）
      text       - 填入文本（action=type 时使用）
      key        - 键名（action=press 时使用，如 Enter、Tab、Escape）
      wait_after - 操作后等待秒数（默认 1.5）

    支持的 action：
      click         - 点击元素（CSS 选择器）
      xpath_click   - 用 XPath 定位并点击元素（适合复杂中文页面）
      type          - 清空并填入文本（input/textarea）
      append        - 追加文本（不清空）
      press         - 按下键盘按键
      hover         - 鼠标悬停
      wait_visible  - 等待元素出现（最多 10 秒）
      screenshot    - 截图（存盘到 _debug_shots/ 并返回文件路径）
      get_links     - 获取页面所有链接
      get_inputs    - 获取页面所有可交互输入框信息
    """
    page = await ensure_browser()
    try:
        if action == "click":
            await page.wait_for_selector(selector, state="visible", timeout=8000)
            await page.click(selector)
            await asyncio.sleep(wait_after)

        elif action == "xpath_click":
            xpath_selector = f"xpath={selector}" if not selector.startswith("xpath=") else selector
            handle = await page.wait_for_selector(xpath_selector, state="visible", timeout=8000)
            await handle.click()
            await asyncio.sleep(wait_after)

        elif action == "type":
            await page.wait_for_selector(selector, state="visible", timeout=8000)
            await page.fill(selector, text)
            await asyncio.sleep(0.3)

        elif action == "append":
            await page.wait_for_selector(selector, state="visible", timeout=8000)
            await page.type(selector, text, delay=30)
            await asyncio.sleep(0.3)

        elif action == "press":
            if selector:
                await page.wait_for_selector(selector, state="visible", timeout=5000)
                await page.press(selector, key or "Enter")
            else:
                await page.keyboard.press(key or "Enter")
            await asyncio.sleep(wait_after)

        elif action == "hover":
            await page.wait_for_selector(selector, state="visible", timeout=8000)
            await page.hover(selector)
            await asyncio.sleep(0.5)

        elif action == "wait_visible":
            await page.wait_for_selector(selector, state="visible", timeout=10000)
            return f"✅ 元素已出现：{selector}"

        elif action == "screenshot":
            png_bytes = await page.screenshot(full_page=False)
            title = await page.title()
            fpath = _save_screenshot(png_bytes, prefix="manual")
            return (
                f"📸 截图完成（当前页面：{title}）\n"
                f"图像大小：{len(png_bytes)} 字节\n"
                f"已存至：{fpath}"
            )

        elif action == "get_links":
            links = await page.evaluate("""() =>
                Array.from(document.querySelectorAll('a[href]'))
                    .map(a => ({ text: a.innerText.trim(), href: a.href }))
                    .filter(l => l.href.startsWith('http') && l.text.length > 0)
                    .slice(0, 30)
            """)
            lines = [f"[{i+1}] {l['text']}  →  {l['href']}" for i, l in enumerate(links)]
            return "🔗 页面链接列表：\n" + "\n".join(lines)

        elif action == "get_inputs":
            inputs = await page.evaluate("""() =>
                Array.from(document.querySelectorAll('input, textarea, select, button'))
                    .map(el => ({
                        tag:         el.tagName.toLowerCase(),
                        type:        el.type || '',
                        id:          el.id || '',
                        name:        el.name || '',
                        placeholder: el.placeholder || '',
                        value:       el.value ? el.value.slice(0, 30) : ''
                    }))
                    .filter(el => el.id || el.name || el.placeholder)
                    .slice(0, 20)
            """)
            lines = []
            for el in inputs:
                parts = [f"<{el['tag']}", f"type={el['type']}" if el["type"] else ""]
                if el["id"]:          parts.append(f"id={el['id']}")
                if el["name"]:        parts.append(f"name={el['name']}")
                if el["placeholder"]: parts.append(f"placeholder='{el['placeholder']}'")
                lines.append(" ".join(p for p in parts if p) + ">")
            return "📝 页面输入元素：\n" + "\n".join(lines)

        else:
            return f"❌ 未知操作类型：{action}"

        # 操作完成后返回页面状态
        new_title = await page.title()
        new_url   = page.url
        summary   = await _extract_main_content(page, max_chars=2000)
        return (
            f"✅ 操作 '{action}' 执行成功\n"
            f"   当前标题：{new_title}\n"
            f"   当前 URL ：{new_url}\n"
            f"{'─'*40}\n"
            f"{summary}"
        )

    except Exception as e:
        debug_shot = await _take_debug_screenshot(page)
        return f"❌ 操作 '{action}' 失败：{e}{debug_shot}"


# ───────────────────────────────
#  4. 获取浏览器状态
# ───────────────────────────────
@mcp.tool()
async def get_browser_status() -> str:
    """获取当前浏览器状态：所有标签页列表、当前活跃页信息、滚动位置等。"""
    if state["context"] is None:
        return "🔴 浏览器尚未启动。"

    try:
        lines = ["🌐 浏览器状态", "─" * 40]
        pages = state["pages"]
        lines.append(f"标签页数量：{len(pages)}")

        for i, p in enumerate(pages):
            marker = "▶" if i == state["active_idx"] else " "
            try:
                t = await p.title()
                u = p.url
            except Exception:
                t, u = "（已关闭）", ""
            lines.append(f"  {marker} [{i}] {t}\n        {u}")

        # 活跃页详细信息
        page = _active_page()
        scroll_info = await page.evaluate("""() => ({
            scrollY:      Math.round(window.scrollY),
            scrollHeight: Math.round(document.body.scrollHeight),
            viewHeight:   Math.round(window.innerHeight)
        })""")
        sy = scroll_info["scrollY"]
        sh = scroll_info["scrollHeight"]
        vh = scroll_info["viewHeight"]
        pct = round(sy / max(sh - vh, 1) * 100, 1) if sh > vh else 100
        lines.append(f"\n当前页面滚动位置：{sy}px / {sh}px（{pct}%）")
        return "\n".join(lines)

    except Exception as e:
        return f"❌ 获取状态失败：{e}"


# ───────────────────────────────
#  5. 多标签页管理
# ───────────────────────────────
@mcp.tool()
async def tab_manager(
    action: str,
    url: str = "",
    tab_index: int = -1,
) -> str:
    """
    管理浏览器标签页（Tab）。

    参数：
      action    - 操作类型：open（新开）/ switch（切换）/ close（关闭）/ list（列出）
      url       - 目标 URL（action=open 时使用）
      tab_index - 目标标签页索引（action=switch/close 时使用，-1 表示最后一个）
    """
    ctx = state.get("context")
    if ctx is None:
        await ensure_browser()
        ctx = state["context"]

    try:
        if action == "list":
            lines = []
            for i, p in enumerate(state["pages"]):
                marker = "▶" if i == state["active_idx"] else " "
                try:
                    t = await p.title()
                    u = p.url
                except Exception:
                    t, u = "（已关闭）", "—"
                lines.append(f"{marker} [{i}] {t}  |  {u}")
            return "📑 标签页列表：\n" + "\n".join(lines)

        elif action == "open":
            new_page = await ctx.new_page()
            state["pages"].append(new_page)
            state["active_idx"] = len(state["pages"]) - 1
            if url:
                await new_page.goto(url, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(1.5)
                title = await new_page.title()
                return f"✅ 已打开新标签页 [{state['active_idx']}]：{title}\n{new_page.url}"
            return f"✅ 已打开空白标签页 [{state['active_idx']}]"

        elif action == "switch":
            idx = tab_index if tab_index >= 0 else len(state["pages"]) - 1
            if idx >= len(state["pages"]):
                return f"❌ 标签页索引 {idx} 不存在（共 {len(state['pages'])} 个）"
            state["active_idx"] = idx
            page = _active_page()
            try:
                await page.bring_to_front()
                title = await page.title()
                return f"✅ 已切换至标签页 [{idx}]：{title}\n{page.url}"
            except Exception:
                return f"✅ 已切换至标签页 [{idx}]"

        elif action == "close":
            pages = state["pages"]
            if len(pages) <= 1:
                return "❌ 无法关闭：至少保留一个标签页"
            idx = tab_index if tab_index >= 0 else len(pages) - 1
            if idx >= len(pages):
                return f"❌ 标签页索引 {idx} 不存在"
            page = pages.pop(idx)
            try:
                await page.close()
            except Exception:
                pass
            state["active_idx"] = min(state["active_idx"], len(pages) - 1)
            return f"✅ 已关闭标签页 [{idx}]，当前活跃：[{state['active_idx']}]"

        else:
            return f"❌ 未知 action：{action}"

    except Exception as e:
        return f"❌ 标签页操作失败：{e}"


# ───────────────────────────────
#  6. 执行任意 JavaScript
# ───────────────────────────────
@mcp.tool()
async def run_javascript(script: str) -> str:
    """
    在当前页面执行任意 JavaScript 代码，并返回结果。
    代码会被自动包装进异步 IIFE，因此可以直接写 return 语句，无需手动包装。

    参数：
      script - 要执行的 JavaScript 代码（支持 return、await、异步操作）

    示例：
      "return document.title"
      "return Array.from(document.querySelectorAll('h2')).map(h=>h.innerText)"
      "const data = await fetch('/api').then(r=>r.json()); return data"
    """
    page = await ensure_browser()
    try:
        wrapped_script = f"""
        (async () => {{
            {script}
        }})()
        """
        result = await page.evaluate(wrapped_script)
        if result is None:
            return "✅ 脚本执行完毕（无返回值）"
        return f"✅ 执行结果：\n{result}"
    except Exception as e:
        return f"❌ JavaScript 执行失败：{e}"


# ───────────────────────────────
#  7. 关闭浏览器
# ───────────────────────────────
@mcp.tool()
async def close_browser() -> str:
    """
    关闭浏览器及所有资源，同时自动清除本次的搜索历史 Cookie
    偏好设置会在下次启动时自动重新注入。
    """
    # 与 ensure_browser() 共用同一把锁：避免「一边在关、一边在起」的竞态
    async with _state_lock:
        if state["context"] is None:
            return "ℹ️ 浏览器当前未运行。"
        await _cleanup()
    return "✅ 浏览器已关闭，搜索历史 Cookie 已重置。"


# ───────────────────────────────
#  8. 结构化数据提取：CSS / XPath
# ───────────────────────────────
@mcp.tool()
async def smart_extract(
    selector: str = "body",
    selector_type: str = "css",
    extract_mode: str = "text",
    max_items: int = 50,
    truncate_len: int = 200,
) -> str:
    """
    使用 CSS 或 XPath 提取结构化数据，适合处理 MC百科等复杂中文社区页面。

    参数：
      selector      - CSS 选择器 或 XPath 表达式
      selector_type - 'css'（默认）或 'xpath'
      extract_mode  - 提取模式：
                        'text'    - 提取纯文本列表（默认）
                        'html'    - 提取 innerHTML
                        'attrs'   - 提取元素的所有 HTML 属性
      max_items     - 最多返回条目数（默认 50）
      truncate_len  - 每个条目的最大字符长度（默认 200），防止 Token 爆炸

    XPath 示例：
      //div[contains(text(), '模组列表')]/following-sibling::div[1]
      //table[@class='wikitable']//tr
    """
    page = await ensure_browser()
    try:
        if selector_type == "xpath":
            # 使用 locator API 处理 XPath
            locator = page.locator(f"xpath={selector}")
            count = await locator.count()
            if count == 0:
                return f"⚠️ XPath 未匹配到任何元素：{selector}"
            results = []
            for i in range(min(count, max_items)):
                el = locator.nth(i)
                if extract_mode == "html":
                    val = await el.inner_html()
                    val = val.strip()[:min(truncate_len + 100, 500)]
                elif extract_mode == "attrs":
                    val = await page.evaluate("el => JSON.stringify(Object.fromEntries(Array.from(el.attributes).map(a=>[a.name,a.value])))", el)
                else:
                    val = await el.inner_text()
                    val = val.strip()
                    if len(val) > truncate_len:
                        val = val[:truncate_len] + "…(截断)"
                results.append(f"[{i+1}] {val}")
            return f"📦 XPath 提取结果（共 {len(results)} 条）：\n" + "\n".join(results)

        else:
            data = await page.evaluate(
                """([sel, mode, maxItems, truncateLen]) => {
                    const els = Array.from(document.querySelectorAll(sel)).slice(0, maxItems);
                    if (!els.length) return [];
                    return els.map(el => {
                        if (mode === "html")  return el.innerHTML.trim().slice(0, Math.min(truncateLen + 100, 500));
                        if (mode === "attrs") return JSON.stringify(Object.fromEntries(Array.from(el.attributes).map(a=>[a.name,a.value])));
                        const text = el.innerText.trim();
                        return text.length > truncateLen ? text.slice(0, truncateLen) + '…(截断)' : text;
                    });
                }""",
                [selector, extract_mode, max_items, truncate_len],
            )
            if not data:
                return f"⚠️ CSS 选择器未匹配到元素：{selector}"
            lines = [f"[{i+1}] {item}" for i, item in enumerate(data)]
            return f"📦 CSS 提取结果（共 {len(lines)} 条）：\n" + "\n".join(lines)

    except Exception as e:
        debug_shot = await _take_debug_screenshot(page)
        return f"❌ smart_extract 失败：{e}{debug_shot}"


# ───────────────────────────────
#  9. 一站式智能浏览：导航+等待+大纲+提取
# ───────────────────────────────
@mcp.tool()
async def smart_browse(
    url: str,
    wait_seconds: float = 2.5,
    max_chars: int = 4000,
    with_outline: bool = True,
    as_markdown: bool = True,
    auto_scroll: bool = True,
) -> str:
    """
    一站式智能浏览工具：导航 → 等待加载 → 自动滚动 → 生成大纲 → 提取正文。
    单次调用即可完成页面跳转和内容读取。

    参数：
      url          - 目标网址
      wait_seconds - 页面加载后额外等待秒数（默认 2.5，复杂 SPA 页面建议 3-5）
      max_chars    - 正文内容最大字符数（默认 4000）
      with_outline - 是否同时附带页面大纲（默认 True）
      as_markdown  - 是否将正文转为 Markdown 格式（默认 True，支持 h1→#、a→[text](url) 等转换）
      auto_scroll  - 是否自动滚动加载更多内容（默认 True，适合无限滚动页面）

    返回：
      页面基本信息 + 大纲索引（可选）+ 正文内容（纯文本或 Markdown）
    """
    page = await ensure_browser()
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(max(0.5, wait_seconds))

        status = resp.status if resp else "unknown"
        final  = page.url
        title  = await page.title()

        output_lines = [
            f"🌐 smart_browse 完成",
            f"   标题  ：{title}",
            f"   最终URL：{final}",
            f"   状态码 ：{status}",
            "─" * 40,
        ]

        # ── 附带大纲
        if with_outline:
            outline = await _get_page_outline(page)
            if outline:
                output_lines.append("📑 页面大纲：")
                for item in outline:
                    id_str = f"  #{item['id']}" if item.get("id") else ""
                    output_lines.append(f"  [{item['tag']}] {item['text']}{id_str}")
                output_lines.append("─" * 40)

        # ── 正文内容
        if as_markdown:
            content = await _html_to_simple_md(page, max_chars=max_chars, auto_scroll=auto_scroll)
        else:
            content = await _extract_main_content(page, max_chars=max_chars, auto_scroll=auto_scroll)
        output_lines.append("📄 正文内容：")
        output_lines.append(content)

        return "\n".join(output_lines)

    except Exception as e:
        debug_shot = await _take_debug_screenshot(page)
        return f"❌ smart_browse 失败：{e}{debug_shot}"


# ═══════════════════════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    mcp.run(transport="stdio")
