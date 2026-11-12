"""
微信消息解析与增量比对工具模块。

主要功能：
    - 提供一系列 msg.type / msg.message_type_name 的判断辅助函数
    - 提供 message 的过滤逻辑（过滤旧日期消息、过滤系统消息）
    - 处理微信时间字符串的多格式解析（AM/PM、毫秒、仅时间、仅日期）
    - 提供基于 hash 的消息去重、消息比较
    - 实现两种消息 diff 算法：
        1) MessageComparer：精确匹配（基于 SHA256）
        2) MessageDiffFast：高性能滚动哈希匹配（O(n)）

用途：
    - 所有与 wxauto4.GetAllMessage() 获取的消息相关的逻辑均会使用此模块。
    - 在守护进程、自动点评、导出程序中频繁依赖该模块。
"""
import re
import os
import hashlib
from datetime import datetime, timedelta

# 打卡工作账号（默认）：
#   收集消息时互相忽略，避免已发送的统计结果被当作打卡记录。
#   通过 manage.py add-bot 可添加新账号，存储在 bot_accounts.txt。
_DEFAULT_BOT_ACCOUNTS = ("小助手", "Assistant")


def _load_bot_accounts():
    """从 bot_accounts.txt 加载工作账号，合并默认值。"""
    accounts = list(_DEFAULT_BOT_ACCOUNTS)
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bot_accounts.txt')
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    name = line.strip()
                    if name and name not in accounts:
                        accounts.append(name)
    except Exception:
        pass
    return tuple(accounts)


_BOT_ACCOUNTS = _load_bot_accounts()


def is_self_or_bot_statistics_message(msg):
    """
    判断消息是否应被打卡统计忽略。

    现与 is_self_or_bot_message 等价：self 自己发的、或打卡工作账号
    （bot account）发的所有消息都忽略（不只【系统消息】统计结果），
    且通过 get_sender_name 兼容 wxauto 3.x / wxauto4 的 sender 语义差异。
    保留此函数名以兼容 filter_messages 的调用。

    :param msg: 微信消息对象
    :return: 是否应被忽略
    :rtype: bool
    """
    return is_self_or_bot_message(msg)


def is_text_message(msg):
    """
    判断消息是否为文本消息。

    包含：
        - 普通 text
        - quote 类型（引用）

    :param msg: 微信消息对象
    :return: 是否为文本
    :rtype: bool
    """    
    return msg.type == "text" or msg.type == "quote"

def is_pic_message(msg):
    """
    判断消息是否为图片消息。

    :param msg: 微信消息对象
    :return: 是否为图片
    :rtype: bool
    """    
    return msg.type == "image"

def is_time_message(msg):
    """
    判断消息是否为时间分割消息（TimeMessage）。

    时间消息用于标识聊天时间节点，用于比对打卡日期。

    兼容 wxauto 3.x 与 wxauto4：
      - wxauto4 的 TimeMessage：type == "time"
      - wxauto 3.x 的 TimeMessage：type == "base"，attr == "time"

    :param msg: 微信消息对象
    :return: 是否为时间消息
    :rtype: bool
    """
    return msg.type == "time" or getattr(msg, "attr", "") == "time"

def is_file_message(msg):
    """
    判断消息是否为文件消息。

    :param msg: 微信消息对象
    :return: 是否文件消息
    :rtype: bool
    """    
    return msg.type == "file"

def is_memo_message(msg):
    """
    判断消息是否为"笔记"类型（微信内置的 [笔记]）。

    兼容 wxauto (type=="other") 与 wxauto4 (type=="note")。

    :param msg: 微信消息对象
    :return: 是否笔记消息
    :rtype: bool
    """
    return (msg.type == "note") or (msg.type == "other" and "笔记" in msg.content)

def is_video_message(msg):
    """
    判断消息是否为视频类型。

    :param msg: 微信消息对象
    :return: 是否视频消息
    :rtype: bool
    """
    return msg.type == "video"

