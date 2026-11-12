"""
一键导出+更新模块（export_v2）。

将 export.py（微信消息抓取 → CSV 生成）和 update.py（CSV → Excel 更新）
合并为一步完成。生成 Excel 后输出按学号排列的打卡统计摘要，并可选择发送到微信群。

用法：
    python export_v2.py [--force]

流程概览：
    阶段0 — 同步：版本检查 → 拉取公开仓库 → 检查当日是否已完成 →
             复制昨日数据到本地 → 月初清零
    阶段1 — 导出：滚动微信窗口收集消息 → 创建 Group → 生成 CSV
    阶段2 — 更新：读取昨日 Excel → 合并当日 CSV → 生成新 Excel
    阶段3 — 统计：读取累计次数与当日状态 → 按学号逐行格式化输出
    阶段4 — 发送：询问是否将结果发回微信群
    阶段5 — 推送：结果推送到公开仓库 → mapping 变化推送到私人仓库

输出格式示例：
    ✅ 共有 35 位同学打卡
    ⚠️ 还有 15 位同学可能没打卡

    29101（10，✓）
    29102（8，X）
    ...

复用模块：
    - export.py：Group、EnglishGroup、MathsGroup、GroupFactory 等
    - update.py：Updater、EnglishUpdater、MathsUpdater、UpdaterFactory 等
    - checkinstats.py、config.py、safewechat.py、message.py 等
"""
import argparse
import os
import sys
import time
import urllib.parse


def _build_parser():
    """构建命令行参数解析器（仅依赖 argparse，供 --help 提前退出复用）。"""
    parser = argparse.ArgumentParser(
        description="一键导出微信打卡数据并更新 Excel，同步到公开仓库。",
        epilog="示例：python export_v2.py --force\n"
               "      python export_v2.py --yes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--force", action="store_true",
                        help="强制重新打卡，即使当日数据已存在")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="无人值守：所有交互问答自动回答 Yes，日期默认用昨天")
    return parser


# 提前处理 --help / -h：只打印帮助即退出，避免加载 pandas/xlwings 等重模块
if "--help" in sys.argv or "-h" in sys.argv:
    _build_parser().print_help()
    sys.exit(0)


import pandas as pd
from datetime import datetime, timedelta

from wechat_compat import WeChat

# ---- 复用 export.py 的类和函数 ----
from export import (
    Group, EnglishGroup, MathsGroup, GroupFactory,
    create_number_set, remove_found_numbers,
    get_student_id_from_string, State, StateMachine,
)

# ---- 复用 update.py 的类和函数 ----
from update import (
    Updater, EnglishUpdater, MathsUpdater, UpdaterFactory,
    reset_monthly_excel,
)

from wxname2studentid import Wxname2StudentID
from logger import get_logger, debug_dump_raw_messages, debug_dump_text_msgs
from safewechat import (
    safe_get_wechat_window, safe_bring_wechat_to_front,
    safe_scroll, safe_scroll_and_collect, safe_open_chat,
    safe_get_all_messages, safe_send_msg_immediately,
    move_mouse_to_chat_center, hide_tkinter_root, SCROLL_PIXEL,
    set_wechat_window_size,
)
from message import (
    is_text_message, is_pic_message, is_time_message,
    is_file_message, is_memo_message, is_video_message,
    is_audio_message, compare_message_date,
)
from config import get_env, get_env_set, ask_yes_no, find_excel_file, set_auto_yes
from version import check_version_or_exit
from git_sync import (
    git_pull_public, check_date_exists_in_public,
    copy_yesterday_from_public, git_push_public, git_push_mapping,
    git_sync_mapping_before_checkin,
    _pink,
)

group_names = get_env_set("CHECKIN_GROUP_NAMES")
checkin_dir = get_env("CHECKIN_DIR")


def _is_english_group(groupname):
    """判断是否为英语群（三类打卡）。"""
    return groupname == "英语群"


