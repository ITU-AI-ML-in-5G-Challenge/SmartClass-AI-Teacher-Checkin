"""
稳定封装 wxauto4 与微信窗口操作的工具模块。

主要功能：
    - 自动搜索并定位微信主窗口
    - 将微信窗口安全置顶
    - 安全加载聊天记录（滚动、延迟等）
    - 安全打开群聊界面
    - 安全获取消息列表（异常兜底）
    - 提供夜间安全发送机制（深夜不发 → 入队；白天自动清理队列）
    - 真实发送消息时支持随机延迟、防检测机制、重试机制
    - 统一通过 get_logger() 输出彩色日志

模块适用场景：
    - 打卡导出程序
    - 自动点评机器人
    - 任何需要稳定控制微信客户端的自动化工具
"""
from collections import deque
from logger import get_logger
from message import _BOT_ACCOUNTS
import pyautogui
import pyperclip
import time
from datetime import datetime
import win32gui, win32con
import random
import uiautomation as auto
from wechat_compat import get_wechat_main_window, detect_wechat_version

SCROLL_PIXEL = 100 # 每次滚动量（滚轮 notch 数，1 notch = WHEEL_DELTA=120）
SCROLL_INTERVAL = 0.3 # 每次滚动后等待秒数
SCROLL_TIMES = 99999 # 滚动次数上限（极大值，实际由日期检测提前终止）

# ---- SendInput 鼠标滚轮相关常量 ----
WHEEL_DELTA = 120
# 每次 SendInput 发送的最大 notch 数，避免单次事件被截断
SENDINPUT_CHUNK_NOTCHES = 20
# chunk 间间隔（秒）
SENDINPUT_CHUNK_INTERVAL = 0.015
# SendInput 用到的 Win32 常量
INPUT_MOUSE = 0
MOUSEEVENTF_WHEEL = 0x0800

# 微信窗口类名（不同版本可能不同，以逗号分隔）
_WECHAT_CLASS_NAMES = ('WeChatMainWndForPC', 'Chrome_WidgetWin_0')

def find_wechat_window():
    """
    使用 uiautomation 查找微信主窗口。

    由于微信版本升级后窗口类名可能从 WeChatMainWndForPC 变为
    Chrome_WidgetWin_0，优先匹配 Name 包含"微信"，其次检查类名。

    :return: uiautomation Control 对象或 None
    :rtype: auto.Control or None
    """
    for w in auto.GetRootControl().GetChildren():
        if '微信' in w.Name or w.ClassName in _WECHAT_CLASS_NAMES:
            return w
    return None

def move_mouse_to_chat_center():
    # 通过 uiautomation 定位微信主窗口
    win = find_wechat_window()
    if not win:
        for cls in _WECHAT_CLASS_NAMES:
            win = auto.WindowControl(ClassName=cls)
            if win.Exists():
                break
        else:
            return

    if not win or not win.Exists():
        return

    win_rect = win.BoundingRectangle

    # 获取左侧“会话”列表
    session_list = win.ListControl(Name='会话')
    if not session_list.Exists():
        # 有些版本 Name 可能取不到，就退而求其次用第一个 ListControl
        session_list = win.ListControl(foundIndex=1)
        if not session_list.Exists():
            return

    session_rect = session_list.BoundingRectangle

    # 计算右侧聊天区域的大致中心点
    cx = (session_rect.right + win_rect.right) // 2
    cy = (session_rect.top + session_rect.bottom) // 2

    # 把鼠标移过去
    pyautogui.moveTo(cx, cy)

def safe_get_my_group_nickname(wx, logger = None):
    """
    安全获取当前微信账号的群昵称（GetMyInfo）。

    :param wx: wxauto4.WeChat 实例
    :param logger: 可选 logger
    :return: 昵称字符串或 None
    :rtype: str or None
    """    
    try:
        # 记录鼠标位置
        x, y = pyautogui.position()

        myinfo = wx.GetMyInfo()

        # 恢复鼠标位置
        time.sleep(0.02)  # 给微信一点时间结束鼠标动作
        pyautogui.moveTo(x, y)

        return myinfo.get("nickname", None)
    except Exception as e:
        get_logger(logger).error(f"无法获取自己的群昵称：{e}")
        return None

