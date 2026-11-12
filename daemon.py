"""
自动微信打卡点评机器人守护进程。

主要功能：
    - 监听微信群消息（图片、文字等）
    - 自动下载学生打卡图片
    - 调用星火（Spark）与通义千问（Qwen VL）模型生成点评
    - 自动发送点评结果到微信群
    - 内置极致鲁棒逻辑：微信掉线自动重连、渲染等待、消息稳定性判断
    - 支持每日定时触发打卡统计(export + update)
    - 支持 ESC 优雅退出、Ctrl+C 退出、心跳日志、安全队列清理
    - 内置安全下载策略、滚动预加载机制、文件完整性检查

依赖模块：
    - wxauto4：与 Windows 微信客户端交互
    - sparkreview / qwenvlreview：AI 点评模型
    - safewechat：稳定版微信 API
    - checkin：每日统计任务
    - message：消息 diff、过滤与日志工具

本模块作为完整的后台守护进程入口，可独立运行。
"""
import os
import sys
import time
import traceback
import argparse


def _build_parser():
    """构建命令行参数解析器（仅依赖 argparse，供 --help 提前退出复用）。"""
    return argparse.ArgumentParser(
        description="自动微信打卡点评机器人守护进程。",
    )


# 提前处理 --help / -h：只打印帮助即退出，避免加载重模块
if "--help" in sys.argv or "-h" in sys.argv:
    _build_parser().print_help()
    sys.exit(0)


import pyautogui
from pathlib import Path
from wechat_compat import WeChat
import msvcrt
from version import check_version_or_exit
import logging
from datetime import datetime, timedelta
from sparkreview import SparkImageReviewer
from qwenvlreview import QwenVLReviewer
from message import MessageComparer, log_messages, filter_messages
from safewechat import safe_get_wechat_window, safe_bring_wechat_to_front, safe_get_my_group_nickname, safe_clean_msg_queue, safe_send_msg_queue, move_mouse_to_chat_center, hide_tkinter_root
from message import is_pic_message, is_text_message, get_sender_name
from checkin import get_next_run_time, run_export_for_date, run_update_for_date
from logger import setup_logger

from config import get_env

group_name = get_env("REVIEW_GROUP_NAME")
review_dir = get_env("REVIEW_IMG_DIR")
# ===================== 全局配置 =====================
POLL_INTERVAL = 2                 # 轮询间隔（秒）
HEARTBEAT_INTERVAL = 60           # 心跳日志间隔（循环次数）
MAX_IMAGE_SIZE = 8 * 1024 * 1024  # 单张图片最大允许 8MB

