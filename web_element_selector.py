"""
web_element_selector.py
Requires: playwright (pip install playwright && playwright install chromium)
Python 3.12+

打开 pixiv 页面前会尝试注入同目录 pixiv_config.json 中保存的登录 Cookie，
避免元素选择器打开时处于未登录状态。
"""

import asyncio
import os
import json
from typing import Dict
from playwright.async_api import async_playwright, Page


def _load_login_cookies() -> list:
    """从同目录 pixiv_config.json 读取登录 Cookie，转成 playwright 可用格式"""
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pixiv_config.json')
    try:
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception:
        return []
    cleaned = []
    for c in (cfg.get('cookies') or []):
        if not isinstance(c, dict) or not c.get('name') or not c.get('domain'):
            continue
        item = {'name': c['name'], 'value': c.get('value') or '',
                'domain': c['domain'], 'path': c.get('path') or '/'}
        for k in ('httpOnly', 'secure'):
            if c.get(k) is not None:
                item[k] = bool(c[k])
        if c.get('sameSite') in ('Strict', 'Lax', 'None'):
            item['sameSite'] = c['sameSite']
        expires = c.get('expires')
        if isinstance(expires, (int, float)) and expires > 0:
            item['expires'] = int(expires)
        cleaned.append(item)
    return cleaned