def bring_wechat_to_front(hwnd):
    """
    将微信窗口置于前台。

    先恢复可能最小化的窗口，再拉到前台。
    重装微信后部分版本对 SW_SHOW 响应异常，改用 SW_RESTORE。

    :param hwnd: 微信主窗口句柄
    :type hwnd: int
    """
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.SetForegroundWindow(hwnd)

def safe_get_wechat_window(logger = None):
    """
    安全封装版的微信窗口获取函数。

    功能：
        - 自动定位微信主窗口
        - 输出彩色日志
        - 找不到时返回 None 而非抛异常

    :param logger: 可选 logger
    :return: 微信主窗口句柄或 None
    :rtype: int or None
    """    
    try:
        hwnd = get_wechat_main_window()
        if hwnd:
            get_logger(logger).info(f"找到微信窗口句柄：{hwnd}")
            return hwnd
        else:
            get_logger(logger).error("未找到微信主窗口！请确保微信已打开。")
    except Exception as e:
        get_logger(logger).error(f"获取微信主窗口失败：{e}")
    return None

def safe_bring_wechat_to_front(hwnd, logger = None):
    """
    安全将微信窗口置顶，不会因异常导致程序崩溃。

    :param hwnd: 微信窗口句柄
    :type hwnd: int
    :param logger: 日志对象
    :type logger: logging.Logger or None
    """    
    try:
        bring_wechat_to_front(hwnd)
        get_logger(logger).info("已将微信窗口置于最前。")
    except Exception as e:
        get_logger(logger).error(f"无法将微信置顶：{e}")

def _send_mousewheel(total_notches, logger=None):
    """
    通过 SendInput（现代 Win32 API）发送鼠标滚轮事件。

    为什么不用 pyautogui.scroll()：
      - pyautogui 内部用 mouse_event()（已废弃 20+ 年的 API），将 clicks 直接当 dwData
        传给 mouse_event(MOUSEEVENTF_WHEEL)，不乘 WHEEL_DELTA(120)。
      - 微信是 Electron/CEF 应用，对废弃 API 发出的大 dwData 值可能做了截断，
        导致无论传 5000 还是 30000，实际滚动量都很小。

    为什么不用 PostMessage(WM_MOUSEWHEEL)：
      - PostMessage 绕过 Windows 输入管道，微信的 CEF 渲染引擎收不到。

    本函数使用 SendInput（Windows 推荐的输入注入 API），将总量拆分为多个小 chunk，
    每个 chunk = SENDINPUT_CHUNK_NOTCHES × WHEEL_DELTA，确保每一份都可靠生效。

    :param total_notches: 总 notch 数（正值=向上/更早, 负值=向下/更新）。
                          1 notch = WHEEL_DELTA = 120，默认约 3 行。
    :param logger: 日志器
    """
    import ctypes
    from ctypes import wintypes

    if total_notches == 0:
        return

    # -- SendInput 结构体定义 --
    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class INPUT(ctypes.Structure):
        _fields_ = [
            ("type", wintypes.DWORD),
            ("mi", MOUSEINPUT),
        ]

    user32 = ctypes.windll.user32

    def _send_one_wheel_event(notches):
        """发送单次 MOUSEEVENTF_WHEEL，notches 为滚动 notch 数（负值=向下）。"""
        inp = INPUT()
        inp.type = INPUT_MOUSE
        inp.mi.dwFlags = MOUSEEVENTF_WHEEL
        inp.mi.mouseData = notches * WHEEL_DELTA
        # dx/dy 不需要设，滚轮事件不依赖绝对坐标
        # dwExtraInfo 可留空（NULL）
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

    sign = 1 if total_notches > 0 else -1
    abs_notches = abs(total_notches)
    full_chunks = abs_notches // SENDINPUT_CHUNK_NOTCHES
    remainder = abs_notches % SENDINPUT_CHUNK_NOTCHES

    for _ in range(full_chunks):
        _send_one_wheel_event(sign * SENDINPUT_CHUNK_NOTCHES)
        time.sleep(SENDINPUT_CHUNK_INTERVAL)

    if remainder > 0:
        _send_one_wheel_event(sign * remainder)

    total_chunks = full_chunks + (1 if remainder > 0 else 0)
    direction = "↑" if total_notches > 0 else "↓"
    get_logger(logger).info(
        f"SendInput 滚轮 {direction} => {abs_notches} notch，分 {total_chunks} 块，"
        f"每块 {SENDINPUT_CHUNK_NOTCHES} notch（{SENDINPUT_CHUNK_NOTCHES * WHEEL_DELTA} mouseData）"
    )