class AutoReviewBot:
    """
    自动微信作业点评机器人。

    功能说明：
        - 持续监听微信群消息
        - 自动识别图片消息并缓存
        - 自动下载图片，处理微信缓存延迟、滚动预加载、重试机制
        - 自动调用两个 AI 模型（星火、大模型）进行作业点评
        - 自动发送点评回复
        - 内置极强的鲁棒逻辑（断线重连、稳定等待、容错主循环）
        - 与每日打卡统计功能无缝集成

    :param wx: wxauto4 的 WeChat 实例
    :type wx: WeChat
    :param reviewer1: 星火图片点评模型
    :type reviewer1: SparkImageReviewer
    :param reviewer2: 通义千问图片点评模型
    :type reviewer2: QwenVLReviewer
    :param group_name: 要监听的微信群名称
    :type group_name: str
    :param img_dir: 下载图片存放目录
    :type img_dir: str
    :param summarize_target_hour: 下次统计指定几点
    :type summarize_target_hour: int
    :param summarize_target_minute:下次统计指定几分
    :param summarize_target_minute: int
    :param logger: 日志实例
    :type logger: logging.Logger
    :param poll_interval: 消息轮询间隔（秒）
    :type poll_interval: float
    :param heartbeat_interval: 心跳日志输出间隔（循环次数）
    :type heartbeat_interval: int
    :param max_image_size: 最大允许图片字节数
    :type max_image_size: int
    """
    def __init__(self,
                 wx: WeChat,
                 reviewer1: SparkImageReviewer,
                 reviewer2: QwenVLReviewer,
                 group_name: str,
                 img_dir: str,
                 logger: logging.Logger,
                 summarize_target_hour = 8,
                 summarize_target_minute = 0,                 
                 poll_interval: float = POLL_INTERVAL,
                 heartbeat_interval: int = HEARTBEAT_INTERVAL,
                 max_image_size: int = MAX_IMAGE_SIZE):
        """
        初始化打点评机器人，创建目录并设置各类参数。

        :return: 无返回值
        :rtype: None
        """        
        self.wx = wx
        self.reviewer1 = reviewer1
        self.reviewer2 = reviewer2
        self.group_name = group_name
        self.img_dir = img_dir
        self.logger = logger
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        self.max_image_size = max_image_size
        self.summarize_target_hour = summarize_target_hour
        self.summarize_target_minute = summarize_target_minute

        os.makedirs(self.img_dir, exist_ok=True)

        self.selfname = None
        self.running = False

    @staticmethod
    def _wait_for_messages_stable(wx, timeout=10, interval=0.3, min_wait=1.2):
        start = time.time()
        last_len = -1
        stable_count = 0
        stable_required = 3  # 连续3次相同才算稳定

        # 阶段1：最少等待 min_wait，避免前两次内容相同但未加载完
        while time.time() - start < min_wait:
            time.sleep(interval)

        # 阶段2：判断消息稳定性
        while time.time() - start < timeout:
            msgs = wx.GetAllMessage() or []
            l = len(msgs)

            if l == last_len:
                stable_count += 1
            else:
                stable_count = 0  # 又变化了，重置计数

            # 如果数量不再变化且连续达到要求次数，认为加载完成
            if stable_count >= stable_required:
                return msgs

            last_len = l
            time.sleep(interval)

        # 超时返回最后一次结果
        return wx.GetAllMessage() or []
    
    @staticmethod
    def _stablely_switch_to_group(wx, groupname):
        wx.ChatWith(groupname)
        time.sleep(1.5)  # 先给微信反应时间，因为它要花时间渲染
        return __class__._wait_for_messages_stable(wx)

    def run(self):
        """
        机器人主循环（超鲁棒版本）。

        核心功能：
            - 监控新消息，自动下载、点评、反馈
            - 自动处理微信掉线情况（wxauto4 异常 → 重连）
            - 使用“消息稳定性算法”确保消息渲染完毕后再读取
            - 每日 02:00 自动触发统计与 Excel 合并任务
            - 定时心跳日志、防卡死安全队列清理
            - 支持 ESC 优雅退出 / Ctrl+C 退出
            - 主循环任何异常都不会中断机器人运行

        :return: None
        :rtype: None
        """   

        def wx_alive():
            """检测微信是否正常"""
            try:
                sessions = self.wx.GetSession() # 最小 API 调用
                return len(sessions) > 0
            except:
                return False

        def reconnect_wx():
            """尝试重连微信"""
            self.logger.warning("正在尝试重新连接微信客户端...")
            try:
                self.wx = WeChat(ads=False, resize=False)
                hide_tkinter_root()
                self.logger.info("微信重新连接成功！")
                # 恢复监控群窗口
                try:
                    self.wx.ChatWith(self.group_name)
                except:
                    pass
                return True
            except Exception as e:
                self.logger.error(f"重新初始化微信失败：{e}")
                self.logger.debug(traceback.format_exc())
                return False
    
        # 初始化昵称
        self.selfname = safe_get_my_group_nickname(self.wx, self.logger)
        if not self.selfname:
            self.logger.error("无法识别自己的群昵称，机器人停止启动。")
            return
        
        self.logger.info(f"启动成功，开始监控微信群：{self.group_name}")
        self.running = True
        move_mouse_to_chat_center()

        comparer = MessageComparer()

        try:
            # 因为用户可能会点鼠标切到了别的群
            # 获取消息的时候，要把聊天窗口切换到监控群
            initial_msgs = filter_messages(__class__._stablely_switch_to_group(self.wx, self.group_name))
        except:
            self.logger.info(f"目前取不到消息{initial_msgs}")
            initial_msgs = []
            pass

        msg_q = {}     # sender -> 最近一条图片消息
        heartbeat = 0

        target_hour = self.summarize_target_hour
        target_minute = self.summarize_target_minute
        next_run = get_next_run_time(target_hour, target_minute)
        self.logger.info("下一次统计任务时间：%s", next_run.strftime("%Y-%m-%d %H:%M:%S"))
        exit_after_task = False

        while self.running:
            try:
                # 检测 ESC
                try:
                    if msvcrt.kbhit():
                        if msvcrt.getch() == b'\x1b':
                            self.logger.info("检测到 ESC，准备退出机器人。")
                            break
                except Exception:
                    pass

                # 统计任务开始执行
                if datetime.now() >= next_run:
                    # 执行任务阶段（ESC 只会记录退出意图，在统计后就可以优雅退出）
                    exit_after_task = False

                    target_date = next_run - timedelta(days=1)
                    self.logger.info("开始统计任务 target_date = %s", target_date.strftime("%Y-%m-%d"))

                    try:
                        run_export_for_date(target_date, self.logger)
                    except Exception as e:
                        self.logger.error("run_export_for_date() 异常：%s", e)
                        self.logger.debug(traceback.format_exc())

                    if msvcrt.kbhit() and msvcrt.getch() == b'\x1b':
                        self.logger.info("检测到 ESC（执行阶段），统计任务结束后退出。")
                        exit_after_task = True

                    try:
                        run_update_for_date(target_date, self.logger)
                        # TEST: self.logger.info("skip updating spreadsheets")
                    except Exception as e:
                        self.logger.error("run_update_for_date() 异常：%s", e)
                        self.logger.debug(traceback.format_exc())

                    next_run = get_next_run_time(target_hour, target_minute)
                    self.logger.info("下一次统计任务时间：%s", next_run.strftime("%Y-%m-%d %H:%M:%S"))
                    try:
                        self.wx.ChatWith(self.group_name)
                    except:
                        pass

                    if exit_after_task:
                        self.logger.info("统计任务完成（执行阶段按 ESC），守护进程优雅退出。")
                        break

                    self.logger.info("统计任务结束，等待下一轮。")


                heartbeat += 1
                if heartbeat % self.heartbeat_interval == 0:
                    self.logger.info("⏳ 机器人运行中（心跳正常）...试图清空发送消息队列...")
                    # 试图清空发送消息队列
                    safe_clean_msg_queue(self.wx, logger=self.logger)

                # -------------------------
                # 检查微信是否在线
                # -------------------------
                if not wx_alive():
                    self.logger.warning("检测到微信失联，进入重连流程...")
                    if not reconnect_wx():
                        time.sleep(3)
                        continue  # 下轮再尝试
                    # 重连成功后重新获取初始消息
                    try:
                        initial_msgs = filter_messages(__class__._stablely_switch_to_group(self.wx, self.group_name))
                    except:
                        self.logger.info(f"目前取不到消息{initial_msgs}")
                        initial_msgs = []
                    time.sleep(1)
                    continue

                # 获取最新消息
                try:
                    # 因为用户可能会点鼠标切到了别的群
                    # 获取消息的时候，要把聊天窗口切换到监控群
                    msgs = filter_messages(__class__._stablely_switch_to_group(self.wx, self.group_name))
                    # log_messages("初始消息集", initial_msgs, self.logger.info)
                    # log_messages("获取消息集", msgs, self.logger.info)
                    
                    new_msgs, _ = comparer.diff_and_merge(initial_msgs, msgs)
                    # 保留上一个消息列表
                    # 另外，如果正在跨了子夜12点，只保留新的一天消息也可以工作
                    
                    if new_msgs:
                        # log_messages("初始消息集", initial_msgs, self.logger.warning)
                        # log_messages("获取消息集", msgs, self.logger.warning)                        
                        log_messages("来了新消息", new_msgs, self.logger.warning)
                        
                    initial_msgs = msgs
                except Exception as e:
                    self.logger.warning(f"获取微信群消息失败，将稍后重试：{e}")
                    self.logger.debug(traceback.format_exc())
                    time.sleep(self.poll_interval)
                    continue

                if new_msgs:
                    self._process_new_messages(new_msgs, msg_q)

                time.sleep(self.poll_interval)

            # Ctrl+C = 优雅退出
            except KeyboardInterrupt:
                self.logger.info("检测到 Ctrl+C，守护进程优雅退出。")
                break
    
            except Exception as loop_err:
                # 兜底保护，不让主循环因为异常退出
                self.logger.error(f"主循环捕获到异常，已自动忽略并继续运行：{loop_err}")
                self.logger.debug(traceback.format_exc())
                time.sleep(3)

        # 退出前发送提示
        # try:
        #     self.wx.SendMsg("......结束交互......")
        # except Exception:
        #     pass

        self.logger.info("机器人已安全退出。")


    def _process_new_messages(self, new_msgs, msg_q):
        """
        处理新收到的消息。

        行为：
            - 忽略自己发的消息
            - 图片消息：加入缓存队列
            - 若图片消息包含 @机器人，则进入点评流程
            - 文本 @ 逻辑可扩展（当前注释掉）

        :param new_msgs: 新消息列表
        :type new_msgs: list
        :param msg_q: 图片缓存队列 {sender: message}
        :type msg_q: dict
        :return: None
        :rtype: None
        """        
        for m in new_msgs:
            try:
                # 自己发的消息忽略
                if getattr(m, "attr", None) == "self":
                    continue

                # 图片消息：缓存
                if is_pic_message(m):
                    msg_q[m.sender] = m
                    self._handle_mention(m, msg_q, False)
                    continue

                # 文本消息并且 @ 我
                # if is_text_message(m) and m.content.startswith('@' + self.selfname):
                #     self._handle_mention(m, msg_q)

            except Exception as e:
                self.logger.error(f"处理单条消息时出错：{e}")
                self.logger.debug(traceback.format_exc())
                # 单条消息失败不影响整个循环，继续处理下一条
                continue

    def safe_download(self, m, max_retries=3):
        """
        超鲁棒图片下载方法。

        包含三大策略：
            1. **快速重试**（解决“消息太快，图片未缓存”问题）
            2. **微信窗口滚动预加载机制**（强制微信加载旧图缓存）
            3. **文件完整性验证**（文件存在 + 大小 > 0）

        特点：
            - 每次都等待适当冷却时间
            - 不同阶段自动扩大滚动范围
            - 全程捕获异常，不影响主逻辑

        :param m: 微信消息对象（图片）
        :type m: Message
        :param max_retries: 重试次数
        :type max_retries: int
        :return: 下载后的图片路径或 None
        :rtype: str or None
        """
        time.sleep(5)
        # --------------------------
        # 0. 类型检查
        # --------------------------
        if not hasattr(m, "download"):
            return None
        if getattr(m, "type", None) != "image":
            return None
        # --------------------------
        # 1. 快速冷却重试（专治消息太快）
        # --------------------------
        for _ in range(3):
            resp = m.download(self.img_dir)

            # download 成功一般返回路径
            if isinstance(resp, Path):
                image_path = str(resp)
                if os.path.exists(image_path) and os.path.getsize(image_path) > 0:
                    self.logger.info(f"图片下载成功：{image_path}")
                    return image_path

            time.sleep(0.5)  # 冷却时间，给微信写缓存

        # --------------------------
        # 2. 全局重试 + 预加载滚动（高强度补救）
        # --------------------------
        scroll_pixels = 600

        for attempt in range(max_retries):

            # 再试一次下载
            resp = m.download(self.img_dir)

            # download 成功一般返回路径
            if isinstance(resp, Path):
                image_path = str(resp)
                if os.path.exists(image_path) and os.path.getsize(image_path) > 0:
                    self.logger.info(f"图片下载成功：{image_path}")
                    return image_path

            self.logger.warning(f"第 {attempt + 1} 次下载失败，触发微信图片预加载…")

            # 预加载滚动（让微信加载旧图缓存）
            try:
                # 获取微信窗口
                hwnd = safe_get_wechat_window(self.logger)
                if hwnd:
                    safe_bring_wechat_to_front(hwnd, self.logger)

                # 上滚加载历史
                pyautogui.scroll(scroll_pixels)
                time.sleep(0.3)

                # 回到最底部
                pyautogui.scroll(-scroll_pixels)
                time.sleep(0.2)

            except Exception as e:
                self.logger.warning(f"图片预加载过程出错（忽略）：{e}")

            # 扩大下一次滚动范围
            scroll_pixels += 300

            time.sleep(0.6)

        # --------------------------
        # 3. 最终失败
        # --------------------------
        self.logger.error("下载多次失败，放弃。")
        return None

    def _handle_mention(self, m, msg_q, dryrun = True):
        """
        图片消息被 @ 时触发点评。

        流程：
            - 检查是否已有缓存图片
            - 稳健下载图片（支持多阶段预加载）
            - 检查文件大小是否超过阈值
            - 分别调用星火点评与千问点评
            - 自动发送点评文本至微信群
            - 删除临时图片
            - 清理 sender 缓存队列

        :param m: @ 机器人的消息
        :type m: Message
        :param msg_q: 图片缓存字典
        :type msg_q: dict
        :dryrun: 如果为False，就真的调用AI，否则就是假的试试
        :return: None
        :rtype: None
        """        
        sender = m.sender          # 微信昵称，msg_q 缓存 key
        sender_remark = get_sender_name(m)  # 群昵称，用于 @人
        # TEST: testgroup = "测试群"
        testgroup = group_name

        if sender not in msg_q:
            safe_send_msg_queue(self.wx, f"@{sender_remark} 你还没发图片哦。", testgroup, logger=self.logger)
            return

        pic_msg = msg_q[sender]

        # 上一轮发送评论到testgroup，在下一轮下载前要切回监控group_name并等微信渲染稳定
        # 有的时候并没有切换到别的微信聊天群，但是为了稳重起见，还是先执行这个，否则后续download image可能会失败
        __class__._stablely_switch_to_group(self.wx, self.group_name)

        # 下载图片
        try:
            image_path = self.safe_download(pic_msg)
            if not image_path:
                self.logger.error(f"下载图片失败: None")
                return
            # resp = pic_msg.download(self.img_dir)
            # image_path = resp.path if hasattr(resp, "path") else resp
        except Exception as e:
            self.logger.error(f"下载图片失败：{e}")
            self.logger.debug(traceback.format_exc())
            return
    
        # 检查图片大小
        try:
            size = os.path.getsize(image_path)
        except Exception:
            size = 0

        if size > self.max_image_size:
            self.logger.warning(f"图片过大（{size} 字节），已拒绝处理：{image_path}")
            safe_send_msg_queue(self.wx, f"@{sender_remark} 这张图片有点大，老师这边看不动呀，麻烦压缩或截图后再发一张哈～", testgroup, logger=self.logger)
            try:
                os.remove(image_path)
            except Exception:
                pass
            del msg_q[sender]
            return

        # 调用AI点评
        try:
            if not dryrun:
                feedback1 = self.reviewer1.review(image_path)
            else:
                feedback1 = "测试一下而已"
            if not feedback1.strip():
                feedback = "【系统提示】：星火老师暂时没能给出点评，请稍后再试一次。"
        except Exception as e:
            self.logger.error(f"星火点评失败：{e}")
            self.logger.debug(traceback.format_exc())
            feedback1 = "【系统提示】：星火点评服务暂时开小差了，请稍后再试一次。"

        try:
            if not dryrun:
                feedback2 = self.reviewer2.review(image_path)
            else:
                feedback2 = "测试两下而已"
            if not feedback2.strip():
                feedback2 = "【系统提示】：千问老师暂时没能给出点评，请稍后再试一次。"
        except Exception as e:
            self.logger.error(f"星火点评失败：{e}")
            self.logger.debug(traceback.format_exc())
            feedback2 = "【系统提示】：千问点评服务暂时开小差了，请稍后再试一次。"

        # 发送反馈
        try:
            # 是否加 @ 看你需求，这里直接发文本
            feedback1 = f"@{sender_remark} 【星火】" + feedback1
            safe_send_msg_queue(self.wx, feedback1, testgroup, logger=self.logger)
        except Exception as e:
            self.logger.error(f"发送反馈消息失败：{e}")
            self.logger.debug(traceback.format_exc())

        try:
            # 是否加 @ 看你需求，这里直接发文本
            feedback2 = f"@{sender_remark} 【千问】" + feedback2
            safe_send_msg_queue(self.wx, feedback2, testgroup, logger=self.logger)
        except Exception as e:
            self.logger.error(f"发送反馈消息失败：{e}")
            self.logger.debug(traceback.format_exc())

        # 删除图片 & 清理缓存
        try:
            os.remove(image_path)
        except Exception:
            pass

        if sender in msg_q:
            del msg_q[sender]

