# obsolete / v1 命令行版（已归档，不再维护）

当前主版本为 **v2 GUI 版**（项目根目录 `GUI.py`，见根目录 README.md）。
以下文件是早期命令行版本，仅作历史参考，请勿直接运行：

| 文件 | 说明 |
|---|---|
| `main.py` | v1 命令行主程序。硬编码 CSS 选择器；每次启动临时浏览器，**不保存登录状态**；依赖 `DrissionPage`、`colorama` |
| `Logger.py` | v1 的彩色 emoji 日志模块（`colorama`），被 v1 main.py 引用 |
| `config.json` | v1 配置文件，内容为 `{"thread_count": 5}` |

v1 与 v2 的差异：

- v2 是图形界面（PySide6），v1 是纯命令行
- v2 会把登录会话保存到工作区根目录 `pixiv_config.json`，下载无需重复登录；v1 每次都要手动登录
- v2 可通过“可视化元素选择器”重新选取页面元素，v1 只能改代码里的选择器常量

如需运行 v1：`python main.py`（先 `pip install DrissionPage colorama`），
下载目录同样为根目录的 `download/`。