def is_audio_message(msg):
    """
    判断消息是否为音频类型。

    :param msg: 微信消息对象
    :return: 是否音频消息
    :rtype: bool
    """
    return msg.type == "voice"

def is_revoke_message(msg):
    """
    判断消息是否为撤回消息

    :param msg: 微信消息对象
    :return: 如果返回空，就不是撤回消息，如果是撤回消息，返回谁撤回的
    :rtype: str | None
    """        
    if hasattr(msg, "sender") and msg.sender == "system" and "撤回" in msg.content:
        return re.sub(r'[""]', '', msg.content.split("撤回")[0]).strip()
    return None

def get_sender_name(msg):
    """
    返回消息发送者的"群昵称/备注名"（即含学号的名字），用作身份标识。

    兼容 wxauto 3.x 与 wxauto4 的 sender / sender_remark 语义差异：
      - wxauto4：sender 即群昵称（含学号），通常没有 sender_remark
      - wxauto 3.x：sender 是微信昵称（不含学号），sender_remark 才是群昵称/备注（含学号）

    :param msg: 微信消息对象
    :return: 群昵称/备注名字符串
    :rtype: str
    """
    remark = getattr(msg, 'sender_remark', None)
    sender = getattr(msg, 'sender', '')
    # system/self 等特殊值不是昵称，退回 sender
    if remark and remark not in ('system', 'self', 'base', ''):
        return remark
    return sender


def is_self_or_bot_message(msg):
    """
    判断消息是否应被过滤：self 自己发的，或打卡工作账号（bot account）发的。

    兼容 wxauto 3.x 与 wxauto4 的 sender / sender_remark 语义差异：
      - self：attr == "self" 或 sender == "self"
      - bot account：用 get_sender_name 取群昵称（3.x 优先 sender_remark、
        4.x 取 sender），匹配 _BOT_ACCOUNTS（小助手 / Assistant 等）

    与 is_self_or_bot_statistics_message 不同：本函数对 bot account 的
    所有消息都过滤（不只【系统消息】统计结果），用于导出 txt / Debug / log
    时彻底排除工作账号的消息。

    :param msg: 微信消息对象
    :return: 是否应被过滤
    :rtype: bool
    """
    if getattr(msg, 'attr', '') == "self":
        return True
    if getattr(msg, 'sender', '') == "self":
        return True
    return get_sender_name(msg) in _BOT_ACCOUNTS


def log_messages(prefix: str, msgs, logger_func=None):
    """
    打印消息内容用于调试。

    输出格式：
        sender | content | attr | type

    :param prefix: 日志前缀
    :type prefix: str
    :param msgs: 消息列表
    :type msgs: list
    :param logger_func: 回调函数（如 logger.info）
    :type logger_func: callable or None
    """    
    if logger_func:
        logger_func(f"{prefix}:")
        for msg in msgs:
            logger_func(f"{msg.sender}|{msg.content}|{msg.attr}|{msg.type}")

# "一天"的起点偏移小时数：凌晨 0:00–2:00 归属前一天
_DAY_OFFSET = timedelta(hours=2)


def _adjusted_date(dt):
    """将时间向前偏移 _DAY_OFFSET，使 0:00–2:00 归属前一天。"""
    return (dt - _DAY_OFFSET).date()