def ask_time(timeout=60):
    """
    非阻塞输入 + 实时倒计时显示 + 超时自动设置为当前时间+3分钟
    """

    print("请输入 hour (0-24) 和 minute (0-59)用于启动每日统计打卡开始时间，格式如：8 30", flush=True)
    print("若超时未输入，将自动设置统计打卡开始时间为“当前时间 + 3 分钟”。", flush=True)
    print("--------------------------------------------------------", flush=True)

    buffer = ""
    start_time = time.time()

    while True:
        elapsed = time.time() - start_time
        remain = int(timeout - elapsed)

        # ---------- 超时 ----------
        if remain <= 0:
            dt = datetime.now() + timedelta(minutes=3)
            print("\n⏰ 超时未输入，统计打卡开始时间自动设置为 3 分钟后。")
            return dt.hour, dt.minute

        # ---------- 动态倒计时刷新 ----------
        sys.stdout.write(f"\r⏳ 剩余时间：{remain:2d} 秒 | 当前输入：{buffer}")
        sys.stdout.flush()

        # ---------- 处理用户按键 ----------
        if msvcrt.kbhit():
            ch = msvcrt.getwch()

            # 回车：结束输入
            if ch == "\r":
                print("")  # 换行
                break

            # 退格键
            elif ch == '\b':
                if buffer:
                    buffer = buffer[:-1]
                continue

            # 正常可见字符
            else:
                buffer += ch
                continue

        time.sleep(0.05)

    # ===============================
    # 输入完成后解析
    # ===============================
    text = buffer.strip()

    if not text:
        dt = datetime.now() + timedelta(minutes=3)
        print("\n⏰ 无输入，统计打卡开始时间自动设置为 3 分钟后")
        return dt.hour, dt.minute

    try:
        hour_str, minute_str = text.split()
        hour = int(hour_str)
        minute = int(minute_str)

        if not (0 <= hour <= 24):
            raise ValueError("hour 超出范围")
        if not (0 <= minute <= 59):
            raise ValueError("minute 超出范围")

        print(f"\n ✔ 已设置统计打卡开始时间为 {hour:02d}:{minute:02d}")
        return hour, minute

    except Exception:
        print("时间格式输入错误，程序结束，请重新运行再输入。")
        exit(1)

