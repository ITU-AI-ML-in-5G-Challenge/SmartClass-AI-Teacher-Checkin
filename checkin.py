# -*- coding: utf-8 -*-
"""
打卡总控守护进程。

主要功能：
    - 每天定时运行一次导出任务和 Excel 更新任务。
    - 导出任务包括读取微信群文字、备份、统计、发送结果。
    - 更新任务包括读取 CSV 并写入 Excel 汇总表。
    - 所有步骤均包含异常捕获，单步失败不会导致进程退出。

本模块依赖 wxauto4、export.GroupFactory、update.UpdaterFactory 等实现核心业务。
"""
import argparse
import sys


def _build_parser():
    """构建命令行参数解析器（仅依赖 argparse，供 --help 提前退出复用）。"""
    return argparse.ArgumentParser(
        description="打卡总控守护进程，定时执行导出与更新任务。",
    )


# 提前处理 --help / -h：只打印帮助即退出，避免加载重模块
if "--help" in sys.argv or "-h" in sys.argv:
    _build_parser().print_help()
    sys.exit(0)


from wechat_compat import WeChat
import os
import time
import logging
import traceback
import msvcrt
from datetime import datetime, timedelta

from wxname2studentid import Wxname2StudentID
from config import get_env, get_env_set
from logger import get_logger
from version import check_version_or_exit
from safewechat import safe_get_wechat_window, safe_bring_wechat_to_front, safe_open_chat, safe_scroll, safe_scroll_and_collect, safe_get_all_messages, safe_send_msg_queue, move_mouse_to_chat_center, hide_tkinter_root
from export import GroupFactory
from update import UpdaterFactory

group_names = get_env_set("CHECKIN_GROUP_NAMES")
checkin_dir = get_env("CHECKIN_DIR")

# =========================
# 1. 导出部分（基于 export.py main 改写）
# =========================

def run_export_for_date(target_date: datetime, logger = None):
    """
    对指定日期执行一次“微信群文字导出 + 统计 + 发群消息”。

    逻辑来源于 ``export.main()``，但做了以下改动：
        - 不再从 input 获取日期，直接使用 ``target_date``。
        - 不再询问是否处理某个群，统一认为需要处理。
        - 不再询问是否向微信群发送统计结果，统一默认为发送。

    :param target_date: 要处理的日期，通常为昨天。
    :type target_date: datetime.datetime
    :param logger: 可选，传入外部 logger；若为空将自动创建。
    :type logger: logging.Logger or None
    :return: 无返回值。本函数的所有输出通过日志记录。
    :rtype: None
    """
    logger = get_logger(logger)

    # sanity check
    if not group_names or not checkin_dir:
        logger.error(f"❌ 打卡微信群和打卡工作目录没有在环境文件里设置！")
        return

    try:
        os.makedirs(checkin_dir, exist_ok=True)
    except:
        logger.error(f"❌ 创建目录{checkin_dir}失败")
        return
    
    logger.info("===== 开始导出微信群消息，目标日期：%s =====", target_date.strftime("%Y-%m-%d"))

    # 创建 WeChat 实例
    try:
        wx = WeChat(ads=False, resize=False)
        hide_tkinter_root()
        logger.info("WeChat 初始化成功")
    except Exception as e:
        logger.error("WeChat 初始化失败：%s", e)
        logger.debug(traceback.format_exc())
        return

    # 读取 / 初始化 映射表
    try:
        os.makedirs(checkin_dir, exist_ok=True)      
        wx2id = Wxname2StudentID(directory=checkin_dir)
        mapping = wx2id.load_mapping()
        logger.info("学号映射表加载成功")
    except Exception as e:
        logger.error("加载学号映射表失败：%s", e)
        logger.debug(traceback.format_exc())
        mapping = {}

    # ------------------------------
    # 获取微信窗口
    # ------------------------------
    hwnd = safe_get_wechat_window(logger)
    if hwnd:
        safe_bring_wechat_to_front(hwnd, logger)
    move_mouse_to_chat_center()

    factory = GroupFactory()

    # 遍历所有群（不再询问 y/n）
    for groupname in group_names:
        logger.info("开始处理群：%s", groupname)
        try:
            if not safe_open_chat(wx, groupname, logger):
                continue

            # 再次置顶窗口
            if hwnd:
                safe_bring_wechat_to_front(hwnd, logger)

            # 边滚动边收集消息（解决微信只返回可见消息的问题）
            msgs = safe_scroll_and_collect(wx, target_date=target_date, logger=logger)
            if not msgs:
                continue

            # 构建 Group 对象并导出 text_msgs
            try:
                group = factory.create_group(groupname, msgs, target_date)
                text_msgs = group.export_group(mapping)
                logger.info("群 %s 导出有效消息条数：%d", groupname, len(text_msgs))
            except Exception as e:
                logger.error("群 %s 导出 text_msgs 失败：%s", groupname, e)
                logger.debug(traceback.format_exc())
                continue

            if not text_msgs:
                logger.info("群 %s 没有检测到有效打卡消息，跳过。", groupname)
                continue

            # 保存 txt/csv，并获得统计 summary
            try:
                summary, file_path = group.save_text_csv(text_msgs)
                logger.info("群 %s 统计完成，CSV：%s", groupname, file_path)
                logger.info("统计摘要：\n%s", summary)
            except Exception as e:
                logger.error("群 %s 保存 CSV/统计失败：%s", groupname, e)
                logger.debug(traceback.format_exc())
                continue

            # 默认：把统计结果发回群里
            try:
                summary_to_send = (
                    f"【系统消息】 {target_date.strftime('%Y-%m-%d')} 统计结果如下：\n"
                    f"{summary}"
                )
                ok = safe_send_msg_queue(wx, summary_to_send, groupname, logger=logger)
                # TEST: ok = True
                if ok:
                    logger.info("群 %s 统计结果发送成功。", groupname)
                else:
                    logger.warning("群 %s 统计结果发送失败（SendMsg 返回 False）。", groupname)
            except Exception as e:
                logger.error("群 %s 统计结果发送异常：%s", groupname, e)
                logger.debug(traceback.format_exc())

        except Exception as e:
            logger.error("处理群 %s 时发生未捕获异常：%s", groupname, e)
            logger.debug(traceback.format_exc())
            # 出错也不要影响其他群，继续下一个群
            continue

        logger.info("群 %s 导出流程结束。", groupname)

    # 保存 mapping
    try:
        wx2id.save_mapping(mapping)
        logger.info("学号映射表保存成功。")
    except Exception as e:
        logger.error("保存学号映射表失败：%s", e)
        logger.debug(traceback.format_exc())

    logger.info("===== 当日导出流程全部结束 =====")