def _read_today_checkins(csv_path, groupname):
    """
    从当日 CSV 读取当天已打卡的学生学号集合。

    对于英语群：任意一类打卡次数 > 0 即视为已打卡。
    对于数学/语文群：第二列值为 1 即视为已打卡。

    :param csv_path: CSV 文件路径
    :type csv_path: str
    :param groupname: 群名称
    :type groupname: str
    :return: 已打卡学号集合
    :rtype: set[int]
    """
    checked = set()
    try:
        df = pd.read_csv(csv_path, encoding="utf-8-sig")
    except Exception:
        try:
            df = pd.read_csv(csv_path, encoding="utf-8")
        except Exception:
            return checked

    if df.empty:
        return checked

    # 标准化列名（去 BOM、去空格）
    df.columns = [str(c).replace("﻿", "").strip() for c in df.columns]

    for _, row in df.iterrows():
        sid_str = str(row.iloc[0]).strip()
        if not sid_str.isdigit():
            continue
        sid = int(sid_str)

        if _is_english_group(groupname):
            # 英语群：第 2~4 列任一类 > 0 即算打卡
            for col_idx in range(1, min(4, len(df.columns))):
                val = row.iloc[col_idx]
                if pd.notna(val) and int(val) > 0:
                    checked.add(sid)
                    break
        else:
            # 数学 / 语文群：第二列 = 1 即算打卡
            if len(df.columns) >= 2:
                val = row.iloc[1]
                if pd.notna(val) and int(val) > 0:
                    checked.add(sid)

    return checked


def _read_cumulative_stats(excel_path, groupname, logger=None):
    """
    从 Excel 读取每个学生的当月累计打卡次数（含当天）。

    统一读取 B 列（累计打卡次数），适用于所有群类型。

    :param excel_path: xlsx 文件路径
    :type excel_path: str
    :param groupname: 群名称（保留参数以兼容调用方）
    :type groupname: str
    :param logger: 日志器，用于输出读取失败警告
    :type logger: logging.Logger or cprint or None
    :return: {student_id: cumulative_count}
    :rtype: dict[int, int]
    """
    import time
    log = get_logger(logger)
    stats = {}

    for attempt in range(1, 4):  # 最多重试 3 次
        try:
            import openpyxl
            wb = openpyxl.load_workbook(excel_path, data_only=True)
            sheet = wb.active

            for row in sheet.iter_rows(min_row=1, values_only=True):
                if not row or not row[0]:
                    continue
                sid_str = str(row[0]).strip()
                if not sid_str.isdigit():
                    continue
                sid = int(sid_str)

                # 统一读 B 列（第 2 列）：累计打卡次数
                if len(row) >= 2 and row[1] is not None:
                    try:
                        stats[sid] = int(float(str(row[1])))
                    except (ValueError, TypeError):
                        stats[sid] = 0
                else:
                    stats[sid] = 0

            wb.close()
            return stats  # 成功，直接返回
        except Exception as e:
            if attempt < 3:
                log.warning(
                    f"⚠️ {groupname}：读取累计文件失败（第 {attempt}/3 次），"
                    f"2 秒后重试... [{type(e).__name__}: {e}]"
                )
                time.sleep(2)
            else:
                log.warning(
                    f"⚠️ {groupname}：读取累计文件失败（已重试 3 次）→ {excel_path}\n"
                    f"   错误详情：{type(e).__name__}: {e}\n"
                    f"   当月累计次数将全部归零，请检查文件是否被占用或损坏！"
                )

    return stats


