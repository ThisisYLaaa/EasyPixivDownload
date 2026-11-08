"""
pixiv_gui.py
Pixiv 图片下载器 v2 - Qt UI 版本，集成 Web 元素选择器 与 登录状态保存/恢复
Python 3.12+
配置文件：工作区根目录 pixiv_config.json（URL / 选择器 / 并发数 / 登录 Cookie）
"""

import sys
import os
import re
import time
import json
import threading
import subprocess
from typing import Dict, Optional, List
from contextlib import redirect_stdout
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QProgressBar,
    QGroupBox, QFormLayout, QMessageBox
)
from PySide6.QtCore import Qt, Signal, QObject, QThread, Slot
from PySide6.QtGui import QFont, QTextCursor

import DrissionPage as Dp
from playwright.sync_api import sync_playwright

# ==================== 全局配置（工作区根目录） ====================
# 一律以本文件所在目录为基准，避免从其它目录启动时找不到配置/下载目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, 'pixiv_config.json')          # v2 统一配置文件
DOWNLOAD_DIR = os.path.join(BASE_DIR, 'download')                  # 下载输出目录
PIXIV_LOGIN_URL = 'https://accounts.pixiv.net/login?return_to=' \
                  'https%3A%2F%2Fwww.pixiv.net%2F'

# 保存 Cookie 时只保留这些字段（CDP 返回的其它字段回填时会报错）
COOKIE_SAVE_KEYS = ('name', 'value', 'domain', 'path', 'expires',
                    'httpOnly', 'secure', 'sameSite', 'priority')


def is_pixiv_cookie(cookie: dict) -> bool:
    """只关心 pixiv 相关域名下的 Cookie"""
    domain = cookie.get('domain') or ''
    return 'pixiv.net' in domain or 'pximg.net' in domain


def read_app_config() -> dict:
    """读取 pixiv_config.json；文件不存在或损坏时返回空 dict"""
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def write_app_config(cfg: dict) -> bool:
    """把配置（含登录 Cookie）写入 pixiv_config.json"""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


# ---------- 智能识别（不依赖死板的 CSS 类名） ----------
# 收集页面所有 i.pximg.net 链接：兼容懒加载（data-src / srcset / 背景图）等写法
PIXIV_COLLECT_JS = '''(() => {
  const urls = new Set();
  const add = u => { if (u && u.indexOf('i.pximg.net') >= 0) urls.add(u); };
  const fromSet = v => { if (!v) return; v.split(',').forEach(x => add(x.trim().split(' ')[0])); };
  const fromAttrs = el => {
    fromSet(el.getAttribute('srcset'));
    fromSet(el.getAttribute('data-srcset'));
    ['src', 'data-src', 'data-original', 'data-lazy-src', 'data-image', 'href']
      .forEach(k => { const v = el.getAttribute && el.getAttribute(k); if (v) add(v); });
    const st = el.getAttribute && el.getAttribute('style');
    if (st) { const m = st.match(/url\\(["']?([^"')]+)["']?\\)/); if (m) add(m[1]); }
  };
  document.querySelectorAll('a[href]').forEach(a => fromAttrs(a));
  document.querySelectorAll('img').forEach(img => { add(img.currentSrc || img.src); fromAttrs(img); });
  document.querySelectorAll('source').forEach(s => fromAttrs(s));
  document.querySelectorAll('div[style]').forEach(d => fromAttrs(d));
  return Array.from(urls);
})()'''

# 自动点“查看更多/展开全部”类按钮（只点 button，不点 <a>，避免跳到其它页面）
PIXIV_CLICK_MORE_JS = '''(() => {
  const keys = ['查看更多', '查看全部', 'view more', 'show more', 'more',
                '続きを見る', 'もっと見る', 'すべて見る'];
  const norm = s => (s || '').replace(/\\s+/g, '').toLowerCase();
  const els = document.querySelectorAll('button, [role="button"]');
  for (const el of els) {
    const t = norm(el.innerText);
    if (!t || t.length > 24) continue;
    if (keys.some(k => t === norm(k) || t.startsWith(norm(k)))) {
      el.click();
      return true;
    }
  }
  return false;
})()'''

PIXIV_TITLE_JS = '''(() => {
  const el = document.querySelector('meta[property="og:title"]');
  if (el && el.content) return el.content;
  return document.title || '';
})()'''


def _to_original_image_url(url: str) -> str:
    """把 i.pximg.net 的缩略图/master 图 URL 转成原图 URL；
    已经是 img-original 的则原样返回；无法识别时返回原 URL。"""
    if not url or 'i.pximg.net' not in url:
        return url
    if '/img-original/' in url:
        return url
    m = re.search(r'(https?://i\.pximg\.net/)(?:c/[^/]+/)?img-master/(img/.+)$', url)
    if not m:
        return url
    tail = m.group(2)  # 形如 img/2023/06/01/12/00/105000000_p0_master1200.jpg
    # 去掉尺寸后缀（master1200/square1200/…），保留 {id}_p{页码}
    tail2 = re.sub(r'_(?:master1200|master|square1200|standard_square0|square0|thumb\w*|mobile)$',
                   '', tail, count=1, flags=re.IGNORECASE)
    if tail2 == tail:  # 未能按已知词命中 → 去掉扩展名前的最后一个下划线段
        tail2 = re.sub(r'_[^_/]+(?=\.(?:jpe?g|png|gif|webp|zip)$)', '', tail, count=1, flags=re.IGNORECASE)
    return m.group(1) + 'img-original/' + tail2


