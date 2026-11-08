import logging

from colorama import Fore, Back, Style
import colorama

# 初始化colorama
colorama.init(autoreset=True)

Miao = True

LOG_ICONS = {
    'DEBUG': '🔍',
    'INFO': 'ℹ️',
    'WARNING': '⚠️',
    'ERROR': '❌',
    'CRITICAL': '💥'
}

# 为不同日志级别定义颜色
LOG_COLORS = {
    'DEBUG': Fore.CYAN,
    'INFO': Fore.WHITE,
    'WARNING': Fore.YELLOW,
    'ERROR': Fore.RED,
    'CRITICAL': Fore.RED + Back.WHITE + Style.BRIGHT
}

class CustomLogFormatter(logging.Formatter):
    def format(self, record):
        # 获取原始日志级别名称
        levelname = record.levelname
        
        # 添加图标
        icon = LOG_ICONS.get(levelname, '📝')
        record.levelname = f"{icon} {levelname}"
        
        # 添加颜色
        color = LOG_COLORS.get(levelname, '')  # 获取日志级别的颜色
        record.levelname = f"{color}{record.levelname}{Style.RESET_ALL}"
        
        # 保存原始消息
        original_msg = record.getMessage()
        
        # 为消息也添加颜色
        message_color = LOG_COLORS.get(levelname, '')
        if message_color:
            # 添加喵后缀并应用颜色
            if '喵' not in original_msg:
                record.msg = f"{message_color}{record.msg} {"喵" if Miao else ""}{Style.RESET_ALL}"
            else:
                record.msg = f"{message_color}{record.msg}{Style.RESET_ALL}"
        else:
            # 如果没有特定颜色，仍然添加喵后缀
            if '喵' not in original_msg:
                record.msg = f"{record.msg} {"喵" if Miao else ""}"
            
        return super().format(record)

def get_logger(name: str, level: int=logging.DEBUG) -> logging.Logger:
    # 创建logger实例
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # 如果logger没有处理器，则添加一个
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(CustomLogFormatter(
            '%(asctime)s | %(name)s | %(levelname)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        logger.addHandler(handler)

    return logger