def _safe_scroll(pixels, desc="滚动", logger = None):
    """
    执行一次鼠标滚轮滚动，向上翻看更早的消息。

    使用鼠标滚轮替代 PageUp，避免翻页过大导致中间消息被跳过。
    滚动前自动将鼠标移到聊天区域中心。

    通过 SendInput（现代 Win32 API）发送鼠标滚轮事件，绕过 pyautogui.scroll()
    在 Windows 上的已知问题。

    :param pixels: 滚动 notch 数（正值=向上翻看更早的消息）
    :type pixels: int
    :param desc: 日志描述
    :type desc: str
    :param logger: 日志器
    """
    try:
        move_mouse_to_chat_center()
        time.sleep(0.05)
        _send_mousewheel(pixels, logger)
        get_logger(logger).info(f"{desc}：滚轮 {pixels} notch")
    except Exception as e:
        get_logger(logger).warning(f"{desc}失败：{e}")

def safe_scroll(scroll_times = SCROLL_TIMES, logger = None):
    """
    连续执行 scroll，用于加载聊天记录的历史消息。

    默认向上滚动 SCROLL_TIMES 次，每次 SCROLL_PIXEL 像素。

    :param scroll_times: 滚动次数
    :type scroll_times: int
    :param logger: 日志器
    :return: None
    """
    # ------------------------------
    # 向上滚动加载历史聊天
    # ------------------------------
    for i in range(scroll_times):
        _safe_scroll(SCROLL_PIXEL, f"向上滚动({i+1}/{scroll_times})", logger)
        time.sleep(SCROLL_INTERVAL)

def _sort_and_dedupe_messages(msgs):
    """
    对微信 4.x 滚动采集的消息按时间去重（仅 4.x 路径调用，3.x 路径保持不变）。

    微信 4.x 滚动过程中 GetAllMessage 会返回重复、且时间分割条错位的消息：
      - 时间分割条（type/attr == time）重复出现，个别重复项位置错乱；
      - 图片/文本等普通消息因滚动窗口重叠而重复出现。

    去重规则：
      - 时间分割条按 msg.time 去重（同一时间点只保留首次出现）；
      - 普通消息按 (时间桶, sender, content, attr, type) 去重，同一消息只保留首次出现。
        其中「时间桶」= 最近一个时间分割条的时间，用来区分同一人在不同时间段
        发出的不同消息。

    注意：普通消息不能用 msg.id（MessageHasher.get_hash）去重。wxauto4 的
    msg.id 是 UIA runtimeid（md5(控件矩形尺寸 + content + GetRuntimeId())），
    图片消息的 content 都是"图片"、矩形尺寸也相同，唯一区别是 GetRuntimeId()，
    而滚动时 runtimeid 会被复用 → 不同图片碰撞成同一个 msg.id，导致图片消息
    被误判为重复而整体丢失（29118 的图片就是这样被误删的）。

    同时，去重键也不能只用 (sender, content, attr, type)：图片消息的 content
    恒为"图片"，同一个人在不同时间段（如昨晚 22:12 与今早 08:24）各发一张打卡图，
    会被合并成一条，导致更早那条被删（29160 昨晚的打卡就是这样丢的）。因此必须
    加入时间桶，把不同时间段的图片区分开，只合并同一时间段内滚动造成的重复。

    为什么去重后序列即按时间有序：
      错位的元素本质上都是"重复项"——错位的时间分割条是此前已出现的重复，
      错位/重复的消息是滚动窗口重叠导致的重复。去掉重复项后，剩余元素保持
      首次出现的顺序，即为"新→旧"的时间有序序列。

    :param msgs: 收集到的消息列表（新→旧，含重复与错位）
    :type msgs: list
    :return: 去重、按时间有序的消息列表（新→旧）
    :rtype: list
    """
    from message import is_time_message

    seen_time = set()
    seen_hash = set()
    result = []
    current_time = None  # 最近一个时间分割条，作为普通消息的「时间桶」

    for m in msgs:
        if is_time_message(m):
            key = getattr(m, 'time', '') or getattr(m, 'content', '')
            if key in seen_time:
                continue
            seen_time.add(key)
            result.append(m)
            current_time = key
        else:
            # 用稳定键 (时间桶, sender, content, attr, type) 去重，不用 msg.id。
            # 时间桶区分同一人在不同时间段发的不同图片；同一时间段内的滚动重复仍会合并。
            key = (
                current_time,
                getattr(m, 'sender', ''),
                getattr(m, 'content', ''),
                getattr(m, 'attr', ''),
                getattr(m, 'type', ''),
            )
            if key in seen_hash:
                continue
            seen_hash.add(key)
            result.append(m)

    return result