def _extract_artwork_id(url: str) -> Optional[str]:
    m = re.search(r'artworks/(\d+)', url)
    if m:
        return m.group(1)
    m = re.search(r'illust_id=(\d+)', url)
    return m.group(1) if m else None


def _clean_title(text: str) -> str:
    """清理网页标题：去掉 ' - pixiv' 之类的站点后缀"""
    text = (text or '').strip()
    text = re.sub(r'\s*[-–—]\s*pixiv\s*$', '', text, flags=re.IGNORECASE)
    return text.strip() or 'untitled'


def _safe_dir_name(name: str) -> str:
    name = re.sub(r'[\\\\/:*?"<>|]', '_', (name or '').strip())
    return name.strip(' .') or 'untitled'


def config_cookie_header() -> str:
    """把配置里的 Cookie 拼成 HTTP 请求头字符串 'a=b; c=d'"""
    cfg = read_app_config()
    pairs = []
    for c in (cfg.get('cookies') or []):
        if isinstance(c, dict) and c.get('name') and c.get('value') is not None:
            pairs.append(f"{c['name']}={c['value']}")
    return '; '.join(pairs)


def cookies_for_playwright() -> list:
    """把配置里的 Cookie 转成 playwright add_cookies() 可用的格式"""
    cleaned = []
    cfg = read_app_config()
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
            item['expires'] = int(expires)  # 会话级 Cookie 省略该字段
        cleaned.append(item)
    return cleaned

# ==================== Web 元素选择器（同步版） ====================
class ElementSelector:
    """网页元素选择器，启动 Chrome 并允许用户交互式选择元素。"""

    def __init__(self, target_url: str, headless: bool = False):
        self.target_url = target_url
        self.headless = headless
        self._result: Optional[Dict[str, str]] = None

    def select_element(self) -> Dict[str, str]:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context()
            # 注入已保存的 pixiv 登录 Cookie，避免元素选择器打开时处于未登录状态
            cookies = cookies_for_playwright()
            if cookies:
                try:
                    context.add_cookies(cookies)
                except Exception:
                    pass  # Cookie 无效/过期时忽略，仍以未登录方式打开
            page = context.new_page()
            page.goto(self.target_url)

            self._inject_control_panel(page)

            result_holder: Dict[str, str] = {}

            def on_confirm(tag: str, cls: str) -> None:
                result_holder['type'] = tag
                result_holder['class'] = cls

            page.expose_function("__py_confirm_selection", on_confirm)

            # 等待用户操作
            while not result_holder:
                try:
                    page.wait_for_timeout(500)
                except:
                    break

            browser.close()
            return result_holder

    def _inject_control_panel(self, page) -> None:
        """注入控制面板 HTML/CSS/JS"""
        page.add_style_tag(content="""
            #py-selector-panel {
                position: fixed; top: 20px; right: 20px; width: 400px;
                max-height: 80vh; background: #2d2d2d; color: #eee;
                border-radius: 12px; box-shadow: 0 8px 20px rgba(0,0,0,0.5);
                z-index: 2147483647; font-family: system-ui, sans-serif;
                font-size: 14px; padding: 16px; overflow: auto;
                border: 1px solid #555;
            }
            #py-selector-panel button {
                background: #3a6ea5; color: white; border: none;
                padding: 8px 16px; margin-right: 8px; border-radius: 6px;
                cursor: pointer; font-weight: bold;
            }
            #py-selector-panel button:hover { background: #4d8cd4; }
            #py-selector-panel button:disabled {
                background: #666; cursor: not-allowed;
            }
            #py-selector-panel pre {
                background: #1e1e1e; padding: 10px; border-radius: 6px;
                overflow-x: auto; white-space: pre-wrap; word-wrap: break-word;
                max-height: 300px; border: 1px solid #444; margin-top: 10px;
            }
            #py-selector-status { margin-bottom: 12px; font-weight: bold; }
            .py-error { color: #ff8080; }
        """)

        page.add_script_tag(content="""
            (function() {
                if (window.__pySelectorInstalled) return;
                window.__pySelectorInstalled = true;

                let isHooking = false;
                let currentElement = null;
                let clickHandler = null;

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

                function getElementSource(el) {
                    if (!el) return '(无)';
                    let html = el.outerHTML;
                    if (html.length > 2000) {
                        html = html.substring(0, 2000) + '\\n... (已截断)';
                    }
                    return html;
                }

                function stopHooking() {
                    if (clickHandler) {
                        document.removeEventListener('click', clickHandler, true);
                        clickHandler = null;
                    }
                    isHooking = false;
                    startBtn.textContent = '🔍 开始 Hook';
                    statusDiv.innerHTML = '⚪ 未 Hook';
                }

                function startHooking() {
                    if (isHooking) {
                        stopHooking();
                        return;
                    }

                    clickHandler = function(e) {
                        if (panel.contains(e.target)) return;
                        e.stopPropagation();
                        e.preventDefault();

                        currentElement = e.target;
                        sourcePre.textContent = getElementSource(currentElement);

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

                async function confirmSelection() {
                    if (!currentElement) {
                        alert('请先点击选择一个元素');
                        return;
                    }
                    const tag = currentElement.tagName.toLowerCase();
                    const cls = currentElement.className || '';
                    stopHooking();
                    statusDiv.innerHTML = `✅ 已确认: &lt;${tag}&gt;  class="${cls}"`;
                    startBtn.disabled = true;
                    confirmBtn.disabled = true;

                    try {
                        await window.__py_confirm_selection(tag, cls);
                    } catch (err) {
                        console.error('回调失败:', err);
                        statusDiv.innerHTML += '<br/><span class="py-error">❌ 通信失败</span>';
                    }
                }

                startBtn.addEventListener('click', startHooking);
                confirmBtn.addEventListener('click', confirmSelection);
            })();
        """)