def _format_checkin_summary(cumulative_stats, checked_today, all_nums, target=21):
    """
    格式化打卡统计摘要为文本。

    输出格式：
        第一行：✅ 共有 X 位同学打卡
        第二行：⚠️ 还有 Y 位同学可能没打卡
        空行
        标题：学号（当月已打卡次数，当天✓已打卡 / X未打卡）
        逐行：学号（当月累计次数，✓ 或 X）
        空行
        达标统计：打卡次数 >= target（X人）

    其中 ✓ = 当天已打卡，X = 当天未打卡。

    :param cumulative_stats: {学号: 当月累计打卡次数}
    :type cumulative_stats: dict[int, int]
    :param checked_today: 当天已打卡的学号集合
    :type checked_today: set[int]
    :param all_nums: 全部合法学号集合
    :type all_nums: set[int]
    :param target: 达标次数，默认 21
    :type target: int
    :return: 格式化后的多行文本
    :rtype: str
    """
    sorted_ids = sorted(all_nums, key=int)

    checked_count = len(checked_today & all_nums)
    not_checked = all_nums - checked_today
    not_checked_count = len(not_checked)

    lines = [
        f"✅ 共有 {checked_count} 位同学打卡",
        f"⚠️ 还有 {not_checked_count} 位同学可能没打卡",
        "",
        "学号（当月已打卡次数，当天✓已打卡 / X未打卡）",
        "",
    ]

    for sid in sorted_ids:
        monthly = cumulative_stats.get(sid, 0)
        mark = "✓" if sid in checked_today else "X"
        lines.append(f"{sid}（{monthly}，{mark}）")

    # 达标统计
    reached = sorted(
        [sid for sid, cnt in cumulative_stats.items() if cnt >= target],
        key=int,
    )
    lines.append("")
    lines.append(f"✅ 迄今为止（目标 = {target}）：")
    lines.append(f"打卡次数 >= {target}（{len(reached)}人）：")
    if reached:
        chunks = [reached[i:i + 2] for i in range(0, len(reached), 2)]
        for chunk in chunks:
            lines.append("  " + "，".join(str(x) for x in chunk))

    return "\n".join(lines)


