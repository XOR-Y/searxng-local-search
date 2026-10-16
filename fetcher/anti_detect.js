/* ============================================================
 *  反检测注入脚本 —— 渲染模式（--render）下注入到每个页面
 *
 *  【可自定义】想加自己的伪装，直接在本文件里追加即可；
 *  改完下次 --render 立即生效，**不必改动任何 Python 代码**。
 *
 *  加载方式见 fetcher/browser.py 的 _load_anti_detect()。
 *
 *  ⚠️ 写之前先读这段：**不是伪装得越多越好**。
 *     真实浏览器里这些值是「稳定且自洽」的，逐项用 JS 造假很容易
 *     互相矛盾、反而变成新的指纹。只处理那些「自动化环境确实缺失 /
 *     确实异常」的项，其余交给真实浏览器自己报告。
 * ============================================================ */

/* ① 抹掉 navigator.webdriver
 *    Playwright 默认置为 true，是最显眼的自动化特征，必须处理。 */
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

/* ② 补齐 window.chrome.runtime
 *    真实 Chrome / Edge 都有这个对象，无头环境下可能缺失，补个空的即可。 */
window.chrome = { runtime: {} };

/* ③ 语言列表与 context 的 locale 保持一致
 *    （见 _new_context 的 locale="zh-CN"；不一致本身就是个破绽）。 */
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });

/* ④ 插件列表 —— 真实浏览器非空，无头环境可能为空数组。 */
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });

/* ============================================================
 *  以下两段**已删除**，别再抄回来（2026-10-13）：
 *
 *  ✗ WebGL 伪造 —— 把渲染器报成
 *      'Intel Open Source Technology Center' /
 *      'Mesa DRI Intel(R) HD Graphics 520 (Skylake GT2)'
 *    那是 **Linux 的 Mesa 字符串**，而我们是在 **Windows 上跑 Edge**，
 *    等于自己举手说「我是伪装的」。真实 GPU 字符串交给浏览器报即可。
 *
 *  ✗ Canvas 伪造 —— 给 getImageData 的每个像素随机 ±1
 *    真实浏览器的 canvas 输出是**稳定值**（同一画布每次一样），
 *    每次返回不同值反而是「被篡改」的铁证。
 *
 *  理由同文件顶部那条：逐项 JS 造假会自相矛盾。若要对抗指纹识别，
 *  正确方向是**传输层指纹**（TLS / HTTP2），而不是在 JS 里逐项编造 ——
 *  本机 SearXNG 用的 curl_cffi impersonate 就是干这个的（备选，未采用）。
 * ============================================================ */
