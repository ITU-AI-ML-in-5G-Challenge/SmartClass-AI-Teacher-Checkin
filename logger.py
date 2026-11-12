"""
轻量级日志工具模块。

主要功能：
    - 提供带 ANSI 颜色的终端输出（cprint）
    - 提供带彩色 Formatter 的 logging.Logger（ColorFormatter）
    - 提供 setup_logger() 创建双 handler（文件 + 彩色控制台）
    - 提供兼容性 get_logger() 可返回 logger 或彩色 cprint

适用场景：
    - 所有简单脚本直接使用 cprint.xxx() 输出彩色日志
    - 复杂模块或守护进程使用 setup_logger() 获取标准 logger
"""
import logging
import os

from message import is_self_or_bot_message

class ColorFormatter(logging.Formatter):
    """
    logging 模块彩色输出格式化器。

    功能：
        - 根据日志级别添加 ANSI 颜色前缀
        - 文件 handler 不使用颜色，仅控制台使用颜色
        - 自动恢复默认颜色，避免污染后续输出

    支持颜色：
        - DEBUG: 白
        - INFO: 亮绿
        - WARNING: 亮黄
        - ERROR: 亮红
        - CRITICAL: 红底白字
    """
    COLORS = {
        'DEBUG': '\033[37m',      # 白
        'INFO': '\033[92m',       # 亮绿
        'WARNING': '\033[93m',    # 亮黄（兼容性最好）
        'ERROR': '\033[91m',      # 亮红
        'CRITICAL': '\033[41m',   # 红底白字
    }
    RESET = '\033[0m'

    def format(self, record):
        """
        重写 logging.Formatter.format，为日志文本添加颜色。

        :param record: logging 日志记录
        :type record: logging.LogRecord
        :return: 彩色格式化后的文本
        :rtype: str
        """        
        color = self.COLORS.get(record.levelname, self.RESET)
        message = super().format(record)
        return f"{color}{message}{self.RESET}"

class cprint:
    """
    简易彩色打印类，用于直接 print 输出带颜色的调试信息。

    特点：
        - 无需配置 logging
        - 采用 ANSI 颜色代码
        - 用于中小脚本或简单 debug
    """    
    RESET = "\033[0m"

    COLORS = {
        'DEBUG':    '\033[37m',   # 白
        'INFO':     '\033[92m',   # 亮绿
        'WARNING':  '\033[93m',   # 亮黄
        'ERROR':    '\033[91m',   # 亮红
        'CRITICAL': '\033[41m',   # 红底白字
    }

    @staticmethod
    def debug(msg, *args):
        """
        打印 DEBUG（白色）消息。
        """
        if args:
            msg = msg % args
        print(f"{cprint.COLORS['DEBUG']}{msg}{cprint.RESET}", flush=True)


    @staticmethod
    def info(msg, *args):
        """
        打印 INFO（亮绿）消息。
        """
        if args:
            msg = msg % args
        print(f"{cprint.COLORS['INFO']}{msg}{cprint.RESET}", flush=True)


    @staticmethod
    def warning(msg, *args):
        """
        打印 WARNING（亮黄）消息。
        """
        if args:
            msg = msg % args
        print(f"{cprint.COLORS['WARNING']}{msg}{cprint.RESET}", flush=True)


    @staticmethod
    def error(msg, *args):
        """
        打印 ERROR（亮红）消息。
        """
        if args:
            msg = msg % args
        print(f"{cprint.COLORS['ERROR']}{msg}{cprint.RESET}", flush=True)


    @staticmethod
    def critical(msg, *args):
        """
        打印 CRITICAL（红底白字）消息。
        """
        if args:
            msg = msg % args
        print(f"{cprint.COLORS['CRITICAL']}{msg}{cprint.RESET}", flush=True)