def main():
    """
    稳健版微信点评机器人入口函数。

    功能：
        - 初始化 logger
        - 加载环境变量（微信群名称、图片目录等）
        - 初始化 WeChat、点评模型、机器人实例
        - 启动机器人主循环
        - 捕获致命错误并写入日志

    :return: None
    :rtype: None
    """    
    check_version_or_exit(label="daemon")
    _build_parser().parse_known_args()
    logger = setup_logger()

    hour, minute = ask_time()

    # sanity check
    if not group_name or not review_dir:
        logger.error(f"❌ 点评微信群和点评工作目录没有在环境文件里设置！")
        return

    try:
        os.makedirs(review_dir, exist_ok=True)
    except:
        logger.error(f"❌ 创建目录{review_dir}失败")
        return

    logger.info("\n")
    logger.info("====== 机器人启动 ======")

    try:
        wx = WeChat(ads=False, resize=False)
    except Exception as e:
        logger.error(f"微信初始化失败：{e}")
        logger.debug(traceback.format_exc())
        return
    hide_tkinter_root()

    try:
        reviewer1 = SparkImageReviewer(logger=logger)
    except Exception as e:
        logger.error(f"讯飞星火点评模型初始化失败：{e}")
        logger.debug(traceback.format_exc())
        return
    
    try:
        reviewer2 = QwenVLReviewer(logger=logger)
    except Exception as e:
        logger.error(f"通义千问点评模型初始化失败：{e}")
        logger.debug(traceback.format_exc())
        return

    bot = AutoReviewBot(
        wx=wx,
        reviewer1=reviewer1,
        reviewer2=reviewer2,
        group_name=group_name,
        img_dir=review_dir,
        logger=logger,        
        summarize_target_hour = hour,
        summarize_target_minute = minute,
        poll_interval=POLL_INTERVAL,
        heartbeat_interval=HEARTBEAT_INTERVAL,
        max_image_size=MAX_IMAGE_SIZE,
    )

    try:
        bot.run()
    except Exception as e:
        logger.error(f"机器人运行过程中发生致命错误：{e}")
        logger.debug(traceback.format_exc())

    logger.info("====== 机器人结束 ======")


if __name__ == "__main__":
    main()