class ElementSelector:
    """网页元素选择器，启动 Chrome 并允许用户交互式选择元素。"""

    def __init__(self, target_url: str):
        """
        :param target_url: 要打开的目标网页 URL
        """
        self.target_url = target_url
        self._result: Dict[str, str] | None = None
        self._selection_event = asyncio.Event()

    def select_element(self) -> Dict[str, str]:
        """
        启动浏览器，等待用户选择元素并确认，返回元素的类型和 class。
        :return: 字典，包含 'type' 和 'class' 字段
        """
        return asyncio.run(self._async_select_element())

    async def _async_select_element(self) -> Dict[str, str]:
        async with async_playwright() as p:
            # 启动 Chrome（非无头模式，方便交互）
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context()
            # 注入已保存的 pixiv 登录 Cookie
            cookies = _load_login_cookies()
            if cookies:
                try:
                    await context.add_cookies(cookies)
                except Exception:
                    pass
            page = await context.new_page()
            await page.goto(self.target_url)

            # 注入控制面板
            await self._inject_control_panel(page)

            # 暴露回调函数给 JavaScript，供“确认”时调用
            await page.expose_function("__py_confirm_selection", self._on_confirm)

            # 等待用户完成选择
            await self._selection_event.wait()

            await browser.close()
            if self._result is None:
                raise ValueError("用户未选择元素")
            return self._result

    async def _inject_control_panel(self, page: Page):
        """将控制面板的 HTML/CSS/JS 注入到页面中"""
        await page.add_style_tag(content="""
            #py-selector-panel {
                position: fixed;
                top: 20px;
                right: 20px;
                width: 400px;
                max-height: 80vh;
                background: #2d2d2d;
                color: #eee;
                border-radius: 12px;
                box-shadow: 0 8px 20px rgba(0,0,0,0.5);
                z-index: 2147483647;
                font-family: system-ui, sans-serif;
                font-size: 14px;
                padding: 16px;
                overflow: auto;
                border: 1px solid #555;
            }
            #py-selector-panel button {
                background: #3a6ea5;
                color: white;
                border: none;
                padding: 8px 16px;
                margin-right: 8px;
                border-radius: 6px;
                cursor: pointer;
                font-weight: bold;
            }
            #py-selector-panel button:hover {
                background: #4d8cd4;
            }
            #py-selector-panel button:disabled {
                background: #666;
                cursor: not-allowed;
            }
            #py-selector-panel pre {
                background: #1e1e1e;
                padding: 10px;
                border-radius: 6px;
                overflow-x: auto;
                white-space: pre-wrap;
                word-wrap: break-word;
                max-height: 300px;
                border: 1px solid #444;
                margin-top: 10px;
            }
            #py-selector-status {
                margin-bottom: 12px;
                font-weight: bold;
            }
            .py-error {
                color: #ff8080;
            }
        """)

        await page.add_script_tag(content="""
            (function() {
                // 避免重复注入
                if (window.__pySelectorInstalled) return;
                window.__pySelectorInstalled = true;

                // 状态变量
                let isHooking = false;
                let currentElement = null;
                let clickHandler = null;

                // DOM 元素
                const panel = document.createElement('div');
                panel.id = 'py-selector-panel';
                panel.innerHTML = `
                    <div id="py-selector-status">⚪ 未开始</div>
                    <div>
                        <button id="py-start-hook">🔍 开始 Hook</button>
                        <button id="py-confirm" disabled>✅ 确认</button>
                    </div>
                    <div style="margin-top: 12px;">
                        <div>当前选中元素源码：</div>
                        <pre id="py-source-view">(尚未选择)</pre>
                    </div>
                `;
                document.body.appendChild(panel);

                const statusDiv = document.getElementById('py-selector-status');
                const startBtn = document.getElementById('py-start-hook');
                const confirmBtn = document.getElementById('py-confirm');
                const sourcePre = document.getElementById('py-source-view');

                // 辅助函数：获取元素简洁的 outerHTML（限制长度）
                function getElementSource(el) {
                    if (!el) return '(无)';
                    let html = el.outerHTML;
                    if (html.length > 2000) {
                        html = html.substring(0, 2000) + '\\n... (内容过长，已截断)';
                    }
                    return html;
                }

                // 停止 Hook 状态
                function stopHooking() {
                    if (clickHandler) {
                        document.removeEventListener('click', clickHandler, true);
                        clickHandler = null;
                    }
                    isHooking = false;
                    startBtn.textContent = '🔍 开始 Hook';
                    statusDiv.innerHTML = '⚪ 未 Hook';
                }

                // 开始 Hook 监听
                function startHooking() {
                    if (isHooking) {
                        stopHooking();
                        return;
                    }

                    clickHandler = function(e) {
                        // 🛡️ 如果点击的是我们的控制面板内部，则忽略
                        const panel = document.getElementById('py-selector-panel');
                        if (panel && panel.contains(e.target)) {
                            return;  // 面板内的点击不触发元素选择
                        }

                        e.stopPropagation();
                        e.preventDefault();

                        currentElement = e.target;
                        const source = getElementSource(currentElement);
                        sourcePre.textContent = source;

                        const tag = currentElement.tagName.toLowerCase();
                        const cls = currentElement.className || '';
                        statusDiv.innerHTML = `🎯 已选中: &lt;${tag}&gt;  class="${cls}"`;

                        confirmBtn.disabled = false;
                    };

                    document.addEventListener('click', clickHandler, true);
                    isHooking = true;
                    startBtn.textContent = '⏹ 停止 Hook';
                    statusDiv.innerHTML = '👆 请点击页面上的任意元素...';
                    confirmBtn.disabled = true;
                    currentElement = null;
                    sourcePre.textContent = '(点击元素后显示源码)';
                }

                // 确认选择，将数据发送给 Python
                async function confirmSelection() {
                    if (!currentElement) {
                        alert('请先点击选择一个元素');
                        return;
                    }

                    const tag = currentElement.tagName.toLowerCase();
                    const cls = currentElement.className || '';

                    // 停止 hook
                    stopHooking();
                    statusDiv.innerHTML = `✅ 已确认: &lt;${tag}&gt;  class="${cls}"`;
                    startBtn.disabled = true;
                    confirmBtn.disabled = true;

                    // 调用 Python 暴露的回调
                    try {
                        await window.__py_confirm_selection(tag, cls);
                    } catch (err) {
                        console.error('回调 Python 失败:', err);
                        statusDiv.innerHTML += '<br/><span class="py-error">❌ 与后端通信失败</span>';
                    }
                }

                // 绑定按钮事件
                startBtn.addEventListener('click', startHooking);
                confirmBtn.addEventListener('click', confirmSelection);

                // 如果用户想重新开始，可以再次点击开始 Hook（会先停止旧的）
            })();
        """)

    async def _on_confirm(self, tag: str, cls: str):
        """JavaScript 回调：接收到确认的元素信息"""
        self._result = {
            "type": tag,
            "class": cls
        }
        self._selection_event.set()


if __name__ == "__main__":
    selector = ElementSelector("https://www.pixiv.net/artworks/143473021")
    result = selector.select_element()
    print("选中的元素信息:", result)
    # result示例: {'type': 'button', 'class': 'sc-f8e29b57-0 gQrBQw'}