def safe_scroll_and_collect(wx, scroll_times=SCROLL_TIMES, scroll_pixel=SCROLL_PIXEL, target_date=None, logger=None):
    """
    边滚动边收集消息，解决 wxauto4 只能获取当前可见消息的限制。

    原理：
        wx.GetAllMessage() 只能拿到微信聊天窗口当前渲染的消息（通常几十条）。
        如果先滚完再拿，中间的消息已被微信 UI 卸载，只能拿到最后停留处的可见消息。

        改进：每轮滚动后立即调用 GetAllMessage()，去重累积。

    提前终止：
        若指定 target_date，滚动中一旦检测到时间戳早于目标日期，
        会再收集一轮（确保带有前一天的消息）后提前结束，省去无效滚动。

    :param wx: wxauto4.WeChat 实例
    :param scroll_times: 滚动次数（最大）
    :type scroll_times: int
    :param scroll_pixel: 每次滚动量（滚轮 notch 数）
    :type scroll_pixel: int
    :param target_date: 目标日期，用于提前终止判断（可选）
    :type target_date: datetime.datetime or None
    :param logger: 日志器
    :return: 累积去重后的消息列表
    :rtype: list
    """
    from message import is_time_message, compare_message_date, MessageHasher

    all_msgs = []
    hasher = MessageHasher()
    seen_hashes = set()
    # 微信 3.x 滚动不改变已渲染的消息集合，每轮 GetAllMessage 读到同一批，
    # 必须按 msg.id 去重；微信 4.x 每轮读到新消息，保持原有直接追加行为不变。
    _is_3x = (detect_wechat_version() == '3')

    def collect_batch(label=""):
        """获取当前可见消息，反转后追加到 all_msgs。

        GetAllMessage 返回旧→新（微信窗口从上到下），反转后新→旧。
        3.x：每轮读到同一批，按 msg.id（runtimeid 哈希）去重；
        4.x：每轮读到新消息，直接追加（与改动前一致）。

        第 0 轮 = 最新消息（底部），后续 PageUp = 更旧区域。
        最终结果：[0] = 最新，末尾 = 最旧。
        """
        try:
            batch = wx.GetAllMessage()
            if batch:
                if _is_3x:
                    new_msgs = []
                    for msg in reversed(batch):
                        h = hasher.get_hash(msg)
                        if h not in seen_hashes:
                            seen_hashes.add(h)
                            new_msgs.append(msg)
                    all_msgs.extend(new_msgs)
                    get_logger(logger).info(
                        f"  收集到 {len(batch)} 条消息"
                        f"（新增 {len(new_msgs)}，累计 {len(all_msgs)} 条）{label}"
                    )
                else:
                    all_msgs.extend(reversed(batch))
                    get_logger(logger).info(
                        f"  收集到 {len(batch)} 条消息（累计 {len(all_msgs)} 条）{label}"
                    )
            return batch or []
        except Exception as e:
            get_logger(logger).warning(f"  收集消息失败：{e}")
            return []

    # 首先定位到最新消息（底部），确保从最新位置开始收集
    # ⚠ 必须先点击聊天区域获取焦点，否则 End 键会被输入框吃掉
    move_mouse_to_chat_center()
    time.sleep(0.1)
    pyautogui.click()
    time.sleep(0.3)
    pyautogui.press('end')
    time.sleep(SCROLL_INTERVAL)
    get_logger(logger).info("已定位到最新消息位置")

    # 先收集当前可见的消息（最新消息）
    collect_batch("— 最新消息")

    extra_rounds = 0  # 剩余额外收集轮数（检测到早于目标日期后设为 1）
    for i in range(scroll_times):
        move_mouse_to_chat_center()
        try:
            _send_mousewheel(scroll_pixel, logger)
        except Exception as e:
            get_logger(logger).warning(f"滚动失败：{e}")
        time.sleep(SCROLL_INTERVAL)
        get_logger(logger).info(f"向上滚动({i+1}/{scroll_times})：滚轮 {scroll_pixel} notch")
        batch = collect_batch(f"— 第 {i+1} 轮")

        # 如果上一轮触发额外收集，本轮就是"额外一轮"，完成后结束
        if extra_rounds > 0:
            extra_rounds -= 1
            if extra_rounds == 0:
                get_logger(logger).info("已额外收集一轮，提前结束滚动")
                break

        # 检查是否已经滚动到早于目标日期的时间戳
        if target_date and extra_rounds == 0 and batch:
            for msg in batch:
                if is_time_message(msg) and compare_message_date(msg, target_date) < 0:
                    get_logger(logger).info(
                        f"检测到早于目标日期的消息（{msg.time}），再收集一轮后结束"
                    )
                    extra_rounds = 1
                    break

    # 兜底：如果在最后一轮才检测到，循环已结束，补一轮
    if extra_rounds > 0:
        move_mouse_to_chat_center()
        try:
            _send_mousewheel(scroll_pixel, logger)
        except Exception as e:
            get_logger(logger).warning(f"滚动失败：{e}")
        time.sleep(SCROLL_INTERVAL)
        get_logger(logger).info(f"向上滚动(额外)：滚轮 {scroll_pixel} notch")
        collect_batch("— 额外一轮")

    get_logger(logger).info(f"滚动收集完成，共获取 {len(all_msgs)} 条消息")
    # 微信 4.x：滚动采集到的消息存在重复与时间分割条错位，去重后即恢复时间有序；
    # 微信 3.x：滚动不改变已渲染集合，collect_batch 已按 msg.id 去重，无需再处理。
    if not _is_3x:
        all_msgs = _sort_and_dedupe_messages(all_msgs)
        get_logger(logger).info(f"按时间去重后：{len(all_msgs)} 条消息")
    return all_msgs