def main():
    """
    一键导出 + 更新入口。

    流程：
        0. 版本检查 → 拉取公开仓库 → 检查当日是否已完成 → 复制昨日数据
        1. 输入目标日期
        2. 初始化微信 SDK 并加载学号映射表
        3. 逐群处理：
            a. 阶段1 — 导出：滚动收集消息 → Group → save_text_csv（生成 CSV）
            b. 阶段2 — 更新：Updater → recalc → update_excel_with_target_data（生成 xlsx）
            c. 阶段3 — 统计：读取文件 → 格式化按学号输出
            d. 阶段4 — 发送：询问是否发送到微信群
        4. 保存映射表
        5. 推送结果到公开仓库 → mapping 变化推送到私人仓库
    """
    logger = get_logger()

    # ── 阶段0：版本检查（大版本不匹配则阻止运行）───────────
    check_version_or_exit(label="export_v2")

    # ── 解析参数 ─────────────────────────────────────────
    parser = _build_parser()
    args, _ = parser.parse_known_args()

    # 无人值守模式：开启自动 Yes
    if args.yes:
        set_auto_yes(True)

    # ── 环境检查 ──────────────────────────────────────────
    if not group_names or not checkin_dir:
        logger.error("❌ 打卡微信群和打卡工作目录没有在环境文件里设置！")
        return

    try:
        os.makedirs(checkin_dir, exist_ok=True)
    except Exception:
        logger.error(f"❌ 创建目录{checkin_dir}失败")
        return

    # ── 日期输入 ──────────────────────────────────────────
    yesterday_date = datetime.today() - timedelta(days=1)
    if args.yes:
        # 无人值守：不询问日期，直接取昨天
        target_date = yesterday_date
    else:
        date_str = input(
            f"请输入要生成的日期（格式 yyyy-mm-dd 或 yyyy/mm/dd，"
            f"例如 {yesterday_date.strftime('%Y-%m-%d')}）："
        ).strip()

        date_formats = ["%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"]
        target_date = None
        for fmt in date_formats:
            try:
                target_date = datetime.strptime(date_str, fmt)
                break
            except Exception:
                continue

        if target_date is None:
            logger.warning('无法识别输入的日期格式，将自动使用"昨天"作为目标日期。')
            target_date = yesterday_date

    # ── 阶段0：拉取公开仓库 ───────────────────────────────
    if not git_pull_public(logger):
        logger.error(
            "❌ 公开 results 仓库不可用（clone/pull 失败），"
            "无法确认是否已统计，终止运行。"
        )
        return

    # ── 阶段0：准备昨日数据 ───────────────────────────────
    yesterday = target_date - timedelta(days=1)
    logger.info(_pink(f"📋 准备 {yesterday.strftime('%Y-%m-%d')} 的数据作为基础..."))
    copy_yesterday_from_public(yesterday, checkin_dir, logger)

    # ── 初始化微信 ────────────────────────────────────────
    logger.info(f"🚀 启动微信群文字导出：{group_names}")
    try:
        wx = WeChat(ads=False, resize=False)
        hide_tkinter_root()
        logger.info("WeChat SDK 初始化成功！")
    except Exception as e:
        logger.error(f"初始化 WeChat SDK 失败：{e}")
        return

    # ── 同步 mapping：fetch 远程并合并成本地总映射（只动 mapping，不动代码）──
    git_sync_mapping_before_checkin(checkin_dir, logger)

    # ── 加载学号映射表 ─────────────────────────────────────
    mapping = {}
    try:
        wx2id = Wxname2StudentID(directory=checkin_dir)
        mapping = wx2id.load_mapping()
        logger.info(f"学生映射表加载成功，共 {len(mapping)} 条。")
    except Exception as e:
        logger.error(f"加载映射表失败，将使用空映射：{e}")

    # ── 微信窗口置顶 ──────────────────────────────────────
    hwnd = safe_get_wechat_window(logger)
    if hwnd:
        safe_bring_wechat_to_front(hwnd, logger)
    move_mouse_to_chat_center()
    set_wechat_window_size(logger)

    # ── 工厂 ──────────────────────────────────────────────
    group_factory = GroupFactory()
    updater_factory = UpdaterFactory()
    all_nums = create_number_set()

    # ── 逐群处理 ──────────────────────────────────────────
    for groupname in group_names:
        # ── 该群当日是否已有人统计（群与群独立）────────────
        if not args.force and check_date_exists_in_public(
            target_date, logger, groupname
        ):
            logger.info(_pink(
                f"⏭️ {target_date.strftime('%Y-%m-%d')} 的{groupname}打卡数据"
                f"已在公开仓库中存在，跳过（使用 --force 可强制重新打卡）"
            ))
            continue

        if not ask_yes_no(f"要处理{groupname}吗？", default=True):
            logger.warning(f"跳过群：{groupname}")
            continue

        if not safe_open_chat(wx, groupname, logger):
            continue

        if hwnd:
            safe_bring_wechat_to_front(hwnd, logger)

        # ========== 阶段1：导出消息 → CSV ===================
        # 三遍滚动收集（补偿不同滚动量，防止漏消息）
        msgs1 = safe_scroll_and_collect(
            wx, scroll_pixel=SCROLL_PIXEL,
            target_date=target_date, logger=logger,
        )
        msgs2 = safe_scroll_and_collect(
            wx, scroll_pixel=SCROLL_PIXEL - 50,
            target_date=target_date, logger=logger,
        )
        msgs3 = safe_scroll_and_collect(
            wx, scroll_pixel=SCROLL_PIXEL + 50,
            target_date=target_date, logger=logger,
        )

        if not msgs1 and not msgs2 and not msgs3:
            logger.warning(f"⚠️ {groupname} 未获取到任何消息")
            continue

        # 本群调试日志文件：屏幕的三轮 Debug 结果同步写入该文件（每次运行重新生成）
        debug_log_path = os.path.join(checkin_dir, f"{groupname}_{target_date.strftime('%Y-%m-%d')}.log")
        with open(debug_log_path, "w", encoding="utf-8"):
            pass

        # DEBUG: 打印三遍的原始消息（未过滤）
        for pass_label, msgs in [("第一遍", msgs1), ("第二遍", msgs2), ("第三遍", msgs3)]:
            debug_dump_raw_messages(groupname, pass_label, msgs, logger, log_file=debug_log_path)

        # 三遍分别解析
        group = None
        try:
            group = group_factory.create_group(groupname, msgs1, target_date, logger)
            text_msgs1_raw = group.export_group(mapping)
        except Exception as e:
            logger.error(f"处理群 {groupname} 第一遍出错：{e}")
            text_msgs1_raw = []

        try:
            group2 = group_factory.create_group(groupname, msgs2, target_date, logger)
            text_msgs2_raw = group2.export_group(mapping)
        except Exception as e:
            logger.error(f"处理群 {groupname} 第二遍出错：{e}")
            text_msgs2_raw = []

        try:
            group3 = group_factory.create_group(groupname, msgs3, target_date, logger)
            text_msgs3_raw = group3.export_group(mapping)
        except Exception as e:
            logger.error(f"处理群 {groupname} 第三遍出错：{e}")
            text_msgs3_raw = []

        # 保证 group 可用（优先第一遍，被异常覆盖时用后两遍兜底）
        if group is None:
            group = group2
        if group is None:
            group = group3

        # 去重合并三遍结果
        seen1 = set()
        text_msgs1 = [t for t in text_msgs1_raw if t not in seen1 and not seen1.add(t)]
        seen2 = set()
        text_msgs2 = [t for t in text_msgs2_raw if t not in seen2 and not seen2.add(t)]
        seen3 = set()
        text_msgs3 = [t for t in text_msgs3_raw if t not in seen3 and not seen3.add(t)]

        seen = set()
        text_msgs = []
        for t in text_msgs1 + text_msgs2 + text_msgs3:
            if t not in seen:
                seen.add(t)
                text_msgs.append(t)
        logger.info(
            f"{groupname} 三遍合并：{len(text_msgs1)}+{len(text_msgs2)}+{len(text_msgs3)}"
            f"→{len(text_msgs)} 条"
        )

        # DEBUG: 打印 export_group 提取到的 text_msgs
        debug_dump_text_msgs(groupname, text_msgs, logger, log_file=debug_log_path)

        try:
            if not text_msgs:
                logger.warning(f"⚠️ 未检测到{groupname}新的文字消息。")
                continue

            # ── 生成 CSV ──────────────────────────────────────────
            _, csv_path = group.save_text_csv(text_msgs)

            # ── 当天打卡学号 ─────────────────────────────────────
            checked_today = _read_today_checkins(csv_path, groupname)

            old_excel_path = find_excel_file(checkin_dir, groupname, yesterday)

            # ── 先 recalc 旧文件，再读累计 ────────────────────────
            #     旧 Excel 的 B 列（累计次数）是公式，openpyxl
            #     data_only=True 只能读缓存值。必须先 xlwings
            #     recalc 刷新缓存，否则缓存值可能是 0。
            updater = None
            if os.path.exists(old_excel_path):
                try:
                    updater = updater_factory.create_updater(
                        groupname, target_date, logger
                    )
                    updater.recalc_and_save_excel()
                    if target_date.day == 1:
                        # 月初第一天：上月累计清零，当月从今天开始（0→1），
                        # 与下方 reset_monthly_excel 的 Excel 清零保持一致
                        cumulative_stats = {
                            sid: 1 for sid in checked_today
                        }
                    else:
                        cumulative_stats = _read_cumulative_stats(
                            old_excel_path, groupname, logger
                        )
                        for sid in checked_today:
                            cumulative_stats[sid] = (
                                cumulative_stats.get(sid, 0) + 1
                            )
                except Exception as e:
                    logger.error(
                        f"创建 updater 或 recalc 旧文件失败：{e}"
                    )
                    cumulative_stats = {
                        sid: 1 for sid in checked_today
                    }
            else:
                # 无历史 Excel（如首次使用）或月初第一天，仅统计当天
                cumulative_stats = {sid: 1 for sid in checked_today}

            # ── 格式化 & 打印（按打卡率着色）────────────────────
            formatted = _format_checkin_summary(
                cumulative_stats, checked_today, all_nums
            )
            checked_count = len(checked_today & all_nums)
            total_count = len(all_nums)
            ratio = checked_count / total_count if total_count > 0 else 0
            if ratio < 0.2:
                color = "\033[91m"   # 亮红色：不足 20%
            elif ratio < 0.4:
                color = "\033[93m"   # 亮黄色：20%~40%
            else:
                color = ""           # 默认白色
            reset = "\033[0m" if color else ""
            filename = f"{groupname}_{target_date.strftime('%Y%m%d')}.xlsx"
            # 文件直链（GitHub Pages 静态托管）
            pages_url = (
                f"https://ITU-AI-ML-in-5G-Challenge.github.io/results"
                f"/{urllib.parse.quote(filename)}"
            )
            # Office Online Viewer 在线渲染（src 传 Pages 直链）
            view_url = (
                f"https://view.officeapps.live.com/op/view.aspx"
                f"?src={urllib.parse.quote(pages_url, safe='%')}"
            )
            link_line = f"\n📊 在线查看：{view_url}"

            print("\n" + color + "=" * 40)
            print(color + formatted + link_line)
            print("=" * 40 + reset + "\n")

            # ── 是否发送到微信群（等 Excel 生成 + git push 后再发）───
            send_msg = None
            if ask_yes_no("⚠️ 要输出结果到微信群里吗", default=True):
                send_msg = (
                    f"【系统消息】{target_date.strftime('%Y-%m-%d')} "
                    f"{groupname}统计结果如下：\n{formatted}"
                    f"\n\n📊 在线查看：{view_url}"
                )

            # ── 生成当日新 Excel ─────────────────────────────────
            #     月初第一天：先清零上月底文件，再基于它生成新文件
            #     （清零前已读完累计统计，群消息发送不受影响）
            if updater:
                try:
                    if target_date.day == 1 and os.path.exists(old_excel_path):
                        reset_monthly_excel(old_excel_path, groupname, logger)
                        # 清零后 B 列公式缓存值仍是旧值，需 recalc 刷新
                        updater.recalc_and_save_excel()

                    new_excel_file = (
                        updater.update_excel_with_target_data()
                    )
                    if new_excel_file:
                        # 新文件公式列缓存值可能未计算，recalc 一次以
                        # 确保公式结果落入文件，分享给他人时不会显示 0
                        updater.recalc_and_save_excel(filepath=new_excel_file)
                        logger.info(
                            f"✅ 已成功生成：{new_excel_file}"
                        )
                    else:
                        logger.error(
                            f"❌ 生成 {groupname} Excel 失败"
                        )
                except Exception as e:
                    logger.error(f"更新 {groupname} Excel 出错：{e}")

            logger.info(f"✅ {groupname}处理完毕！")

            # ── 该群结果立即推送到公开仓库（群与群独立）──────
            git_push_public(
                checkin_dir,
                target_date,
                f"{target_date.strftime('%Y-%m-%d')} {groupname}打卡结果",
                logger,
                groupname=groupname,
            )

            # ── 推送完成后发送微信（确保 GitHub 链接已生效）───
            #     recalc 后 Excel 白窗可能抢走焦点，发送前强制把
            #     微信窗口拉回前台，否则消息会敲到别处。
            if send_msg:
                if hwnd:
                    safe_bring_wechat_to_front(hwnd, logger)
                    time.sleep(0.3)
                if safe_send_msg_immediately(
                    wx, send_msg, groupname, logger=logger
                ):
                    logger.info(f"✅ 发送到微信群{groupname}成功！")
                else:
                    logger.error(f"❌ 发送到微信群{groupname}失败！")

        except Exception as e:
            logger.error(f"处理群 {groupname} 发生错误：{e}")

    # ── 保存映射表 ─────────────────────────────────────────
    wx2id.save_mapping(mapping)
    logger.info("所有群组处理已结束！")

    # ── mapping.xlsx 有变化则推送到私人仓库 ───────────────
    git_push_mapping(
        checkin_dir,
        f"Feat: {target_date.strftime('%Y-%m-%d')} 打卡数据更新",
        logger,
    )


if __name__ == "__main__":
    main()
