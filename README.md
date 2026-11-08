# EasyPixivDownload

Pixiv 作品图片批量下载器：用 [DrissionPage](https://github.com/g1879/DrissionPage) 接管本机
Chrome / Edge 打开作品页，自动识别作品里的全部原图并多线程下载；界面基于 PySide6。
登录状态只需人工登录一次，之后保存在本地配置文件里复用，下载 R18 等需要登录的作品
无需反复登录。

> 当前主线是 **v2（GUI 版）**，入口为 `GUI.py`。
> 早期命令行版（v1）已归档到 [`obsolete/`](obsolete/README.md)，仅作历史参考，不再维护。

## Features

- **图形界面**：作品 URL 输入框、一键下载、进度条、实时运行日志。
- **智能识别（默认路径，无需配置选择器）**：优先调用 pixiv 官方接口
  `/ajax/illust/{id}` 与 `/ajax/illust/{id}/pages` 取作品标题和**每一页原图**地址
  （多页漫画/画集也能拿全）；接口不可用时退回页面 DOM 识别 —— 自动点击“查看更多”
  类按钮、滚动触发懒加载、收集页面中的 `i.pximg.net` 图片，并统一换算成
  `img-original` 原图地址。
- **登录状态保存 / 恢复**：点一次“🔐 登录 Pixiv”，程序把 pixiv 域下未过期的 Cookie
  写入本地配置；之后启动任务会自动回填，界面会显示“已保存登录状态：N 条 Cookie”。
- **可视化元素选择器（可选兜底）**：智能识别失败时，可在**已登录**状态下用鼠标点选
  “展开按钮 / 图片链接 / 作品标题”，自动生成 `css:tag.class` 形式的选择器并保存。
- **多线程下载**：并发线程数由配置项 `thread_count` 控制（默认 5）。
- **按作品建目录**：文件落到 `download/<作品标题>/`，文件名沿用 Pixiv 原始文件名
  （形如 `105000000_p0.jpg`），下载结束后自动打开保存目录。

## Requirements

- Windows 10+（代码本身跨平台，但本项目仅在 Windows 上开发与验证）
- Python 3.10+，推荐 3.12（开发与验证环境为 3.12）
- 本机已安装 Chrome 或 Edge —— DrissionPage 直接接管本机浏览器，不需要额外下载内核
- 网络能访问 `www.pixiv.net` / `i.pximg.net`
- 首次使用需要人工登录一次（见 [Login](#login)）

## Installation

```bash
git clone https://github.com/ThisisYLaaa/EasyPixivDownload.git
cd EasyPixivDownload
```

建议使用虚拟环境（可选）：

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate.bat
# Linux/macOS:  source .venv/bin/activate
```

安装依赖：

```bash
pip install -r requirements.txt
```

只有“可视化元素选择器”需要 Playwright 的浏览器内核（日常下载不需要）：

```bash
playwright install chromium
```

不装这一步也能正常下载，只是点选元素时会报缺少可执行文件。

## Quick Start

```bash
# 1) 复制配置模板（可选：直接运行也行，程序会在首次登录/保存时自动创建配置）
#    Windows:  copy pixiv_config.example.json pixiv_config.json
#    Linux/macOS:  cp pixiv_config.example.json pixiv_config.json

# 2) 启动图形界面
python GUI.py
```

界面上按顺序操作：

1. **首次使用 / 登录失效时**：点“🔐 登录 Pixiv（保存登录状态）”，在弹出的浏览器窗口里
   完成登录（R18 作品请先在账号设置里开启 R18 浏览权限），然后回到程序点“确定”。
   程序会重新打开 pixiv 首页确认登录状态，并把 Cookie 落盘到 `pixiv_config.json`。
2. 在“作品 URL”里粘贴作品地址，例如 `https://www.pixiv.net/artworks/12345678`。
3. 点“⬇️ 开始下载”。程序会依次：恢复登录状态 → 打开作品页 → 判断是否需要登录 →
   智能识别标题与原图列表 → 多线程下载到 `download/<作品标题>/` → 打开保存目录。

正常情况下**不需要配置任何选择器**。仅当日志提示“未能识别到任何图片”、且已确认
登录有效、作品可访问时，才需要用“选取展开按钮 / 选取图片链接 / 选取作品标题”手动
点选兜底，选择器会自动保存，下次下载时在智能识别失败后自动启用。

## Login

- 登录页：`https://accounts.pixiv.net/login`（程序启动浏览器后自动跳转）。
- 只保存 `pixiv.net` / `pximg.net` 域下的 Cookie，且**跳过已过期**的项；每条只保留
  `name / value / domain / path / expires / httpOnly / secure / sameSite / priority`。
- 保存的是浏览器会话凭证，**等同于账号权限**，请勿分享、截图或提交到仓库
  （`.gitignore` 已忽略真实配置文件）。
- 清除登录状态：删除 `pixiv_config.json`，或把其中 `cookies` 改成 `[]`。

## Configuration

配置文件名固定为 `pixiv_config.json`，位置为**程序所在目录**（即 `GUI.py` 同级），
无论从哪个工作目录启动都以此为准。仓库只提供模板 `pixiv_config.example.json`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `url` | string | 上次使用的作品 URL，仅用于回填输入框 |
| `expand_selector` | string | “查看更多”按钮的 CSS 选择器，形如 `css:button.abc.def`；留空表示不使用 |
| `pic_selector` | string | 图片链接元素的 CSS 选择器；仅在兜底路径使用，留空表示不使用 |
| `title_selector` | string | 作品标题元素的 CSS 选择器；仅在兜底路径使用，留空表示不使用 |
| `thread_count` | int | 图片下载并发线程数，默认 `5`；过高容易被风控 |
| `cookies` | array | pixiv 登录 Cookie 列表，登录后自动写入（**含登录凭证，勿外传**） |
| `cookies_saved_at` | string | 登录状态保存时间，格式 `YYYY-MM-DD HH:MM:SS` |

模板内容（`pixiv_config.example.json`）：

```json
{
  "url": "https://www.pixiv.net/artworks/00000000",
  "expand_selector": "",
  "pic_selector": "",
  "title_selector": "",
  "thread_count": 5,
  "cookies": [],
  "cookies_saved_at": ""
}
```

> ⚠️ `pixiv_config.json` 中的 `cookies` 是登录凭证。提交代码前请确认它已被忽略
> （`git status` 中不应出现该文件），也不要把内容贴进 issue 或聊天记录。

## Output

```
<程序目录>/download/<作品标题>/<Pixiv 原始文件名>
```

- 目录名取自作品标题，非法字符 `\ / : * ? " < > |` 会被替换为 `_`，空标题回退为 `untitled`。
- 文件名沿用图片 URL 的最后一段（例如 `105000000_p0.jpg`），不额外重命名。
- 下载完成后自动打开保存目录（Windows `explorer` / macOS `open` / Linux `xdg-open`）。
- `download/` 体积很大，已在 `.gitignore` 中忽略，不会入库。

## Project Structure

```
EasyPixivDownload/
├── GUI.py                     # v2 主程序与唯一入口（PySide6 图形界面）
├── web_element_selector.py    # 独立的元素选择器脚本（GUI 内已内置等价的同步版）
├── pixiv_config.example.json  # 配置模板（复制为 pixiv_config.json 使用）
├── pixiv_config.json          # 本地配置 + 登录 Cookie（运行时生成，不入库）
├── requirements.txt           # 运行依赖
├── download/                  # 下载输出目录（运行时创建，不入库）
├── obsolete/                  # v1 命令行版归档：main.py / Logger.py / config.json / README.md
├── drissionpage_docs_en/      # DrissionPage 英文离线文档（开发参考，原样保留）
├── LICENSE.txt                # MIT 许可证
└── README.md                  # 本说明
```

## Troubleshooting

**1. 日志提示“需要登录”**
已保存的登录状态失效或权限不足（例如作品要求关注、需要 R18 认证）。在浏览器窗口里
重新登录一次，点“确定”后程序会自动覆盖保存新的登录状态。

**2. R18 作品打不开 / 提示需要认证**
先用浏览器登录 pixiv，在账号设置中开启“浏览 R18 / R18G 作品”，再重新执行一次
“🔐 登录 Pixiv（保存登录状态）”。

**3. 提示“未能识别到任何图片”**
按顺序排查：是否真的登录成功（界面应显示“已保存登录状态：N 条 Cookie”）→ 作品是否
仍然公开、是否满足关注/R18 条件 → 页面是否加载过慢（重试）。仍失败时用界面上的
“选取××”按钮手动点选兜底。

**4. 登录后仍显示未登录**
浏览器里可能没真正完成登录（验证码、两步验证未走完）。重复登录流程，确认页面右上角
已显示头像/用户名后再点“确定”。

**5. 下载很慢或被中断**
可调高 `pixiv_config.json` 的 `thread_count`，但并发过高容易触发 pixiv 风控，建议不超过
10；网络不稳时可先降回 3～5 重试。

**6. 元素选择器报 `Executable doesn't exist`**
没有安装 Playwright 浏览器内核，执行 `playwright install chromium` 即可；不影响下载功能。

**7. 浏览器窗口没有弹出 / 行为异常**
确认本机已安装 Chrome 或 Edge；若已有多个浏览器实例占用调试端口，先关掉多余的浏览器
窗口再重试（DrissionPage 会接管本机浏览器的调试端口）。

**8. 从其它目录启动时找不到配置**
程序以 `GUI.py` 所在目录为基准定位配置文件与 `download/`，请用
`python path/to/GUI.py` 启动，或先 `cd` 到项目目录。

## Disclaimer

- 本项目仅用于下载你**有权访问**的作品，供**个人备份与离线阅读**。
- 请遵守 pixiv 的利用規約与作品著作权：**不得**将下载内容用于二次分发、商业用途、
  公开传播或任何侵权场景；转载/使用请先取得作者授权。
- 本工具不提供绕过付费、会员、关注限制或版权保护的能力；R18 内容需账号自行开启，
  且访问者须达到所在地区的法定年龄。
- 高频或大并发请求可能触发 pixiv 风控甚至账号限制，风险与后果由使用者自行承担。
- 本项目与 pixiv 官方无关，属非官方第三方工具。
- 代码按 MIT 协议“AS IS”提供，作者不对使用后果承担任何责任。

## License

本项目使用 MIT 许可证，详见 [LICENSE.txt](LICENSE.txt)。
Copyright (c) 2026 This_is_YL。

## Credits

- [DrissionPage](https://github.com/g1879/DrissionPage)：浏览器接管与文件下载。
- [PySide6](https://doc.qt.io/qtforpython/)（Qt for Python）：图形界面。
- [Playwright](https://playwright.dev/python/)：可视化元素选择器。
- `drissionpage_docs_en/` 为 DrissionPage 官方英文文档的离线副本，版权归其原作者。