# ==================== Pixiv 下载逻辑 ====================
class DownloaderSignals(QObject):
    log = Signal(str)
    progress = Signal(int, int)
    finished = Signal(str)
    error = Signal(str)
    need_login = Signal(str)  # 需要用户手动登录时发出（由主线程弹窗提示）


class PixivDownloader:
    """Pixiv 图片下载核心类（在 QThread 工作线程中运行）"""

    LOGIN_CHECK_SELECTOR = 'css:div.sc-860d04ea-1.fGipbA'  # 出现即代表当前处于未登录/需要登录状态

    def __init__(self, signals: DownloaderSignals):
        self.signals = signals
        cfg = read_app_config()
        try:
            self.thread_count = max(1, int(cfg.get('thread_count') or 5))
        except (TypeError, ValueError):
            self.thread_count = 5
        self.page: Optional[Dp.ChromiumPage] = None
        # 与主线程协作的登录等待事件（由 GUI 在启动任务前设置）
        self._login_event = getattr(signals, 'login_event', None)

    # ---------- 基础操作 ----------
    def log(self, msg: str) -> None:
        self.signals.log.emit(msg)

    def _open_browser(self) -> None:
        self.log('正在启动浏览器…')
        self.page = Dp.ChromiumPage()

    def _need_login(self) -> bool:
        try:
            return bool(self.page.ele(self.LOGIN_CHECK_SELECTOR, timeout=5))
        except Exception:
            return False

    def _prompt_login(self, message: str) -> None:
        """请求用户手动登录：
        弹窗必须在主线程显示（工作线程里弹模态框会导致界面假死），
        因此只发信号让主线程弹窗，本线程用事件等待用户点击“确定”。"""
        if self._login_event is None:
            self.log('请在浏览器窗口中完成登录。')
            return
        self._login_event.clear()
        self.signals.need_login.emit(message)
        self._login_event.wait()  # 主线程弹窗关闭后会 set 该事件

    # ---------- 登录状态（Cookie）的保存与恢复 ----------
    def _collect_login_cookies(self) -> List[dict]:
        """抓取当前浏览器里 pixiv 相关的未过期 Cookie（仅保留可回填的字段）"""
        now = time.time()
        result: List[dict] = []
        for c in self.page.cookies(all_domains=True, all_info=True):
            if not is_pixiv_cookie(c):
                continue
            expires = c.get('expires')
            if isinstance(expires, (int, float)) and 0 < expires < now:
                continue  # 已过期的不保存
            keep = {k: c[k] for k in COOKIE_SAVE_KEYS if k in c}
            if keep.get('name') and keep.get('value') is not None:
                result.append(keep)
        return result

    def save_login(self) -> bool:
        """把当前浏览器的 pixiv Cookie 写入 pixiv_config.json，返回是否成功"""
        try:
            cookies = self._collect_login_cookies()
        except Exception as e:
            self.log(f'❌ 读取浏览器 Cookie 失败: {e}')
            return False
        if not cookies:
            self.log('⚠️ 未获取到 Pixiv 相关 Cookie，可能尚未真正登录')
            return False
        cfg = read_app_config()
        cfg['cookies'] = cookies
        cfg['cookies_saved_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
        if not write_app_config(cfg):
            self.log(f'❌ 写入配置文件失败: {CONFIG_FILE}')
            return False
        self.log(f'💾 登录状态已保存（{len(cookies)} 条 Cookie）→ pixiv_config.json')
        return True

    def restore_login(self) -> bool:
        """浏览器启动后，用 pixiv_config.json 里的 Cookie 恢复登录状态"""
        cfg = read_app_config()
        cookies = cfg.get('cookies') or []
        if not cookies:
            self.log('ℹ️ 配置文件中没有已保存的登录状态（首次使用请先登录一次）')
            return False
        now = time.time()
        valid = []
        for c in cookies:
            if not isinstance(c, dict) or not c.get('domain'):
                continue
            expires = c.get('expires')
            if isinstance(expires, (int, float)) and 0 < expires < now:
                continue  # 已过期的不恢复
            valid.append(c)
        if not valid:
            self.log('⚠️ 已保存的登录状态已过期，请重新登录一次')
            return False
        try:
            self.page.set.cookies(valid)
            saved_at = cfg.get('cookies_saved_at') or ''
            tail = f'（保存于 {saved_at}）' if saved_at else ''
            self.log(f'🔑 已恢复登录状态：{len(valid)} 条 Cookie{tail}')
            return True
        except Exception as e:
            self.log(f'❌ 恢复登录状态失败: {e}')
            return False

    # ---------- 仅登录：登录并保存登录状态，不下载 ----------
    def run_login(self) -> None:
        try:
            self._open_browser()
            self.restore_login()
            self.log('正在打开 Pixiv 登录页…')
            self.page.get(PIXIV_LOGIN_URL)
            self._prompt_login(
                '请在浏览器窗口中完成登录。\n'
                '登录成功后点击“确定”，程序会自动保存登录状态。'
            )
            self.log('正在打开 Pixiv 首页以确认登录状态…')
            self.page.get('https://www.pixiv.net/')
            self.save_login()
            self.log('✅ 登录流程结束。')
            self.signals.finished.emit('login')
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            if self.page:
                self.page.close()

    # ---------- 下载作品（智能识别主流程） ----------
    def run_download(
        self,
        url: str,
        expand_selector: str,
        pic_selector: str,
        title_selector: str
    ) -> None:
        """下载作品：优先智能识别（自动点“查看更多”+自动收集图片），
        失败时若配置了 CSS 选择器则退回选择器方式。"""
        try:
            self._open_browser()
            restored = self.restore_login()
            self.log(f'正在打开页面: {url}')
            self.page.get(url)

            # 检查登录：若页面提示需要登录，则等用户手动登录后保存状态
            if self._need_login():
                if restored:
                    self.log('⚠️ 已保存的登录状态已失效或权限不足，请重新登录。')
                self.log('🔐 需要登录，请在浏览器中完成…')
                self._prompt_login(
                    '作品需要登录后才能查看（R18 等）。\n'
                    '请在浏览器窗口中完成登录，登录成功后点击“确定”，'
                    '程序会自动保存登录状态并继续下载。'
                )
                self.log('登录完成，正在重新加载页面…')
                self.page.get(url)
                self.save_login()
            else:
                self.log('✅ 已处于登录状态，无需登录。')

            # ---- 智能识别：自动展开 + 自动收集图片（无需配置选择器） ----
            title, img_urls = self._collect_smart(url)
            if img_urls:
                self._perform_download(title, img_urls)
                return

            # ---- 兜底：使用用户配置的 CSS 选择器 ----
            if pic_selector and expand_selector:
                self.log('⚠️ 智能识别未获取到图片，改用已配置的选择器方式…')
                self._download_via_selectors(expand_selector, pic_selector, title_selector)
                return

            raise RuntimeError(
                '未能识别到任何图片。常见原因：\n'
                '1) 未登录或登录已失效（请先点“🔐 登录 Pixiv”并确认已登录）；\n'
                '2) 作品已被删除 / 不公开 / 需要关注或 R18 认证；\n'
                '3) 页面加载较慢。\n'
                '仍然失败时，可用“选取图片链接”等按钮手动配置选择器。'
            )

        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            if self.page:
                self.page.close()

    # ---------- 智能识别：自动展开并收集原图链接 ----------
    def _try_click_view_more(self) -> bool:
        """按文字自动点击“查看更多”类按钮（只点 button，避免跳到其它页面）"""
        try:
            return bool(self.page.run_js(PIXIV_CLICK_MORE_JS))
        except Exception:
            return False

    def _scroll_to_load(self) -> None:
        """缓慢向下滚动到底再回顶，触发图片懒加载"""
        try:
            for _ in range(12):
                self.page.run_js('window.scrollBy(0, 900)')
                time.sleep(0.12)
            self.page.run_js('window.scrollTo(0, 0)')
        except Exception:
            pass

    def _collect_dom_image_urls(self) -> List[str]:
        try:
            raw = self.page.run_js(PIXIV_COLLECT_JS) or []
        except Exception:
            raw = []
        return [u for u in raw if isinstance(u, str) and 'i.pximg.net' in u]

    def _read_page_title(self) -> str:
        try:
            return _clean_title(self.page.run_js(PIXIV_TITLE_JS))
        except Exception:
            return 'untitled'

    def _organize_original_urls(self, raw_urls: List[str]) -> List[str]:
        """去重 + master 转原图 + 按页码排序"""
        seen = set()
        out: List[str] = []
        for u in raw_urls:
            orig = _to_original_image_url(u)
            if not orig or orig in seen:
                continue
            seen.add(orig)
            out.append(orig)

        def page_key(u: str):
            m = re.search(r'_p(\d+)(?=\.|$)', u)
            return (0, int(m.group(1))) if m else (1, 0)

        out.sort(key=page_key)
        return out

    def _collect_smart(self, url: str):
        """智能识别图片：返回 (标题, 原图URL列表)。
        优先用 Pixiv 官方 AJAX 接口（已登录时稳定返回全部页码原图，
        不受前端懒加载/改版影响）；失败再退回页面 DOM 自动识别。"""
        # ---- 主：官方 /ajax/illust/{id} 元数据接口 ----
        meta = self._fetch_illust_metadata(url)
        if meta:
            meta_title, meta_urls = meta
            self.log(f'📄 官方接口返回 {len(meta_urls)} 张原图')
            return meta_title, meta_urls

        # ---- 兜底：DOM 自动识别（展开“查看更多” + 懒加载后收集） ----
        self.log('官方接口不可用，改用页面 DOM 智能识别：展开“查看更多”并收集图片…')
        raw_urls: List[str] = []
        for _ in range(3):
            clicked = self._try_click_view_more()
            self._scroll_to_load()
            raw_urls = self._collect_dom_image_urls()
            if raw_urls:
                break
            if not clicked:
                break
            time.sleep(0.5)

        title = self._read_page_title()
        if raw_urls:
            urls = self._organize_original_urls(raw_urls)
            self.log(f'📄 从页面自动识别到 {len(urls)} 张图片')
            if urls:
                return title, urls
        return title, []

    def _fetch_illust_metadata(self, url: str):
        """用已保存的登录 Cookie 请求 Pixiv 官方接口，返回 (标题, 原图URL列表) 或 None。

        两个接口配合：
        1) /ajax/illust/{id}           —— 标题、（单页作品时）封面原图
        2) /ajax/illust/{id}/pages     —— 多页漫画/画集的每页原图（关键！）
        """
        art_id = _extract_artwork_id(url)
        if not art_id:
            self.log('无法从 URL 解析作品 ID')
            return None
        cookie_header = config_cookie_header()
        if not cookie_header:
            self.log('配置文件中没有登录 Cookie，官方接口无法使用')
            return None
        headers = {
            'Referer': f'https://www.pixiv.net/artworks/{art_id}',
            'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                           'AppleWebKit/537.36 (KHTML, like Gecko) '
                           'Chrome/131.0.0.0 Safari/537.36'),
            'Cookie': cookie_header,
            'Accept': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
        }

        def _get(ep_url):
            try:
                import requests
                resp = requests.get(ep_url, headers=headers, timeout=20)
                return resp.json() or {}
            except Exception as e:
                self.log(f'❌ 官方接口请求失败: {e}')
                return None

        # 1) 作品信息：标题 + （单页作品/兜底）原图
        info = _get(f'https://www.pixiv.net/ajax/illust/{art_id}')
        if info is None:
            return None
        info_body = info.get('body') or {}
        if not info_body:
            self.log('⚠️ 官方接口未返回数据（可能未登录或作品不可见）')
            return None
        title = _clean_title(info_body.get('title') or '')
        page_count = info_body.get('pageCount') or 0

        # 2) 页码列表：多页漫画/画集的每页原图都从这里来
        urls: List[str] = []
        if page_count > 1:
            pages_data = _get(f'https://www.pixiv.net/ajax/illust/{art_id}/pages')
            if pages_data is not None and isinstance(pages_data.get('body'), list):
                for p in pages_data['body']:
                    u = ((p.get('urls') or {}).get('original')
                         or (p.get('urls') or {}).get('regular'))
                    if u:
                        urls.append(u)

        # 3) 兜底：单页作品直接用封面原图
        if not urls:
            u = ((info_body.get('urls') or {}).get('original')
                 or info_body.get('originalImageUrl'))
            if u:
                urls.append(u)

        if not urls:
            self.log('⚠️ 官方接口未返回图片地址')
            return None
        return title, urls

    # ---------- 下载执行 ----------
    def _perform_download(self, title: str, img_urls: List[str]) -> None:
        title_safe = _safe_dir_name(title) if title else 'untitled'
        save_dir = os.path.join(DOWNLOAD_DIR, title_safe)
        os.makedirs(save_dir, exist_ok=True)
        self.page.set.download_path(save_dir)

        total = len(img_urls)
        completed = 0
        start_time = time.time()
        self.log(f'开始下载，共 {total} 张图片 → {save_dir}')

        with ThreadPoolExecutor(max_workers=self.thread_count) as executor:
            futures = [executor.submit(self._download_one, u) for u in img_urls]
            for _ in as_completed(futures):
                completed += 1
                self.signals.progress.emit(completed, total)

        elapsed = time.time() - start_time
        self.log(f'✅ 下载完成！共 {total} 张，耗时 {elapsed:.1f} 秒，保存至 {save_dir}')

        # 打开保存目录
        if sys.platform == 'win32':
            subprocess.run(['explorer', save_dir])
        elif sys.platform == 'darwin':
            subprocess.run(['open', save_dir])
        else:
            subprocess.run(['xdg-open', save_dir])

        self.signals.finished.emit(save_dir)

    # ---------- 兜底：使用手动配置的 CSS 选择器 ----------
    def _download_via_selectors(
        self,
        expand_selector: str,
        pic_selector: str,
        title_selector: str
    ) -> None:
        if expand_selector:
            btn = self.page.ele(expand_selector)
            if btn:
                self.log('找到展开按钮，正在点击…')
                btn.click()
                time.sleep(1)
            else:
                self.log('未找到展开按钮，已跳过。')

        pic_elements = self.page.eles(pic_selector)
        href_list: List[str] = []
        for el in pic_elements:
            href = el.attr('href')
            if href:
                href_list.append(href)
        self.log(f'选择器提取到 {len(href_list)} 个图片链接')

        if not href_list:
            raise RuntimeError('选择器未找到图片链接，可能已随网页改版失效。请用“选取图片链接”重新点选。')

        title = 'untitled'
        if title_selector:
            title_ele = self.page.ele(title_selector)
            if title_ele and title_ele.text:
                title = _safe_dir_name(str(title_ele.text))
        else:
            title = self._read_page_title()
        self.log(f'作品标题: {title}')
        self._perform_download(title, href_list)

    def _download_one(self, url: str) -> None:
        try:
            # 直接用 URL 文件名（如 105000000_p0.jpg）
            filename = url.split('/')[-1].split('?')[0]
            if not filename or '.' not in filename or len(filename) > 120:
                filename = f"{int(time.time() * 1000)}.jpg"
            self.page.set.download_file_name(filename)
            with redirect_stdout(open(os.devnull, 'w', encoding='utf-8')):
                self.page.download(url)
        except Exception as e:
            self.log(f'下载失败: {e}')