# =========================
# 2. 合并 Excel 部分（基于 update.py main 改写）
# =========================

def run_update_for_date(target_date: datetime, logger = None):
    """
    对指定日期执行一次 Excel 合并与更新。

    逻辑来源于 ``update.main()``，做了以下改动：
        - 不再通过 input 询问是否处理某个群，默认全部处理。
        - 不再询问是否覆盖 Excel，统一自动覆盖。
        - 自动处理 recalc、写入当天数据等步骤，失败但不退出主进程。

    :param target_date: 要处理的日期。
    :type target_date: datetime.datetime
    :param logger: 可选自定义 logger；缺省时使用默认 logger。
    :type logger: logging.Logger or None
    :return: 无返回值。
    :rtype: None
    """
    logger = get_logger(logger)

    # sanity check
    if not group_names or not checkin_dir:
        logger.error(f"❌ 打卡微信群和打卡工作目录没有在环境文件里设置！")
        return

    try:
        os.makedirs(checkin_dir, exist_ok=True)
    except:
        logger.error(f"❌ 创建目录{checkin_dir}失败")
        return

    logger.info("===== 开始更新 Excel 打卡统计，目标日期：%s =====", target_date.strftime("%Y-%m-%d"))

    factory = UpdaterFactory()

    for name in group_names:
        logger.info("开始处理群 Excel：%s", name)
        try:
            # 创建 updater（内部已经自动生成 csv/excel 路径）
            updater = factory.create_updater(name, target_date)
        except Exception as e:
            logger.error("创建 updater 失败：群 %s，错误：%s", name, e)
            logger.debug(traceback.format_exc())
            continue

        # 默认：允许覆盖 new_excel_file，不再询问 ask_overwrite()
        try:
            # 先重新计算旧 Excel 的公式
            try:
                updater.recalc_and_save_excel()
            except Exception as e:
                logger.error("群 %s recalc_and_save_excel 异常（继续尝试更新数据）：%s", name, e)
                logger.debug(traceback.format_exc())

            # 再把当天 CSV 写入新的 Excel（实际上路径等同于 main 中的 new_excel_file）
            new_excel_file = updater.update_excel_with_target_data()
            if new_excel_file:
                logger.info("群 %s 已成功生成 Excel：%s", name, new_excel_file)
            else:
                logger.warning("群 %s 未生成新的 Excel 文件（可能 CSV 为空或出错）。", name)

        except PermissionError as e:
            logger.error("群 %s 保存 Excel 失败（可能文件被占用）：%s", name, e)
            logger.debug(traceback.format_exc())
        except Exception as e:
            logger.error("群 %s 更新 Excel 时发生异常：%s", name, e)
            logger.debug(traceback.format_exc())
            continue

        logger.info("群 %s Excel 更新流程结束。", name)

    logger.info("===== 当日 Excel 更新流程全部结束 =====")


