# Pixiv 图片下载器

这是一个使用 Python 和 DrissionPage 库开发的 Pixiv 图片下载器，支持多线程下载 Pixiv 作品中的所有图片。

## 项目结构

```
.
├── main.py              # 主程序文件
├── Logger.py            # 日志处理模块
├── config.json          # 配置文件
├── download/            # 下载目录
│   └── [作品标题]/      # 以作品标题命名的子目录
└── drissionpage_docs_en/ # DrissionPage 库文档
```

## 安装依赖

在项目目录下运行以下命令安装所需依赖：

```bash
pip install DrissionPage colorama
```

## 简易使用方法

1. 在项目目录下运行主程序：

```bash
python main.py
```

2. 输入 Pixiv 作品网址，例如：

```
请输入pixiv作品网址: https://www.pixiv.net/artworks/12345678
```

3. 等待程序自动下载完成，下载完成后会自动打开保存目录。

## 联系方式

- Bilibili 空间：[https://space.bilibili.com/616045770](https://space.bilibili.com/616045770)
- QQ 群：132292540