# ==================== Qt UI 组件 ====================
class SelectorButton(QPushButton):
    selector_changed = Signal(str)

    def __init__(self, text: str, parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        self.selector = ""

    def set_selector(self, selector: str) -> None:
        self.selector = selector
        self.selector_changed.emit(selector)


class MainWindow(QMainWindow):
    # 信号定义（必须是类属性）
    log_signal = Signal(str)
    progress_signal = Signal(int, int)
    finished_signal = Signal(str)
    error_signal = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pixiv 图片下载器")
        self.resize(700, 600)

        self.selector_thread: Optional[QThread] = None
        self.current_selector_target: Optional[SelectorButton] = None
        self.download_thread: Optional[QThread] = None
        self.login_thread: Optional[QThread] = None

        self.setup_ui()
        self.setup_signals()
        self.load_config()

    def load_config(self) -> None:
        """从 pixiv_config.json 加载上次保存的 URL / 选择器"""
        cfg = read_app_config()
        if not cfg:
            self.append_log("未找到配置文件，首次使用请先输入 URL 并选取元素。")
            self.refresh_login_state_label()
            return
        url = cfg.get('url', '')
        if url:
            self.url_edit.setText(url)
        expand_sel = cfg.get('expand_selector', '')
        if expand_sel:
            self.expand_btn.set_selector(expand_sel)
            self.expand_label.setText(expand_sel)
        pic_sel = cfg.get('pic_selector', '')
        if pic_sel:
            self.pic_btn.set_selector(pic_sel)
            self.pic_label.setText(pic_sel)
        title_sel = cfg.get('title_selector', '')
        if title_sel:
            self.title_btn.set_selector(title_sel)
            self.title_label.setText(title_sel)
        self.append_log(f"已加载配置：{os.path.basename(CONFIG_FILE)}")
        self.refresh_login_state_label()

    def save_config(self) -> None:
        """把当前界面上的 URL / 选择器写入 pixiv_config.json（保留登录 Cookie 等其它字段）"""
        cfg = read_app_config()
        cfg.update({
            'url': self.url_edit.text().strip(),
            'expand_selector': self.expand_btn.selector,
            'pic_selector': self.pic_btn.selector,
            'title_selector': self.title_btn.selector,
        })
        if not write_app_config(cfg):
            self.append_log(f"❌ 保存配置失败: {CONFIG_FILE}")

    def refresh_login_state_label(self) -> None:
        """根据 pixiv_config.json 里的 Cookie 更新界面上的登录状态提示"""
        cfg = read_app_config()
        cookies = cfg.get('cookies') or []
        valid = [c for c in cookies
                 if isinstance(c, dict) and c.get('domain') and c.get('name')]
        if valid:
            saved_at = cfg.get('cookies_saved_at') or ''
            self.login_state_label.setText(
                f'已保存登录状态：{len(valid)} 条 Cookie' + (f'（{saved_at}）' if saved_at else ''))
        else:
            self.login_state_label.setText('未保存登录状态（请先“登录 Pixiv”一次）')

    def setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        title_label = QLabel("Pixiv 作品下载工具")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)

        # URL 输入
        url_layout = QHBoxLayout()
        url_layout.addWidget(QLabel("作品 URL:"))
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://www.pixiv.net/artworks/...")
        url_layout.addWidget(self.url_edit)
        layout.addLayout(url_layout)

        # 登录状态行：登录并保存 / 显示当前登录状态
        login_layout = QHBoxLayout()
        self.login_btn = QPushButton("🔐 登录 Pixiv（保存登录状态）")
        self.login_btn.setToolTip(
            "打开浏览器登录 Pixiv，登录成功后把会话(Cookie)保存到工作区的 pixiv_config.json，"
            "之后下载 R18 等作品无需重复登录"
        )
        self.login_state_label = QLabel('')
        self.login_state_label.setStyleSheet('color: #777;')
        login_layout.addWidget(self.login_btn)
        login_layout.addWidget(self.login_state_label, 1)
        layout.addLayout(login_layout)

        # 选择器配置组
        selector_group = QGroupBox("元素选择器配置（可选 · 智能识别失败时才需要）")
        form = QFormLayout(selector_group)

        self.expand_btn = SelectorButton("选取展开按钮")
        self.expand_label = QLabel("未选择")
        self.expand_label.setStyleSheet("color: gray;")
        expand_layout = QHBoxLayout()
        expand_layout.addWidget(self.expand_btn)
        expand_layout.addWidget(self.expand_label)
        expand_layout.addStretch()
        form.addRow("展开按钮:", expand_layout)

        self.pic_btn = SelectorButton("选取图片链接")
        self.pic_label = QLabel("未选择")
        self.pic_label.setStyleSheet("color: gray;")
        pic_layout = QHBoxLayout()
        pic_layout.addWidget(self.pic_btn)
        pic_layout.addWidget(self.pic_label)
        pic_layout.addStretch()
        form.addRow("图片链接:", pic_layout)

        self.title_btn = SelectorButton("选取标题")
        self.title_label = QLabel("未选择")
        self.title_label.setStyleSheet("color: gray;")
        title_layout = QHBoxLayout()
        title_layout.addWidget(self.title_btn)
        title_layout.addWidget(self.title_label)
        title_layout.addStretch()
        form.addRow("作品标题:", title_layout)

        layout.addWidget(selector_group)

        self.download_btn = QPushButton("⬇️ 开始下载")
        self.download_btn.setMinimumHeight(40)
        layout.addWidget(self.download_btn)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        log_group = QGroupBox("运行日志")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 10))
        log_layout.addWidget(self.log_text)
        layout.addWidget(log_group)

    def setup_signals(self) -> None:
        self.expand_btn.clicked.connect(lambda: self.start_element_selector(self.expand_btn))
        self.pic_btn.clicked.connect(lambda: self.start_element_selector(self.pic_btn))
        self.title_btn.clicked.connect(lambda: self.start_element_selector(self.title_btn))

        self.expand_btn.selector_changed.connect(
            lambda s: self.expand_label.setText(s if s else "未选择"))
        self.pic_btn.selector_changed.connect(
            lambda s: self.pic_label.setText(s if s else "未选择"))
        self.title_btn.selector_changed.connect(
            lambda s: self.title_label.setText(s if s else "未选择"))

        self.download_btn.clicked.connect(self.start_download)
        self.login_btn.clicked.connect(self.start_login)

        self.log_signal.connect(self.append_log)
        self.progress_signal.connect(self.update_progress)
        self.finished_signal.connect(self.on_download_finished)
        self.error_signal.connect(self.on_download_error)

    def append_log(self, msg: str) -> None:
        self.log_text.append(msg)
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_text.setTextCursor(cursor)

    def _action_buttons(self):
        """下载 / 登录流程涉及的全部操作按钮"""
        return (self.expand_btn, self.pic_btn, self.title_btn,
                self.download_btn, self.login_btn)

    def set_actions_enabled(self, enabled: bool) -> None:
        for btn in self._action_buttons():
            btn.setEnabled(enabled)

    def start_element_selector(self, target_btn: SelectorButton) -> None:
        url = self.url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "警告", "请先输入作品 URL")
            return

        self.current_selector_target = target_btn
        self.append_log(f"启动元素选择器，目标页面: {url}")

        self.set_actions_enabled(False)

        self.selector_thread = QThread()
        self.selector_worker = SelectorWorker(url)
        self.selector_worker.moveToThread(self.selector_thread)

        self.selector_thread.started.connect(self.selector_worker.run)
        self.selector_worker.finished.connect(self.on_selector_finished)
        self.selector_worker.finished.connect(self.selector_thread.quit)
        self.selector_worker.finished.connect(self.selector_worker.deleteLater)
        self.selector_thread.finished.connect(self.selector_thread.deleteLater)
        self.selector_worker.log.connect(self.append_log)

        self.selector_thread.start()

    def on_selector_finished(self, result: Dict[str, str]) -> None:
        self.set_actions_enabled(True)

        if not result:
            self.append_log("元素选择已取消或失败")
            return

        tag = result.get('type', '')
        cls = result.get('class', '')
        if cls:
            class_selector = '.'.join(cls.split())
            selector_str = f"css:{tag}.{class_selector}"
        else:
            selector_str = f"css:{tag}"

        if self.current_selector_target:
            self.current_selector_target.set_selector(selector_str)
            self.append_log(f"已选择元素: {selector_str}")
            self.save_config()
        self.current_selector_target = None

    def start_download(self) -> None:
        url = self.url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "警告", "请输入作品 URL")
            return

        expand_sel = self.expand_btn.selector
        pic_sel = self.pic_btn.selector
        title_sel = self.title_btn.selector

        if pic_sel and expand_sel:
            self.append_log("检测到已配置的选择器 → 将作为智能识别失败时的备用方案。")
        else:
            self.append_log("未配置选择器 → 将使用智能识别（自动展开并识别图片，无需配置）。")

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.set_actions_enabled(False)
        self.save_config()

        self.append_log("========== 开始下载任务 ==========")

        self.download_signals = DownloaderSignals()
        self.download_signals.login_event = threading.Event()
        self.download_signals.log.connect(self.log_signal)
        self.download_signals.progress.connect(self.progress_signal)
        self.download_signals.finished.connect(self.finished_signal)
        self.download_signals.error.connect(self.error_signal)
        self.download_signals.need_login.connect(self.on_need_login)

        self.download_thread = QThread()
        self.download_worker = DownloadWorker(
            url, expand_sel, pic_sel, title_sel,
            self.download_signals
        )
        self.download_worker.moveToThread(self.download_thread)

        self.download_thread.started.connect(self.download_worker.run)
        self.download_worker.finished.connect(self.download_thread.quit)
        self.download_worker.finished.connect(self.download_worker.deleteLater)
        self.download_thread.finished.connect(self.download_thread.deleteLater)

        self.download_thread.start()

    def start_login(self) -> None:
        """单独登录 Pixiv 并保存登录状态（不下载作品）"""
        self.set_actions_enabled(False)
        self.append_log("========== 登录 Pixiv（保存登录状态） ==========")

        self.login_signals = DownloaderSignals()
        self.login_signals.login_event = threading.Event()
        self.login_signals.log.connect(self.append_log)
        self.login_signals.finished.connect(self.on_login_finished)
        self.login_signals.error.connect(self.on_login_error)
        self.login_signals.need_login.connect(self.on_need_login)

        self.login_thread = QThread()
        self.login_worker = DownloadWorker('', '', '', '', self.login_signals, task='login')
        self.login_worker.moveToThread(self.login_thread)

        self.login_thread.started.connect(self.login_worker.run)
        self.login_worker.finished.connect(self.login_thread.quit)
        self.login_worker.finished.connect(self.login_worker.deleteLater)
        self.login_thread.finished.connect(self.login_thread.deleteLater)

        self.login_thread.start()

    def on_need_login(self, message: str) -> None:
        """【主线程】弹出登录提示框；关闭后放行等待中的工作线程"""
        QMessageBox.information(self, '需要登录', message)
        signals = self.sender()
        event = getattr(signals, 'login_event', None)
        if event:
            event.set()

    def on_login_finished(self, _result: str) -> None:
        self.append_log("✅ 登录流程结束，可以开始下载作品了。")
        self.set_actions_enabled(True)
        self.refresh_login_state_label()

    def on_login_error(self, err_msg: str) -> None:
        self.append_log(f"❌ 登录出错: {err_msg}")
        QMessageBox.critical(self, "登录出错", err_msg)
        self.set_actions_enabled(True)
        self.refresh_login_state_label()

    def update_progress(self, current: int, total: int) -> None:
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)

    def on_download_finished(self, save_dir: str) -> None:
        self.append_log(f"✅ 下载完成！保存目录: {save_dir}")
        self.reset_ui_after_download()
        self.refresh_login_state_label()

    def on_download_error(self, err_msg: str) -> None:
        self.append_log(f"❌ 下载出错: {err_msg}")
        QMessageBox.critical(self, "下载错误", err_msg)
        self.reset_ui_after_download()
        self.refresh_login_state_label()

    def reset_ui_after_download(self) -> None:
        self.set_actions_enabled(True)
        self.progress_bar.setVisible(False)