# =========================
# 3. 调度：每天 06:00 跑一次
# =========================
def get_next_run_time(target_hour=6, target_minute=0):
    """
    计算下一次任务的绝对运行时间。

    逻辑：
        - 如果当前时间尚未到达今日的 target_time，则返回今日 target_time；
        - 否则返回明日的 target_time。

    :param target_hour: 目标小时
    :param target_minute: 目标分钟
    :return: 下一次任务时间（datetime）
    """

    now = datetime.now()
    today_target = datetime(now.year, now.month, now.day, target_hour, target_minute)

    if now < today_target:
        return today_target
    else:
        return today_target + timedelta(days=1)

def main_loop():
    """
    守护进程主循环，一般不会直接调用。一般是从daemon.py调该文件的部分函数。

    功能：
        - 持续等待下一次执行时间（每天 02:00）。
        - 到点后依次执行导出任务与 Excel 更新任务。
        - 支持 ESC 软退出：
            - 等待阶段：立即停止。
            - 执行阶段：记录退出意图，任务完成后退出。
        - 支持 Ctrl+C 优雅退出。
        - 任意步骤异常仅记录日志，不中断主循环。

    :return: 无返回值；函数不会主动退出，除非收到退出指令。
    :rtype: None
    """
    check_version_or_exit(label="checkin")
    _build_parser().parse_known_args()
    logger = get_logger()
    logger.info("守护进程已启动。按 ESC 或 Ctrl+C 可优雅退出；关闭窗口 = 强制退出")

    exit_after_task = False
    heartbeat = 0
    heartbeat_interval = 60

    while True:
        try:
            target_hour = 8
            target_minute = 30
            next_run = get_next_run_time(target_hour, target_minute)
            logger.info("下一次任务时间：%s", next_run.strftime("%Y-%m-%d %H:%M:%S"))

            # 等待阶段
            while True:
                if msvcrt.kbhit() and msvcrt.getch() == b'\x1b':
                    logger.info("检测到 ESC（等待阶段），守护进程优雅退出。")
                    return

                heartbeat += 1
                if heartbeat % heartbeat_interval == 0:
                    logger.info("⏳ 打卡统计守护进程运行中（心跳正常）...")

                if datetime.now() >= next_run:
                    break

                time.sleep(1)

            # 执行任务阶段（ESC 只会记录退出意图）
            exit_after_task = False

            target_date = next_run - timedelta(days=1)
            logger.info("开始任务 target_date = %s", target_date.strftime("%Y-%m-%d"))

            try:
                run_export_for_date(target_date, logger)
            except Exception as e:
                logger.error("run_export_for_date() 异常：%s", e)
                logger.debug(traceback.format_exc())

            if msvcrt.kbhit() and msvcrt.getch() == b'\x1b':
                logger.info("检测到 ESC（执行阶段），任务结束后退出。")
                exit_after_task = True

            try:
                run_update_for_date(target_date, logger)
            except Exception as e:
                logger.error("run_update_for_date() 异常：%s", e)
                logger.debug(traceback.format_exc())

            if exit_after_task:
                logger.info("任务完成（执行阶段按 ESC），守护进程优雅退出。")
                return

            logger.info("任务结束，等待下一轮。")

        # Ctrl+C = 优雅退出
        except KeyboardInterrupt:
            logger.info("检测到 Ctrl+C，守护进程优雅退出。")
            return

        except Exception as e:
            logger.error("主循环异常：%s", e)
            logger.debug(traceback.format_exc())
            time.sleep(3)

if __name__ == "__main__":
    main_loop()