def safe_open_chat(wx, groupname, logger = None):
    """
    安全打开指定群聊窗口。

    :param wx: WeChat 实例
    :type wx: wxauto4.WeChat
    :param groupname: 群名称
    :type groupname: str
    :param logger: 日志器
    :return: 是否成功
    :rtype: bool
    """    
    try:
        # 关键：必须 "真正切换" 到目标群，不能是 no-op。
        # 如果当前已在目标群，ChatWith(groupname) 可能是空操作，
        # 聊天视图不会刷新，press('end') 也就不会跳到最新消息。
        # 所以：先从会话列表中找一个 "不是目标群" 的会话切过去，
        # 保证 ChatWith(groupname) 一定是真正的切换。
        try:
            sessions = wx.GetSession()  # 获取所有会话
            other = None
            for s in sessions:
                # 兼容三种返回类型：字符串、dict、SessionElement
                if isinstance(s, str):
                    name = s
                elif hasattr(s, 'name'):
                    name = s.name
                else:
                    name = s.get('nickname', '')
                if name and name != groupname:
                    other = name
                    break
            if other:
                wx.ChatWith(other)
                get_logger(logger).info(f'先切换到“{other}”以确保后续切回是真正的切换')
            else:
                # 只有一个会话（就是目标群），没办法切走，只能直接 ChatWith
                pass
        except Exception:
            # GetSession 失败时的兜底：依次尝试所有打卡工作账号
            for fallback_name in _BOT_ACCOUNTS:
                try:
                    wx.ChatWith(fallback_name)
                    break
                except Exception:
                    continue

        wx.ChatWith(groupname)  # 现在一定是真正的切换！
        time.sleep(1)            # 等微信渲染完
        # ⚠ 必须先点击聊天区域获取焦点，否则 End 键会被输入框吃掉
        move_mouse_to_chat_center()
        time.sleep(0.1)
        pyautogui.click()
        time.sleep(0.3)
        pyautogui.press('end')   # 定位到最底部（最新消息）
        get_logger(logger).info(f'已打开微信群：”{groupname}”，已定位到最新消息')
        return True
    except Exception as e:
        get_logger(logger).error(f"无法打开群 {groupname}：{e}")
        return False