def setup_logger(log_file: str = "autoreview_bot.log") -> logging.Logger:
    """
    创建一个带文件输出 + 彩色控制台输出的 logger。

    特点：
        - 文件 handler：无颜色，包含时间 / 级别 / 源文件行号
        - 控制台 handler：使用 ColorFormatter，带颜色
        - 自动阻止 propagate，避免重复输出
        - 若 logger 已存在 handler，则不会重复添加

    :param log_file: 日志文件名（默认 autoreview_bot.log）
    :type log_file: str
    :return: 配置好的 logging.Logger 对象
    :rtype: logging.Logger
    """    
    logger = logging.getLogger(os.path.splitext(log_file)[0])
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        # 文件 handler（无颜色）
        fh = logging.FileHandler(log_file, encoding="utf-8")
        file_fmt = logging.Formatter(
            "%(asctime)s [%(name)s] [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s"
        )
        fh.setFormatter(file_fmt)

        # 屏幕 handler（彩色）
        sh = logging.StreamHandler()
        color_fmt = ColorFormatter(
            "%(asctime)s [%(name)s] [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s"
        )
        sh.setFormatter(color_fmt)

        logger.addHandler(fh)
        logger.addHandler(sh)

    # 关键：阻止冒泡，避免重复输出
    logger.propagate = False

    return logger

def get_logger(logger = None):
    """
    兼容性获取 logger。

    行为：
        - 若传入 logger → 原封返回
        - 若传入 None → 返回 cprint（轻量彩色打印类）

    :param logger: logging.Logger 或 None
    :type logger: logging.Logger or None
    :return: logger 或 cprint
    :rtype: logging.Logger or cprint
    """
    return logger or cprint


def debug_dump_raw_messages(groupname, pass_label, msgs, logger, log_file=None):
    """
    打印某遍滚动收集到的原始消息（已过滤 self / bot account），用于排查漏消息问题。

    若提供 log_file，则把同样的调试内容（无颜色）追加写入该文件，
    便于在打卡目录下留存每轮的原始消息供事后排查。

    :param groupname: 群名称
    :type groupname: str
    :param pass_label: 第几遍标签，如 "第一遍"
    :type pass_label: str
    :param msgs: 消息列表
    :type msgs: list
    :param logger: 日志器
    :type logger: logging.Logger or cprint
    :param log_file: 可选，调试日志文件路径；提供则追加写入
    :type log_file: str or None
    """
    log = get_logger(logger)
    # 过滤 self / bot account（工作账号）消息，不打印、不写日志
    shown = [m for m in msgs if not is_self_or_bot_message(m)]
    lines = []

    def emit(text):
        log.warning(text)
        if log_file:
            lines.append(text)

    emit(
        f"========== DEBUG: {groupname} {pass_label} 原始消息 "
        f"共 {len(msgs)} 条（过滤 self/bot 后剩 {len(shown)} 条） =========="
    )
    for i, msg in enumerate(shown):
        sender = getattr(msg, 'sender', 'N/A')
        content = getattr(msg, 'content', 'N/A')
        msg_type = getattr(msg, 'type', 'N/A')
        attr = getattr(msg, 'attr', 'N/A')
        msg_time = getattr(msg, 'time', 'N/A')
        # 时间消息：wxauto4 为 type='time'，wxauto 3.x 为 type='base' + attr='time'
        is_time = (msg_type == 'time') or (attr == 'time')
        extra = f" | msg.time={msg_time}" if is_time else ""
        emit(
            f"  [{i}] type={msg_type} | sender={sender} | "
            f"attr={attr} | content={content}{extra}"
        )
    emit("========== DEBUG END ==========")

    if log_file:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def debug_dump_text_msgs(groupname, text_msgs, logger, log_file=None):
    """
    打印 export_group() 提取到的打卡文本列表，用于排查解析遗漏。

    若提供 log_file，则把同样的调试内容（无颜色）追加写入该文件。

    :param groupname: 群名称
    :type groupname: str
    :param text_msgs: 文本消息列表
    :type text_msgs: list[str]
    :param logger: 日志器
    :type logger: logging.Logger or cprint
    :param log_file: 可选，调试日志文件路径；提供则追加写入
    :type log_file: str or None
    """
    log = get_logger(logger)
    lines = []

    def emit(text):
        log.warning(text)
        if log_file:
            lines.append(text)

    emit(
        f"========== DEBUG: {groupname} text_msgs 共 {len(text_msgs)} 条 =========="
    )
    for i, t in enumerate(text_msgs):
        emit(f"  [{i}] {t}")
    emit("========== DEBUG END ==========")

    if log_file:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")