def _is_target_date(date_string, target_date):
    """
    内部函数：解析多种格式的时间字符串，并与 target_date 比较。

    将"一天"定义为 2:00 到次日 2:00：所有时间向前偏移 2 小时后再比较日期。
    0:00–2:00 的消息归属前一天。

    支持：
        - "03:30 PM" 12 小时制
        - "2025-10-13 15:30:00.123" 毫秒
        - "2025-10-13 15:30:00"
        - "15:30"（无日期，默认今日）
        - "2025-10-13"（纯日期）

    :param date_string: 原始时间文本
    :type date_string: str
    :param target_date: 目标日期
    :type target_date: datetime.datetime
    :return: 相差天数（正、零、负）
    :rtype: int
    """
    # 1. 处理带 AM/PM 格式的时间（例如 "03:30 PM"）
    try:
        if "AM" in date_string or "PM" in date_string:
            date_format = "%I:%M %p"
            input_datetime = datetime.strptime(date_string, date_format)
            input_datetime = input_datetime.replace(
                year=datetime.today().year,
                month=datetime.today().month,
                day=datetime.today().day,
            )
            return (_adjusted_date(input_datetime) - target_date.date()).days
    except ValueError:
        pass

    # 2. 处理带毫秒的时间（例如 "2025-10-13 15:30:00.123"）
    try:
        if "." in date_string:
            date_format = "%Y-%m-%d %H:%M:%S.%f"
            input_datetime = datetime.strptime(date_string, date_format)
            return (_adjusted_date(input_datetime) - target_date.date()).days
    except ValueError:
        pass

    # 3. 处理完整日期时间（例如 "2025-10-13 15:30:00"）
    try:
        if " " in date_string:
            date_format = "%Y-%m-%d %H:%M:%S"
            input_datetime = datetime.strptime(date_string, date_format)
            return (_adjusted_date(input_datetime) - target_date.date()).days
    except ValueError:
        pass

    # 4. 处理没有日期的时间字符串（例如 "15:30"），默认是今天
    if ":" in date_string and len(date_string) <= 5:
        input_datetime = datetime.strptime(
            datetime.today().strftime("%Y-%m-%d") + " " + date_string,
            "%Y-%m-%d %H:%M",
        )
        return (_adjusted_date(input_datetime) - target_date.date()).days

    # 5. 处理只包含日期的字符串（例如 "2025-10-13"）
    try:
        date_format = "%Y-%m-%d"
        input_datetime = datetime.strptime(date_string, date_format)
        return (_adjusted_date(input_datetime) - target_date.date()).days
    except ValueError:
        pass

    return -1  # 如果所有格式都不匹配

def compare_message_date(msg, target_date):
    """
    比较 msg 所代表的时间与目标日期的天数差。

    用于判断哪些消息属于今日、昨天或更早。

    :param msg: 时间消息对象（必须 is_time_message）
    :type msg: Message
    :param target_date: 目标日期
    :type target_date: datetime.datetime
    :return: 天数差值
    :rtype: int or None
    """    
    if not is_time_message(msg):
        return None
    
    # 返回该时间消息距离目标日期的天数
    # 如果该时间消息发生在目标日期之后，则为正数
    # 如果该时间消息发生在目标日期当天，则为零
    # 否则，为负数
    return _is_target_date(msg.time, target_date)

def filter_messages(msg_list, target_date = datetime.today()):
    """
    基于 target_date 过滤消息列表，剪去目标日期以前的消息。

    规则：
        - 向前扫描直到遇到当天/之后的第一条时间消息
        - 忽略 system / self 消息
        - 打卡工作账号：【系统消息】开头的统计结果忽略，其余作为打卡记录
        - 返回一个新的消息列表

    注意：
        已 filter 的结果不能再次 filter，否则会丢失上下文。

    :param msg_list: 微信消息列表（wxauto4.GetAllMessage()）
    :type msg_list: list
    :param target_date: 目标日期
    :type target_date: datetime.datetime
    :return: 过滤后的消息列表
    :rtype: list
    """
    # 只从某天的消息（默认是今天）开始看起，再之前的消息不管
    # msg_list必须是wx.GetAllMessages()来的消息
    # 小心处理，被filter_messages过的messages不能再被filter，否则会变成[]
    days = 0
    position = 0
    for msg in msg_list:
        if is_time_message(msg):
            days = compare_message_date(msg, target_date)
            if days >= 0:
                break

        position += 1
        
    # 过滤掉系统消息和自己发的消息
    filtered_msg = []
    for m in msg_list[position:]:
        if not hasattr(m, "sender"):
            continue
        # self 消息、工作账号的【系统消息】统计结果均忽略
        if is_self_or_bot_statistics_message(m):
            continue
        if m.sender in ("system"):
            result = is_revoke_message(m)
            if not result:
                continue
            else:
                m.sender_remark = result
                m.content = "ANY"
                filtered_msg.append(m)
            continue

        filtered_msg.append(m)
    return filtered_msg

