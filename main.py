import DrissionPage as Dp
import json
import os
import threading
import time
import subprocess
import logging
from contextlib import redirect_stdout
from concurrent.futures import ThreadPoolExecutor, as_completed
from Logger import get_logger

# 禁用drissionpage的日志输出
logging.getLogger('DrissionPage').setLevel(logging.CRITICAL)

class PixivDownloader:
    def __init__(self):
        # 创建logger实例
        self.logger = get_logger('PixivDownloader')
        # 读取配置文件
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        self.thread_count = config.get('thread_count', 5)
        self.total_files = 0
        self.completed_files = 0
        self.start_time = 0
        self.lock = threading.Lock()
    
    def run(self):
        # 获取用户输入的pixiv作品网址
        url = input("请输入pixiv作品网址: ")
        
        # 使用DrissionPage打开Chrome并访问该网址
        self.page = Dp.ChromiumPage()
        self.page.get(url)
        
        # 查找button标签、class为sc-e3cb8b83-0 LDEei的元素
        button_element = self.page.ele('css:button.sc-e3cb8b83-0.LDEei')
        if button_element:
            self.logger.info("找到按钮，正在点击...")
            button_element.click()
            # 等待点击后页面加载
            time.sleep(1)
        else:
            self.logger.info("未找到按钮，跳过点击步骤")
        
        # 查找所有a标签、class为"sc-440d5b2c-3 jpNsVx gtm-expand-full-size-illust"的元素
        elements = self.page.eles('css:a.sc-440d5b2c-3.jpNsVx.gtm-expand-full-size-illust')
        href_list = [element.attr('href') for element in elements]  # pyright: ignore[reportGeneralTypeIssues]
        
        # 打印提取的链接，用于调试
        self.logger.info(f"提取到{len(href_list)}个链接")
        for link in href_list:
            self.logger.debug(f"链接: {link}")
        
        # 查找h1标签、class为"sc-965e5f82-3 kANgwp"的元素
        title_element = self.page.ele('css:h1.sc-965e5f82-3.kANgwp')
        title = title_element.text if title_element else "untitled"
        self.logger.info(f"作品标题: {title}")
        
        # 创建保存目录
        save_dir = f'./download/{title}'
        os.makedirs(save_dir, exist_ok=True)
        
        # 设置浏览器下载路径
        self.page.set.download_path(save_dir)
        
        # 开始下载
        self.total_files = len(href_list)
        self.completed_files = 0
        self.start_time = time.time()
        
        self.logger.info(f"开始下载，共{self.total_files}张图片")
        
        # 多线程下载
        with ThreadPoolExecutor(max_workers=self.thread_count) as executor:
            future_to_url = {executor.submit(self.download_image, img_url, save_dir): img_url for img_url in href_list}
            for future in as_completed(future_to_url):
                with self.lock:
                    self.completed_files += 1
                    self.show_progress()
        
        # 下载完成后打开保存目录
        self.logger.info(f"下载完成！保存目录：{save_dir}")
        subprocess.run(['explorer', os.path.abspath(save_dir)])

        # 关闭浏览器
        self.page.close()
    
    def download_image(self, url, save_dir):
        with redirect_stdout(open(os.devnull, 'w', encoding='utf-8')):
            # 下载图片
            try:
                # 生成文件名
                filename = f"{int(time.time() * 1000)}.jpg"
                
                # 设置下载文件名
                self.page.set.download_file_name(filename)
                
                # 使用浏览器下载图片（借用已登录的cookie）
                mission = self.page.download(url)
            except Exception as e:
                self.logger.error(f"下载失败 {url}: {e}")
    
    def show_progress(self):
        # 计算已用时间
        elapsed_time = time.time() - self.start_time
        # 计算下载速度
        if elapsed_time > 0:
            speed = self.completed_files / elapsed_time
        else:
            speed = 0
        
        # 显示进度条
        progress = self.completed_files / self.total_files * 100
        bar_length = 50
        filled_length = int(bar_length * self.completed_files // self.total_files)
        bar = '█' * filled_length + '-' * (bar_length - filled_length)
        
        print(f"\r[{bar}] {self.completed_files}/{self.total_files} ({progress:.1f}%) 速度: {speed:.2f} 图片/秒", end="")

if __name__ == "__main__":
    downloader = PixivDownloader()
    downloader.run()