def safe_get_all_messages(wx, logger = None):
    """
    安全获取当前群聊的所有消息。

    特性：
        - 若返回 None → 自动视为空列表
        - 若异常 → 返回空列表
        - 自动输出消息条数

    :param wx: wxauto4.WeChat 实例
    :param logger: 日志器
    :return: 消息列表
    :rtype: list
    """    
    try:
        msgs = wx.GetAllMessage()
        if msgs is None:
            get_logger(logger).warning("GetAllMessage() 返回 None，已当成空列表处理")
            return []
        get_logger(logger).info(f"成功获取消息记录，共 {len(msgs)} 条")
        return msgs
    except Exception as e:
        get_logger(logger).error(f"获取群消息失败：{e}")
        return []

# 全局队列
SEND_QUEUE = deque()

def safe_clean_msg_queue(wx, *,
                        max_retry=3,
                        min_delay=1.2,
                        max_delay=3.5,
                        night_start=0,
                        night_end=6,                        
                        logger=None):
    """
    白天执行队列消息发送；深夜不发送。

    特性：
        - 深夜：不发送，只打印日志
        - 白天：依次取出队列中的消息，调用真实发送函数
        - 每条消息支持重试 + 随机延迟

    :param wx: WeChat 实例
    :param max_retry: 最大重试次数
    :type max_retry: int
    :param min_delay: 最小随机延迟秒
    :type min_delay: float
    :param max_delay: 最大随机延迟秒
    :type max_delay: float
    :param night_start: 夜间开始小时
    :type night_start: int
    :param night_end: 夜间结束小时
    :type night_end: int
    :param logger: 日志器
    :return: None
    """    
    def log(text):
        get_logger(logger).info(text)

    now_hour = datetime.now().hour

    # ----------------------------
    # ① 判断是否在深夜
    # ----------------------------
    if night_start <= now_hour < night_end:
        log(f"[safe_clean_msg_queue] 深夜 {now_hour} 点，消息队列不处理")
        return

    # ----------------------------
    # ② 非深夜 → 清理队列
    # ----------------------------
    if SEND_QUEUE:
        log(f"[safe_clean_msg_queue] 当前时间安全（{now_hour}点），开始发送 {len(SEND_QUEUE)} 条队列消息...")
        while SEND_QUEUE:
            old_msg, old_recv = SEND_QUEUE.popleft()
            _real_send(wx, old_msg, old_recv, max_retry, min_delay, max_delay, logger)
    else:
        log("[safe_clean_msg_queue] 消息队列为空无需处理")

def safe_send_msg_queue(wx, msg, receiver, *,
                        max_retry=3,
                        min_delay=1.2,
                        max_delay=3.5,
                        night_start=0,
                        night_end=6,
                        logger=None):
    """
    带夜间队列的安全发送消息函数。

    逻辑：
        - 若在深夜（night_start~night_end）→ 将消息写入队列、不发送
        - 若在白天 → 先清空队列，再发送当前消息
        - 调用内部 _real_send 实现真实发送

    :param wx: WeChat 实例
    :param msg: 要发送的消息文本
    :param receiver: 群名或好友名
    :param max_retry: 最大重试次数
    :param min_delay: 最小延迟
    :param max_delay: 最大延迟
    :param night_start: 夜间开始小时
    :param night_end: 夜间结束小时
    :param logger: 日志器
    :return: 是否发送成功
    :rtype: bool
    """

    def log(text):
        get_logger(logger).info(text)

    now_hour = datetime.now().hour

    # ----------------------------
    # ① 判断是否在深夜
    # ----------------------------
    if night_start <= now_hour < night_end:
        SEND_QUEUE.append((msg, receiver))
        log(f"[safe_send_msg_queue] 深夜 {now_hour} 点，消息已加入队列，不立即发送: {msg!r}")
        return True

    # ----------------------------
    # ② 非深夜 → 先清理队列
    # ----------------------------
    safe_clean_msg_queue(wx,
                         max_retry=max_retry,
                         min_delay=min_delay,
                         max_delay=max_delay,
                         night_start=night_start,
                         night_end=night_end,
                         logger=logger)

    # ----------------------------
    # ③ 发送当前消息
    # ----------------------------
    return _real_send(wx, msg, receiver, max_retry, min_delay, max_delay, logger)