class MessageHasher:
    """
    为微信消息自动生成 SHA256 hash，用于比较消息内容是否相同。

    特点：
        - 基于 (sender, content, attr, type)
        - 缓存 hash 避免重复计算
    """
    def get_hash(self, msg):
        """
        获取消息 hash，若已缓存则直接返回。

        :param msg: 微信消息对象
        :type msg: Message
        :return: SHA256 哈希值（hex）
        :rtype: str
        """        
        if hasattr(msg, "_hash_cache"):
            return msg._hash_cache
        
        # 使用 wxauto4 自带的 msg.id 作为唯一标识（跨调用稳定，且不同图片不会碰撞）
        key = getattr(msg, 'id', None)
        if not key:
            msg_time = getattr(msg, 'time', '')
            key = f"{msg.sender}|{msg.content}|{msg.attr}|{msg.type}|{msg_time}"
        h = hashlib.sha256(key.encode("utf-8")).hexdigest()
        msg._hash_cache = h
        return h

class MessageComparer:
    """
    基于哈希的消息增量检测（diff & merge）。

    功能：
        - 找到 new_list 相对于 old_list 的新增消息
        - 自动处理"尾部最长匹配"
        - 动态最小匹配长度：min(3, len(old_list))

    返回：
        (new_items, merged_list)
    """
    def __init__(self):
        """
        初始化 comparer，内含 MessageHasher。

        :return: None
        """        
        self.hasher = MessageHasher()

    def is_same(self, a, b):
        """
        判断两个消息对象是否相同（通过 hash）。

        :param a: 消息 A
        :param b: 消息 B
        :return: 是否相同
        :rtype: bool
        """
        # 如果a和b消息来自同一个发送者，且a或b是一个"撤回"消息，则认为a与b是同一个消息
        # 这么做是避免，当某个人撤回消息的时候，系统会认为出现了很多新消息，其实不然
        if hasattr(a, "content") and hasattr(b, "content") and hasattr(a, "sender_remark") and hasattr(b, "sender_remark"):
            if a.content == "ANY" and a.sender_remark == b.sender_remark:
                return True
            if b.content == "ANY" and b.sender_remark == a.sender_remark:
                return True

        return self.hasher.get_hash(a) == self.hasher.get_hash(b)

    def diff_and_merge(self, old_list, new_list):
        """
        比较 old_list 与 new_list，返回新增项。

        算法：
            - 尝试从 old_list 尾部寻找最长连续匹配段
            - 匹配成功：之后的部分为新增
            - 匹配失败：new_list 全部为新增

        :param old_list: 旧消息列表
        :type old_list: list
        :param new_list: 新消息列表
        :type new_list: list
        :return: (新增消息列表, 合并后的消息列表)
        :rtype: tuple[list, list]
        """
        max_tail_len = 0
        match_pos = -1

        # ⭐ 新增：根据 old_list 长度确定最小匹配长度
        if len(old_list) == 0:
            min_match_len = 0
        else:
            min_match_len = min(3, len(old_list))

        # 寻找 old_list 的"最长尾部"匹配
        for tail_len in range(1, len(old_list) + 1):

            # ⭐ 不满足最小匹配长度 → 忽略
            if tail_len < min_match_len:
                continue

            tail = old_list[-tail_len:]

            for i in range(len(new_list) - tail_len + 1):
                segment = new_list[i:i + tail_len]

                if all(self.is_same(tail[k], segment[k]) for k in range(tail_len)):
                    # 找到匹配
                    max_tail_len = tail_len
                    match_pos = i + tail_len
                    break

        # 无有效匹配 → new_list 全部是新增
        if max_tail_len == 0:
            new_items = new_list
            merged = old_list + new_items
            return new_items, merged

        # 有有效匹配 → 匹配之后部分是新增
        new_items = new_list[match_pos:]
        merged = old_list + new_items
        return new_items, merged