class SelectorWorker(QObject):
    finished = Signal(dict)
    log = Signal(str)

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    def run(self) -> None:
        try:
            selector = ElementSelector(self.url)
            result = selector.select_element()
            self.finished.emit(result)
        except Exception as e:
            self.log.emit(f"选择器出错: {e}")
            self.finished.emit({})


class DownloadWorker(QObject):
    finished = Signal()

    def __init__(
        self,
        url: str,
        expand_sel: str,
        pic_sel: str,
        title_sel: str,
        signals: DownloaderSignals,
        task: str = 'download'
    ):
        super().__init__()
        self.url = url
        self.expand_sel = expand_sel
        self.pic_sel = pic_sel
        self.title_sel = title_sel
        self.signals = signals
        self.task = task  # 'download' = 下载作品；'login' = 仅登录并保存登录状态

    def run(self) -> None:
        downloader = PixivDownloader(self.signals)
        if self.task == 'login':
            downloader.run_login()
        else:
            downloader.run_download(
                self.url,
                self.expand_sel,
                self.pic_sel,
                self.title_sel
            )
        self.finished.emit()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()

    print(r"""
     _____   ___   ___   _   _  
    | _,\ `v' | \_/ / | | \ / | 
    | v_/`. .' > , <| |_`\ V /' 
    |_|   !_! /_/ \_\___| \_/   
    """)
    print("Pixiv 下载器 GUI 版 - 启动成功")

    sys.exit(app.exec())