def safe_send_msg_immediately(wx, msg, receiver, *,
                        max_retry=3,
                        min_delay=1.2,
                        max_delay=3.5,
                        logger=None):
    """
    立即发送消息（不考虑时间，不入队列）。

    用于统计结果、人工触发、非机器自动消息。

    :param wx: WeChat 实例
    :param msg: 消息文本
    :param receiver: 接收者
    :param max_retry: 最大重试次数
    :param min_delay: 最小延迟
    :param max_delay: 最大延迟
    :param logger: 日志器
    :return: 是否成功
    :rtype: bool
    """    
    # ----------------------------
    # 立刻发送当前消息
    # ----------------------------    
    return _real_send(wx, msg, receiver, max_retry, min_delay, max_delay, logger)

def _real_send(wx, msg, receiver, max_retry, min_delay, max_delay, logger):
    """
    真实的消息发送逻辑（safe_send_msg_queue / safe_send_msg_immediately 均调用本函数）。

    方法：
        先尝试 wx.SendMsg（旧版微信可用），若抛异常则重试；
        所有重试耗尽后，走剪贴板 + Ctrl+V + Enter 兜底。

    :param wx: WeChat 实例
    :param msg: 文本内容
    :param receiver: 群名或好友名
    :param max_retry: 最大重试次数
    :param min_delay: 最小延迟秒
    :param max_delay: 最大延迟秒
    :param logger: 日志器
    :return: 是否成功
    :rtype: bool
    """
    if not receiver:
        log("[_real_send] 发送对象为空，放弃发送")
        return False

    def log(text):
        get_logger(logger).info(text)

    # 随机延迟
    delay = random.uniform(min_delay, max_delay)
    log(f"[_real_send] 延迟 {delay:.2f}s 后发送...")
    time.sleep(delay)

    # ── 方法1：wx.SendMsg（仅旧版 WeChatMainWndForPC 可用）─
    #     新版 Chrome_WidgetWin_0 类名下 SendMsg 不抛异常但
    #     静默失败，跳过直接走剪贴板。
    class_name = _get_wechat_class_name()
    if class_name in ('WeChatMainWndForPC',):
        for attempt in range(1, max_retry + 1):
            try:
                log(f"[_real_send] SendMsg 第 {attempt} 次 → {receiver}")
                wx.SendMsg(msg, receiver)
                log("[_real_send] SendMsg 成功")
                return True
            except Exception as e:
                log(f"[_real_send] SendMsg 失败：{e}")
                if attempt < max_retry:
                    time.sleep(random.uniform(2, 5))
    else:
        log(f"[_real_send] 微信类名={class_name}，跳过 SendMsg，直接走剪贴板")

    # ── 方法2：剪贴板 + Ctrl+V + Enter ─────────────────
    for attempt in range(1, max_retry + 1):
        try:
            log(f"[_real_send] 剪贴板 第 {attempt} 次 → {receiver}")

            # 点击输入框（微信窗口底部中间偏上）
            rect = _get_wechat_rect()
            if rect:
                cx = (rect[0] + rect[2]) // 2
                cy = rect[3] - 100
                pyautogui.click(cx, cy)
                time.sleep(0.15)

            # 粘贴
            pyperclip.copy(msg)
            time.sleep(0.1)
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.2)

            # 发送
            pyautogui.hotkey('enter')
            log("[_real_send] 剪贴板发送成功")
            return True
        except Exception as e:
            log(f"[_real_send] 剪贴板发送失败：{e}")
            if attempt < max_retry:
                time.sleep(random.uniform(2, 5))

    log("[_real_send] 最终失败，放弃发送")
    return False


def _get_wechat_rect():
    """获取微信主窗口屏幕坐标 (left, top, right, bottom)，失败返回 None。"""
    try:
        hwnd = get_wechat_main_window()
        if hwnd:
            return win32gui.GetWindowRect(hwnd)
    except Exception:
        pass
    return None


def _get_wechat_class_name():
    """获取微信主窗口的类名，失败返回空字符串。"""
    try:
        hwnd = get_wechat_main_window()
        if hwnd:
            return win32gui.GetClassName(hwnd)
    except Exception:
        pass
    return ''