class MessageDiffFast:
    """
    高性能消息增量检测（滚动哈希版）。

    特点：
        - 将 SHA256 的前 64 位转换为整数，提升速度
        - 使用 BASE=257 的 rolling hash
        - 时间复杂度 O(n)
        - 自动使用动态最小匹配规则 min(3, len(old_list))

    用途：
        适用于高频消息的实时 diff 检测（如机器人主循环）。
    """
    BASE = 257
    MOD = 2**64   # 使用 64bit 自然溢出，提高速度

    def __init__(self):
        """
        初始化滚动哈希 diff 模块。

        :return: None
        """        
        self.hasher = MessageHasher()

    def _list_hashes(self, msg_list):
        """
        返回 msg_list 的 SHA256 哈希字符串列表。

        :param msg_list: 消息列表
        :type msg_list: list
        :return: 哈希字符串列表
        :rtype: list[str]
        """        
        return [self.hasher.get_hash(m) for m in msg_list]

    def _rolling_hash(self, hash_list):
        """
        将 SHA256 字符串的前 16 hex（64bit）转换为整数，用于滚动哈希。

        :param hash_list: 哈希字符串列表
        :type hash_list: list[str]
        :return: 64bit 整数数组
        :rtype: list[int]
        """
        nums = []
        for h in hash_list:
            nums.append(int(h[:16], 16))  # 取前 64bit
        return nums

    def diff_and_merge(self, old_list, new_list):
        """
        高性能 diff 检测。

        步骤：
            1. 将消息转换为滚动哈希序列
            2. 计算 old_list 各尾部的哈希
            3. 使用 rolling hash 在 new_list 中寻址匹配
            4. 匹配成功 → 返回新增部分
            5. 匹配失败 → new_list 全部视为新增

        :param old_list: 旧消息列表
        :type old_list: list
        :param new_list: 新消息列表
        :type new_list: list
        :return: (新增消息, 合并后的消息列表)
        :rtype: tuple[list, list]
        """        
        A = self._rolling_hash(self._list_hashes(old_list))
        B = self._rolling_hash(self._list_hashes(new_list))

        n = len(A)
        m = len(B)

        # ⭐ 用户的新规则：动态最小匹配长度
        if n == 0:
            min_match_len = 0
        else:
            min_match_len = min(3, n)

        # 预计算 power(BASE, tail_len)
        power = 1
        for _ in range(n):
            power = (power * self.BASE) & 0xFFFFFFFFFFFFFFFF

        # ⭐ 从最长尾部开始尝试
        for tail_len in range(n, 0, -1):

            # ⭐ 不满足最小匹配长度 → 忽略该长度
            if tail_len < min_match_len:
                continue

            # old_list 的尾部序列
            tail = A[-tail_len:]

            # 计算 tail 的哈希
            target_hash = 0
            for x in tail:
                target_hash = (target_hash * self.BASE + x) & 0xFFFFFFFFFFFFFFFF

            # 滚动匹配 new_list
            window_hash = 0
            for i in range(m):
                window_hash = (window_hash * self.BASE + B[i]) & 0xFFFFFFFFFFFFFFFF

                if i >= tail_len:
                    window_hash = (window_hash -
                                   (B[i - tail_len] * power)) & 0xFFFFFFFFFFFFFFFF

                # 匹配成功
                if i >= tail_len - 1 and window_hash == target_hash:
                    match_end = i + 1
                    new_items = new_list[match_end:]
                    merged = old_list + new_items
                    return new_items, merged

        # ⭐ 没有匹配（或匹配长度不够）→ 全部当新增
        new_items = new_list
        merged = old_list + new_items
        return new_items, merged