# 微信窗口最佳尺寸（保证消息列表渲染完整，避免因窗口过小导致漏消息）
_WECHAT_OPTIMAL_WIDTH = 2292
_WECHAT_OPTIMAL_HEIGHT = 2086


def set_wechat_window_size(logger=None):
    """
    将微信窗口调整到最佳尺寸，确保消息列表区域足够大。

    窗口过小时微信可能只渲染部分消息，导致 GetAllMessage()
    漏收。Python 控制台排左侧，微信排右侧，互不遮挡。

    :param logger: 日志器
    """
    log = get_logger(logger)
    try:
        hwnd = get_wechat_main_window()
        if not hwnd:
            log.warning("未找到微信窗口，无法调整大小")
            return

        rect = win32gui.GetWindowRect(hwnd)
        cur_w, cur_h = rect[2] - rect[0], rect[3] - rect[1]

        # 如果窗口最小化，先恢复
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.3)

        # ── 获取工作区尺寸（排除任务栏）───────────────
        import ctypes
        from ctypes import wintypes
        work_area = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work_area), 0)
        work_x = work_area.left
        work_y = work_area.top
        work_w = work_area.right - work_area.left
        work_h = work_area.bottom - work_area.top

        # ── Python 控制台 → 排在屏幕左侧，宽度收窄 ────
        console = ctypes.windll.kernel32.GetConsoleWindow()
        console_w = work_w // 4 if console else 0
        if console:
            win32gui.SetWindowPos(console, 0, work_x, work_y, console_w, work_h,
                win32con.SWP_NOZORDER | win32con.SWP_SHOWWINDOW)

        # ── 微信 → 排在右侧，顶部对齐工作区 ──────────
        # 目标尺寸：优先最佳值（保证消息列表渲染完整），但不超出工作区
        target_w = min(_WECHAT_OPTIMAL_WIDTH, work_w - console_w)
        target_h = min(_WECHAT_OPTIMAL_HEIGHT, work_h)
        new_x = work_x + max(console_w, work_w - target_w)
        new_y = work_y

        win32gui.SetWindowPos(
            hwnd, 0,
            new_x, new_y, target_w, target_h,
            win32con.SWP_NOZORDER | win32con.SWP_SHOWWINDOW,
        )

        if cur_w != target_w or cur_h != target_h:
            log.info(
                f"微信窗口已调整：{cur_w}x{cur_h} → "
                f"{target_w}x{target_h}"
            )
        else:
            log.info(
                f"微信窗口尺寸正确：{target_w}x{target_h}"
            )
        time.sleep(0.3)
    except Exception as e:
        log.warning(f"调整微信窗口大小失败：{e}")

def bring_console_to_front(logger=None):
    """
    将当前 Python 控制台窗口恢复到前台。

    在微信消息收集完成后调用，让焦点回到控制台，
    避免用户每次都要手动点击控制台来继续交互（Y/N）。

    :param logger: 日志器
    """
    log = get_logger(logger)
    try:
        import ctypes
        console = ctypes.windll.kernel32.GetConsoleWindow()
        if console:
            win32gui.SetForegroundWindow(console)
            log.info("控制台窗口已恢复前台")
    except Exception as e:
        log.warning(f"恢复控制台窗口失败：{e}")


def hide_tkinter_root():
    """
    隐藏 wxauto4 初始化时可能创建的 Tkinter 白色空白窗口。
    在 WeChat() 初始化之后调用。

    wxauto4 41.1.7 起内部创建 Tk 窗口的方式有变化，_default_root 可能
    拿不到，因此额外枚举 TkTopLevel 类名窗口用 Win32 隐藏兜底。
    """
    # 方法1：标准 _default_root（旧版 wxauto4 有效）
    try:
        import tkinter as tk
        root = getattr(tk, '_default_root', None)
        if root is not None:
            root.withdraw()
    except Exception:
        pass

    # 方法2：兜底——枚举 TkTopLevel 空白窗口隐藏（仅无标题或标题"tk"，避免误伤其他 Tk 程序）
    try:
        def _cb(hwnd, _):
            if win32gui.GetClassName(hwnd) == 'TkTopLevel':
                title = win32gui.GetWindowText(hwnd)
                if not title or title == 'tk':
                    win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
        win32gui.EnumWindows(_cb, None)
    except Exception:
        pass
