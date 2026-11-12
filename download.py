# -*- coding: utf-8 -*-
"""
微信群图片与文件批量导出工具（支持备注路径智能解析）。

主要功能：
    - 自动从微信群批量下载图片与文件
    - 支持备注（文字消息）作为目录路径
    - 支持 AI（Spark）根据备注智能选择保存目录
    - 自动重命名文件（前缀 + 日期 + 随机数），避免冲突
    - 支持目录自动创建、安全路径清洗、路径超长处理
    - 支持连续图片消息（多图模式）与单图片模式
    - 支持下载失败后的微信滚动预加载机制，提高下载成功率
    - 与“家长昵称 → 学号”映射表结合，自动决定文件名前缀
    - 提供 CLI 交互版，可逐个选择微信群进行处理

依赖模块：
    - wxauto4：获取微信消息与下载文件
    - safewechat：可靠的微信窗口控制
    - sparkchat, hunyuanchat：用于 AI 路径推断
    - wxname2studentid：昵称到学号映射表
    - message：消息过滤与类型判断工具

本模块既可独立运行，也可作为库被调用。
"""
import argparse
import sys


def _build_parser():
    """构建命令行参数解析器（仅依赖 argparse，供 --help 提前退出复用）。"""
    return argparse.ArgumentParser(
        description="微信群图片与文件批量导出工具。",
    )


# 提前处理 --help / -h：只打印帮助即退出，避免加载重模块
if "--help" in sys.argv or "-h" in sys.argv:
    _build_parser().print_help()
    sys.exit(0)


from wechat_compat import WeChat
import pyautogui
import time, os
from datetime import datetime, timedelta
from version import check_version_or_exit
from enum import Enum
import random
import shutil
from pathlib import Path
from wxname2studentid import Wxname2StudentID
from logger import get_logger
from safewechat import safe_get_wechat_window, safe_bring_wechat_to_front, safe_scroll, safe_open_chat, safe_get_all_messages, move_mouse_to_chat_center
from message import is_pic_message, is_file_message, is_time_message, is_text_message, is_video_message, compare_message_date, get_sender_name
from sparkchat import SparkChatClient
from hunyuanchat import HunyuanChatClient
import re
from video_download import ui_open_video_preview, ui_click_original_area_and_save_on_window

from config import get_env, get_env_map, ask_yes_no

group_names = get_env_map("DOWNLOAD_GROUP_NAMES")
download_dir = get_env("DOWNLOAD_DIR")
checkin_dir = get_env("CHECKIN_DIR")

class GroupDownload:
    """
    负责处理单个微信群的图片/文件下载与整理逻辑。

    主要职责：
        - 清洗备注文本并解析出目录结构
        - 自动补全群名与路径层级
        - 对文件名进行安全化处理（非法字符、长度限制）
        - 下载图片（包含多阶段重试与微信预加载机制）
        - 支持文件下载并保存
        - 支持连续图片 + 备注的整体处理（一批消息 → 一批文件）
        - 清理处理过的消息并返回下一条消息位置
        - 同一人连续多图 → 保存到同一目录

    :param name: 群名称
    :type name: str
    :param msgs: 来自微信的消息列表
    :type msgs: list
    :param target_date: 预计的处理日期，用于文件重命名
    :type target_date: datetime.datetime
    :param logger: 日志对象
    :type logger: logging.Logger
    """    
    def __init__(self, name: str, msgs: list = None, target_date: datetime = None, logger = None):
        """
        初始化 GroupDownload 对象。

        :param name: 群名称
        :type name: str
        :param msgs: 消息列表；若为空使用空列表
        :type msgs: list or None
        :param target_date: 目标日期；默认为今天
        :type target_date: datetime.datetime or None
        :param logger: 自定义 logger，可为 None
        :type logger: logging.Logger or None
        """        
        self.name = name
        self.msgs = msgs or []
        self.target_date = target_date or datetime.today()
        self.logger = get_logger(logger)

    # ------------------------------
    # 创建目录
    # ------------------------------
    def make_subdir(self, directory: str, name: str) -> str:
        """
        在指定目录下创建子目录（若不存在则自动创建）。

        :param directory: 上级目录路径
        :type directory: str
        :param name: 子目录名称
        :type name: str
        :return: 创建后的完整路径
        :rtype: str
        """        
        full_path = os.path.join(directory, name)
        try:
            os.makedirs(full_path, exist_ok=True)
            self.logger.info(f"目录已准备好：{full_path}")
        except Exception as e:
            self.logger.error(f"创建目录失败：{full_path}，错误：{e}")
        return full_path

    def sanitize_filename(self, name: str) -> str:
        """
        清洗文件名非法字符（兼容 Windows 文件系统）。

        会移除：``\\ / : * ? " < > |``

        :param name: 原始文件名或路径片段
        :type name: str
        :return: 安全文件名
        :rtype: str
        """
        cleaned = name
        for ch in ['\\', '/', ':', '*', '?', '"', '<', '>', '|']: # Windows 不允许的字符
            cleaned = cleaned.replace(ch, "_")
        return cleaned

    # ------------------------------
    # 稳健生成新的文件路径
    # ------------------------------
    def parse_path_pattern(self, raw: str, group_name: str, directory: str) -> str | None:
        """
        将用户备注解析为目录路径（不含扩展名）。

        解析示例：
            - ``作业1`` → ``<directory>/<group_name>/作业1``
            - ``数学/作业1`` → 保持结构
            - ``数学-作业1`` → 自动转换为 ``数学/作业1``
            - 开头 ``/`` 自动去掉
            - 若第一层不是群名 → 自动补齐群名

        :param raw: 用户备注的原始内容
        :type raw: str
        :param group_name: 群名称（作为路径的必备层级）
        :type group_name: str
        :param directory: 保存根目录
        :type directory: str
        :return: 拼接好的完整目录路径前缀（不含扩展名）
        :rtype: str or None
        """
        try:
            if raw is None:
                self.logger.error("parse_path_pattern: raw 参数为 None")
                return None

            raw = raw.strip()
            if not raw:
                self.logger.error("parse_path_pattern: raw 是空字符串")
                return None

            # 标准化分隔符
            raw_norm = raw.replace("\\", "/")
            # FIXBUG: 下面这些如果不去掉，会让"16-17日"变成"16/17日"，生成更多不必要的子目录
            # if "-" in raw_norm:
            #     raw_norm = raw_norm.replace("-", "/")

            # 去掉开头的 '/'
            if raw_norm.startswith("/"):
                raw_norm = raw_norm[1:]

            # 切分并清理非法字符
            parts = [self.sanitize_filename(p.strip()) for p in raw_norm.split("/") if p.strip()]

            if not parts:
                self.logger.error(f"parse_path_pattern: 解析失败，raw='{raw}' → parts为空")
                return None

            # 第一个不是群名 → 自动补上
            if parts[0] != group_name:
                self.logger.info(f"parse_path_pattern: 自动补上群名 '{group_name}'")
                parts.insert(0, group_name)

            # 拼成完整路径（无扩展名）
            full_path = Path(directory)
            for p in parts:
                full_path = full_path / p

            self.logger.info(f"解析路径成功: {full_path}")
            return str(full_path)

        except Exception as e:
            self.logger.error(f"parse_path_pattern 异常: {e}")
            self.logger.error(f"raw='{raw}', group='{group_name}', directory='{directory}'")
            return None

    def clean_prefix(self, prefix: str, max_len: int = 50) -> str:
        """
        清洗文件名前缀，使之适合 Windows 文件名。

        包含：
            - 非法字符替换
            - 空白压缩
            - 最大长度限制

        :param prefix: 原始前缀
        :type prefix: str
        :param max_len: 最大长度，默认 50
        :type max_len: int
        :return: 清洗后的前缀
        :rtype: str
        """
        if not prefix:
            return "unknown"

        # 替换 Windows 文件名非法字符
        prefix = re.sub(r'[<>:"/\\|?*]', "_", prefix)

        # 去掉换行、制表符、多余空白
        prefix = re.sub(r'\s+', ' ', prefix).strip()

        # 限制最大长度
        if len(prefix) > max_len:
            prefix = prefix[:max_len].rstrip()

        return prefix

    # ------------------------------
    # 稳健文件重命名
    # ------------------------------
    def rename_file(self, full_path: str, prefix: str, date: datetime) -> str:
        """
        为已保存的文件重新命名。

        规则：
            - ``prefix_YYYYMMDD_random.ext`` 格式
            - 若目标文件已存在 → 先删后写，实现 overwrite
            - 自动处理 Windows 路径过长错误（OSError 206）

        :param full_path: 原完整路径
        :type full_path: str
        :param prefix: 文件名前缀（通常为学生昵称或学号）
        :type prefix: str
        :param date: 用于命名的日期
        :type date: datetime.datetime
        :return: 新文件路径
        :rtype: str
        """        
        try:
            old_path = Path(full_path)
            if not old_path.exists():
                self.logger.warning(f"文件不存在，无法重命名：{full_path}")
                return full_path

            safe_prefix = self.clean_prefix(prefix)

            date_str = date.strftime("%Y%m%d")
            rand_num = random.randint(1000, 9999)
            new_filename = f"{date_str}_{safe_prefix}_{rand_num}{old_path.suffix}"
            new_path = old_path.with_name(new_filename)

            # ⭐ 如果目标存在 → 删除实现 overwrite
            if new_path.exists():
                try:
                    new_path.unlink()
                except Exception as e:
                    self.logger.error(f"覆盖旧文件失败：{new_path}，错误：{e}")
                    return full_path

            old_path.rename(new_path)
            self.logger.info(f"已重命名文件：{new_path}")
            return str(new_path)
        except OSError as e:
            # 特殊处理：Windows 路径超长
            if "File name too long" in str(e) or "206" in str(e):
                self.logger.error(f"❌ 路径太长，重命名失败：{full_path} → {new_filename}")
            else:
                self.logger.error(f"重命名文件失败：{full_path} → {new_filename}，错误：{e}")

            return full_path
        except Exception as e:
            self.logger.error(f"重命名文件失败：{full_path} → {new_filename}，错误：{e}")
            return full_path

    def remove_if_empty(self, dir_path: str | Path):
        p = Path(dir_path)
        if p.is_dir() and not any(p.iterdir()):
            p.rmdir()
            self.logger.info(f"已删除空目录: {p}")
        else:
            self.logger.info(f"目录 {p} 不为空")

    # ------------------------------
    # 稳健视频下载
    # ------------------------------
    def safe_download_video(
        self,
        msg,
        save_dir,
        *,
        timeout: int = 30,
        max_retries: int = 3,
        settle_sec: float = 1.0,
        ui_fallback: bool = True,
        ui_min_size_mb: float = 0.0
    ):

        def _is_pathlike(x):
            return isinstance(x, (str, Path))

        def _ensure_exists(p: Path, wait_sec: float = 5.0) -> bool:
            deadline = time.time() + wait_sec
            while time.time() < deadline:
                if p.exists():
                    return True
                time.sleep(0.2)
            return False

        def _wait_settle(p: Path, max_wait: float) -> bool:
            """
            等待文件写入稳定：连续两次 size 不变且 >0
            """
            deadline = time.time() + max_wait
            last_size = -1
            stable = 0

            while time.time() < deadline:
                if p.exists():
                    sz = p.stat().st_size
                    if sz > 0 and sz == last_size:
                        stable += 1
                        if stable >= 2:
                            return True
                    else:
                        stable = 0
                        last_size = sz
                time.sleep(max(0.1, settle_sec / 2))
            return False

        try:
            save_dir = Path(save_dir)
            save_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.logger.error(f"视频创建目录失败: {save_dir} err={e}")
            return None

        api_best_path = None

        for attempt in range(1, max_retries + 1):
            try:
                self.logger.info(f"视频下载尝试 attempt={attempt}/{max_retries}")
                ret = msg.download(str(save_dir), True, timeout)

                # 失败时往往是 WxResponse；不要用 truthy 判断，按类型判断
                if not _is_pathlike(ret):
                    self.logger.warning(f"视频下载返回非路径（可能失败 WxResponse）: {ret!r}")
                    time.sleep(0.6 * attempt)
                    continue

                p = Path(ret)

                # 有时返回的路径不在 save_dir（微信默认目录），只要存在就认
                if not _ensure_exists(p, wait_sec=5.0):
                    self.logger.warning(f"视频下载返回路径不存在（等待后仍不存在）: {p}")
                    time.sleep(0.6 * attempt)
                    continue

                # 等待落盘稳定
                ok = _wait_settle(p, max_wait=max(10.0, timeout))
                if not ok:
                    self.logger.warning(f"视频文件未稳定落盘: {p}")
                    time.sleep(0.6 * attempt)
                    continue

                # 基础校验
                try:
                    size = p.stat().st_size
                except Exception as e:
                    self.logger.warning(f"视频读取文件大小失败: {p} err={e}")
                    time.sleep(0.6 * attempt)
                    continue

                if size <= 0:
                    self.logger.warning(f"视频文件大小为0: {p}")
                    time.sleep(0.6 * attempt)
                    continue
                size_mb = size / 1024 / 1024

                self.logger.info(f"视频下载成功: {p} ({size/1024/1024:.1f}MB)")
                api_best_path = p

                if not (ui_fallback and ui_min_size_mb > 0 and size_mb < ui_min_size_mb):
                    return p

                break

            except Exception as e:
                self.logger.warning(f"视频下载异常 attempt={attempt}: {e}")
                time.sleep(0.6 * attempt)
                continue

        # ==============================
        # UI 兜底就在这里
        # 触发条件：
        #   - API 完全失败（api_best_path is None），或
        #   - API 成功但你设置 ui_min_size_mb 且文件偏小（上面 break 到这里）
        # 你要“自动走前面那个UI兜底”，就是加/改这块。
        # ==============================
        if ui_fallback:
            try:
                # 打开视频预览窗口
                try:
                    ui_open_video_preview(msg)
                except:
                    pass

                # 生成一个临时目标名（UI 另存为会用它）
                ui_target = save_dir / f"原视频_{int(time.time())}.mp4"

                # 这里直接调用你之前实现的 UI 函数（可能抛异常）
                # 我们在 safe_download_video 内部兜住，不让异常往外冒
                # ui_path = ui_force_download_original_video(ui_target, timeout=60)
                ui_path = ui_click_original_area_and_save_on_window(ui_target, rel_x=0.95, rel_y=0.06, debug_print=True)

                if ui_path and Path(ui_path).exists() and Path(ui_path).stat().st_size > 0:
                    size_mb = Path(ui_path).stat().st_size / 1024 / 1024
                    self.logger.info(f"UI兜底成功（原视频）: {ui_path} ({size_mb:.1f}MB)")
                    return Path(ui_path)

                self.logger.warning("UI兜底未拿到有效文件（返回空或文件不存在/大小为0）")

            except Exception as e:
                self.logger.warning(f"UI兜底失败: {e}")

        # 如果 UI 兜底失败，但 API 其实拿到过一个文件，那就把 API 的返回给你（至少有视频）
        if api_best_path:
            self.logger.warning(f"UI兜底失败，回退使用API结果: {api_best_path}")
            return api_best_path

        self.logger.error("视频下载最终失败（已耗尽重试与回退）。")
        return None

    # ------------------------------
    # 稳健文件下载
    # ------------------------------
    def safe_download_file(
        self,
        msg,
        save_dir,
        *,
        timeout=30,
        max_retries=3,
        settle_sec=1.0,
    ):
        """
        wxauto4 文件下载鲁棒版：
        1) 先尝试 download(save_dir, force_click=False) （不打开文件）
        2) 失败时再尝试 force_click=True（可能会打开文件，但能触发下载/落盘）
        3) download() 返回 WxResponse 时，不直接判死刑，而是去：
        - save_dir
        - WxParam.DEFAULT_SAVE_PATH（如果可取到）
        - 你自己配置的微信下载目录（可选扩展）
        中做“验收”（按文件名优先，否则找最近新增/更新文件）
        """

        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)

        # 可能的“实际落盘目录”（wxauto4 默认保存路径）
        candidate_dirs = [save_dir]
        try:
            # 若你的项目里能 import 到 WxParam，就加上默认目录
            from wechat_compat import WxParam
            default_dir = Path(WxParam.DEFAULT_SAVE_PATH)
            candidate_dirs.append(default_dir)
        except Exception:
            default_dir = None

        def _list_files(d: Path):
            try:
                if d and d.exists():
                    return {p for p in d.iterdir() if p.is_file()}
            except Exception:
                pass
            return set()

        def _wait_size_stable(p: Path, checks=3, interval=0.25) -> bool:
            try:
                if not (p.exists() and p.is_file() and p.stat().st_size > 0):
                    return False
                last = p.stat().st_size
                stable = 0
                for _ in range(checks * 3):
                    time.sleep(interval)
                    cur = p.stat().st_size
                    if cur == last and cur > 0:
                        stable += 1
                        if stable >= checks:
                            return True
                    else:
                        stable = 0
                        last = cur
                return False
            except Exception:
                return False

        # 尽量拿到文件名：wxauto4 里很多时候 msg.filename 就是文件名
        fname = None
        for attr in ("file_name", "filename", "name", "content"):
            v = getattr(msg, attr, None)
            if isinstance(v, str) and v.strip():
                # content 有时包含别的文本，这里做个保守处理：有后缀更像文件名
                if "." in v:
                    # 这里很重要，这里要把msg.filename里存在非法字符替换掉，否则到后面msg.download()总会失败
                    fname = self.sanitize_filename(v.strip())
                    setattr(msg, attr, fname)
                    break

        def _find_downloaded_file(before_snapshots):
            """
            验收逻辑：
            - 若有 fname：在各候选目录直接找同名文件
            - 否则：找“下载后新增/更新”的最新文件
            """
            # 1) 同名优先
            if fname:
                for d in candidate_dirs:
                    p = d / fname
                    if p.exists() and p.is_file() and p.stat().st_size > 0 and _wait_size_stable(p):
                        return p

            # 2) 增量/最新兜底
            best = None
            best_mtime = 0.0
            for d in candidate_dirs:
                after = _list_files(d)
                before = before_snapshots.get(str(d), set())
                delta = list(after - before)
                # 如果没有 delta，也允许找“mtime 变新”的文件（覆盖写入）
                candidates = delta if delta else list(after)

                for p in candidates:
                    try:
                        mt = p.stat().st_mtime
                        if mt > best_mtime and p.stat().st_size > 0:
                            best = p
                            best_mtime = mt
                    except Exception:
                        continue

            if best and _wait_size_stable(best):
                return best
            return None

        def _move_into_save_dir(src: Path) -> Path:
            # 确保最终落在 save_dir
            dst = save_dir / (src.name if src.name else "downloaded_file")
            if src.resolve() == dst.resolve():
                return dst

            # 避免重名覆盖：如已存在则追加时间戳
            if dst.exists():
                stem = dst.stem
                suffix = dst.suffix
                dst = save_dir / f"{stem}_{int(time.time())}{suffix}"

            # 优先 move，失败再 copy（跨盘符/权限时 move 可能失败）
            try:
                shutil.move(str(src), str(dst))
            except Exception:
                shutil.copy2(str(src), str(dst))
            return dst

        def _snapshot_all_dirs():
            return {str(d): _list_files(d) for d in candidate_dirs}

        # 给微信一点时间刷新消息状态
        time.sleep(settle_sec)

        for attempt in range(1, max_retries + 1):
            before = _snapshot_all_dirs()

            # A) 先不强制点击（不打开文件）
            try:
                ret = msg.download(str(save_dir), False, timeout)
                if isinstance(ret, Path) and ret.exists() and ret.stat().st_size > 0 and _wait_size_stable(ret):
                    self.logger.info(f"文件下载成功（直返Path，attempt {attempt}）：{ret}")
                    return str(ret)
                else:
                    # 失败/返回WxResponse都会走到下面验收
                    self.logger.warning(f"download(force_click=False) 未返回有效Path（attempt {attempt}）：type={type(ret)} ret={ret}")
            except Exception as e:
                self.logger.warning(f"download(force_click=False) 抛异常（attempt {attempt}）：{e}")

            # 验收：看看是不是其实下到 default_dir / 其它目录了
            found = _find_downloaded_file(before)
            if found:
                final = _move_into_save_dir(found)
                self.logger.info(f"文件下载成功（验收命中，attempt {attempt}）：{final}")
                return str(final)

            # B) 再强制点击（可能会打开文件，但能触发下载/落盘）
            before = _snapshot_all_dirs()
            try:
                ret = msg.download(str(save_dir), True, timeout)
                if isinstance(ret, Path) and ret.exists() and ret.stat().st_size > 0 and _wait_size_stable(ret):
                    self.logger.info(f"文件下载成功（force_click=True 直返Path，attempt {attempt}）：{ret}")
                    return str(ret)
                else:
                    self.logger.warning(f"download(force_click=True) 未返回有效Path（attempt {attempt}）：type={type(ret)} ret={ret}")
            except Exception as e:
                self.logger.warning(f"download(force_click=True) 抛异常（attempt {attempt}）：{e}")

            found = _find_downloaded_file(before)
            if found:
                final = _move_into_save_dir(found)
                self.logger.info(f"文件下载成功（force_click=True 后验收命中，attempt {attempt}）：{final}")
                return str(final)

            # 触发一下“预加载”（你图片里用滚动，这里也可以）
            try:
                hwnd = safe_get_wechat_window()
                if hwnd:
                    safe_bring_wechat_to_front(hwnd)
                pyautogui.scroll(600 + 300 * (attempt - 1))
                time.sleep(0.2)
                pyautogui.scroll(-(600 + 300 * (attempt - 1)))
                time.sleep(0.2)
            except Exception as e:
                self.logger.debug(f"滚动预加载失败（忽略）：{e}")

            time.sleep(0.8)

        # 最后一次兜底寻找Wechat下载文件目录
        def _find_and_copy_first(root_dir: str, filename: str, save_dir: str) -> Path | None:
            root_dir = Path(root_dir)
            save_dir = Path(save_dir)
            save_dir.mkdir(parents=True, exist_ok=True)

            for p in root_dir.rglob(filename):
                if p.is_file():
                    target = save_dir / p.name
                    shutil.copy2(p, target)  # copy2 会保留时间戳等信息
                    return target

            return None

        # FIXBUG
        wechat_dir = "C:\\Users\\luhui\\Documents\\WeChat Files\\wxid_beywjp9eofvs19\\FileStorage\\File"
        found = _find_and_copy_first(wechat_dir, fname, save_dir)
        if found:
            self.logger.info(f"兜底寻找，文件在微信下载目录里找到了{wechat_dir} : {found}")
            return str(found)

        self.logger.error("文件多次下载/验收仍失败：放弃。")
        return None

    # ------------------------------
    # 稳健图片下载（增强异常捕获 + 彩色日志）
    # ------------------------------
    def safe_download(self, m, img_dir, max_retries=3):
        """
        超鲁棒的图片下载逻辑。

        包含：
            - 小重试：解决“消息写入缓存未完成”
            - 大重试 + 滚动预加载：触发微信加载旧图缓存
            - 文件完整性检查（文件存在 + size > 0）

        :param m: 微信消息对象（必须为图片）
        :type m: Message
        :param img_dir: 保存路径目录
        :type img_dir: str
        :param max_retries: 大重试次数
        :type max_retries: int
        :return: 保存的图片路径或 None
        :rtype: str or None
        """        
        time.sleep(5)
        try:
            if not hasattr(m, "download"):
                self.logger.warning("消息对象无 download 方法，跳过。")
                return None

            if getattr(m, "type", None) != "image":
                self.logger.warning("不是图片消息，跳过。")
                return None
        except Exception as e:
            self.logger.error(f"检查消息类型出错：{e}")
            return None

        # ⚡ 快速小重试
        for i in range(3):
            try:
                resp = m.download(img_dir, True)
                if isinstance(resp, Path) and resp.exists() and resp.stat().st_size > 0:
                    self.logger.info(f"图片下载成功：{resp}")
                    return str(resp)
            except Exception as e:
                self.logger.warning(f"快速重试 {i+1}/3 失败：{e}")

            time.sleep(0.5)

        # ⚡ 大重试 + 微信滚动预加载
        scroll_pixels = 600
        for attempt in range(max_retries):
            try:
                resp = m.download(img_dir, True)
                if isinstance(resp, Path) and resp.exists() and resp.stat().st_size > 0:
                    self.logger.info(f"图片下载成功：{resp}")
                    return str(resp)
            except Exception as e:
                self.logger.warning(f"下载失败（第 {attempt+1}/{max_retries} 次）：{e}")

            self.logger.info("尝试触发微信预加载（滚动）…")
            try:
                hwnd = safe_get_wechat_window()
                if hwnd:
                    safe_bring_wechat_to_front(hwnd)
                pyautogui.scroll(scroll_pixels)
                time.sleep(0.3)
                pyautogui.scroll(-scroll_pixels)
                time.sleep(0.2)
            except Exception as e:
                self.logger.warning(f"滚动加载失败（忽略）：{e}")

            scroll_pixels += 300
            time.sleep(0.6)

        self.logger.error("多次下载失败，放弃该图片。")
        return None

    # ------------------------------
    # 返回一个目录下所有可能的目录路径
    # ------------------------------
    def get_all_subdirectories(self, root_dir: str) -> set:
        """
        获取指定目录下的所有子目录（递归），过滤掉 ``.`` 开头的目录。

        :param root_dir: 根目录
        :type root_dir: str
        :return: 所有子目录的绝对路径集合
        :rtype: set[str]
        """
        result = set()
        
        for dirpath, dirnames, _ in os.walk(root_dir):
            # 过滤掉所有以 "." 开头的目录
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            
            for dirname in dirnames:
                full_path = os.path.abspath(os.path.join(dirpath, dirname))
                result.add(full_path)
        
        return result

    # ------------------------------
    # 稳健图片找文件名和路径
    # ------------------------------
    def process_images_files_with_remark(
        self,
        start_index: int,
        base_dir: str,
        sender_id: str,
        target_date: datetime
    ):
        """
        处理从 ``msgs[start_index]`` 开始，属于同一人的连续图片/文件 + 备注。

        功能说明：
            - 自动收集同一发送者的连续图片/文件
            - 检测同一发送者的备注（下一条文本消息）
            - 使用备注作为目录路径；若失败则使用 AI 推断最佳目录
            - 多图 → 保存到目录；单图 → 保存到文件名前缀目录
            - 每个保存文件自动重命名
            - 处理完成后，从消息列表中删除已处理条目
            - 返回 ``new_msgs, next_index`` 供调用者迭代

        :param start_index: 起始消息序号
        :type start_index: int
        :param base_dir: 保存的根目录
        :type base_dir: str
        :param sender_id: 学号或昵称，用于文件前缀
        :type sender_id: str
        :param target_date: 用于重命名的日期
        :type target_date: datetime.datetime
        :return: (更新后的消息列表, 下一条消息下标)
        :rtype: tuple
        """

        msgs = self.msgs
        group_name = self.name
        try:
            first_msg = msgs[start_index]
            if not is_pic_message(first_msg) and not is_file_message(first_msg) and not is_video_message(first_msg):
                self.logger.error(f"process_images_files_with_remark: start_index={start_index} 不是 image or file or video")
                return

            sender = first_msg.sender        # 微信昵称，用于判断是否同一个人连续发消息
            sender_remark = get_sender_name(first_msg)  # 群昵称（含学号），用于文件重命名前缀
            images = []
            remark = None
            pre_remark = None

            idx = start_index

            # ---------------------------
            # ① 收集同一个人发的连续所有图片/文件 + 备注
            # ---------------------------
            # 有的时候，备注在图片/文件的最前面
            if start_index > 0:
                pre_msg = msgs[start_index - 1]
                if is_text_message(pre_msg) and pre_msg.sender == sender:
                    pre_remark = pre_msg.content.strip()

            while idx < len(msgs):
                m = msgs[idx]

                if is_time_message(m):
                    break

                if m.sender != sender:
                    # 别人的消息 → 忽略
                    idx += 1
                    continue

                # 该 sender 的图片
                if is_pic_message(m) or is_file_message(m) or is_video_message(m):
                    images.append(m)
                    idx += 1
                    continue

                # 该 sender 的文字（备注）
                if is_text_message(m):
                    remark = m.content.strip()

                # 该 sender 的其它消息，说明已经不是连续图片/文件了
                break

            if not images:
                self.logger.warning("process_images_files_with_remark: 没有收集到图片/文件/视频，跳过当前消息")
                return

            self.logger.info(f"检测到发送者 {sender} 连续发了 {len(images)} 张图片/文件/视频")

            if not remark:
                remark = pre_remark

            # ---------------------------
            # ② 解析备注路径（不带扩展名）
            # ---------------------------
            date_str = target_date.strftime("%Y%m%d")
            if remark:
                dir_name = f"{date_str}_{remark}"
                path_prefix = self.parse_path_pattern(self.sanitize_filename(dir_name), self.sanitize_filename(group_name), base_dir)
            else:
                # 无备注 → 使用群名目录
                dir_name = f"{date_str}_{sender_remark}"
                path_prefix = str(Path(base_dir) / self.sanitize_filename(group_name) / self.sanitize_filename(dir_name))
                self.logger.warning(f"未检测到备注，自动使用目录: {path_prefix}")

            # if not path_prefix:
            #     self.logger.error("备注路径解析失败，放弃处理该批次图片/文件")
            #     return msgs, start_index + 1

            # ---------------------------
            # ③ 保存所有图片/文件
            # ---------------------------
            saved_files = []

            # ---------------------------
            # 先检查目录名是否规范，如果不规范，让AI来生成一个目录名
            # ---------------------------
            inferenced = False
            while True:
                try:
                    # 如果本身目录名也太长，超过20个字符，也用AI来重新生成一个简短的
                    self.logger.debug(f"图片/文件/视频是关于: {path_prefix}")
                    if not inferenced and len(path_prefix) - len(group_name) - len(base_dir) - 2 > 20:
                        inferenced = True
                        raise OSError
                    save_dir = Path(path_prefix)
                    os.makedirs(save_dir, exist_ok=True)
                    self.logger.info(f"目录'{save_dir}'已创建并验证")
                    break
                except OSError:
                    # 用AI去根据remark判断图片/文件应该在哪一目录里
                    # dirs = self.get_all_subdirectories(base_dir)
                    # dirlist = "\n".join(str(i) for i in sorted(dirs))
                    # prompt = "你理解一下" + remark + "觉得它最应用归在下列哪个目录里（只能在列表中选择一项）:" + dirlist
                    prompt = "请根据下面内容，生成一个包含关键信息的、简洁简短的且操作系统可以创建的目录名（字数在15-20个字符之间，不超过20个字符），目录名最好包含人物、地点以及主要发生的事件: "
                    prompt = prompt + remark + "。 目录名不需要包含时间，如果人物或地点无法从提示词中提取则无需提供，不要乱给，也不要胡编乱造，更不要胡说八道。而且，直接给目录名即可，别的字不要加。"
                    # possible_dir = SparkChatClient().chat(prompt)
                    possible_dir = HunyuanChatClient().chat(prompt)
                    self.logger.debug(f"推理结果: {possible_dir}")
                    dir_name = f"{date_str}_{possible_dir}"
                    path_prefix = self.parse_path_pattern(self.sanitize_filename(dir_name), self.sanitize_filename(group_name), base_dir)

            self.logger.info(f"保存到目录 {save_dir}")
            for img in images:
                try:
                    if is_pic_message(img) and "image" in group_names[group_name]:
                        saved = self.safe_download(img, str(save_dir))
                        if saved:
                            final = self.rename_file(saved, sender_remark + '_学号' + sender_id, target_date)
                            saved_files.append(final)
                            self.logger.info(f"保存图片: {final}")
                        else:
                            self.logger.error("图片下载失败，类型异常")
                    elif is_file_message(img) and "file" in group_names[group_name]:
                        saved = self.safe_download_file(img, str(save_dir))
                        # saved = img.download(str(save_dir))
                        if saved:
                            saved_files.append(saved)
                            self.logger.info(f"保存文件: {saved}")
                        else:
                            self.logger.error("文件下载失败，类型异常")
                    elif is_video_message(img) and "video" in group_names[group_name]:
                        # saved = img.download(str(save_dir), True)
                        saved = self.safe_download_video(img, save_dir, ui_min_size_mb=30.0)
                        if saved:
                            final = self.rename_file(saved, sender_remark + '_学号' + sender_id, target_date)
                            saved_files.append(final)
                            self.logger.info(f"保存视频: {final}")
                        else:
                            self.logger.error("视频下载失败，类型异常")
                except Exception as e:
                    self.logger.error(f"保存异常: {e}")

            # 如果目录为空，没有文件下载下来，则删除刚刚建的空目录
            self.logger.info(f"检查是否为空目录，如是则删除掉目录 {save_dir}")
            self.remove_if_empty(save_dir)

            # self.logger.debug(f"保存文件列表 \n {saved_files}")
            # ---------------------------
            # ④ 从 msgs 中删掉已处理图片/文件
            # ---------------------------
            processed_ids = set(id(img) for img in images)
            self.msgs = [m for m in msgs if id(m) not in processed_ids]

            # ---------------------------
            # ⑤ 重新定位 idx（原 idx 在新 msgs 里的位置）
            # ---------------------------
            # next_index = None

            # original_rest = msgs[idx:]

            # for ni, m in enumerate(new_msgs):
            #     if m in original_rest:
            #         next_index = ni
            #         break

            # if next_index is None:
            #     next_index = len(new_msgs)

            # self.logger.info(f"处理完成，下一条 index = {next_index}")

        except Exception as e:
            self.logger.error(f"process_images_files_with_remark: 致命异常: {e}")

    # ------------------------------------------------------
    # 群处理主逻辑
    # ------------------------------------------------------
    def download_group(self, mapping):
        """
        执行微信群的完整“下载任务”。

        包含：
            - 时间判断：只处理目标日期当天的图片/文件
            - 跳过自己发送的消息
            - 文件/图片消息 → 进入批处理逻辑
            - 逐条推进消息指针，支持动态修改消息列表

        :param mapping: 昵称 → 学号映射表
        :type mapping: dict
        :return: None
        :rtype: None
        """        
        days = 0

        i = 0
        while i < len(self.msgs):
            try:
                self.logger.debug(f"现在正处理到第 {i} / {len(self.msgs)} 条消息......")
                m = self.msgs[i]

                # 时间判断
                if is_time_message(m):
                    days = compare_message_date(m, self.target_date)

                # 时间太早的不管
                if days < 0:
                    i += 1
                    continue

                # 跳过自己发的消息
                if m.attr == "self":
                    i += 1
                    continue

                # 处理文件和图片
                if is_file_message(m) or is_pic_message(m) or is_video_message(m):
                    self.process_images_files_with_remark(
                                         i,
                                         download_dir,
                                         str(mapping.get(m.sender, get_sender_name(m))),
                                         self.target_date + timedelta(days))
                    self.logger.debug(f"self.msgs 里删除了被处理的消息，总消息数变少还有 {len(self.msgs)} 条消息")

                # 正常推进
                i += 1
            except Exception as e:
                self.logger.error(f"处理消息时出错：{e}")

def main():
    """
    命令行执行入口。

    功能：
        - 初始化日志与目录
        - 加载群列表 / 家长映射表
        - 初始化 WeChat
        - 让用户选择要处理的群
        - 自动滚动加载历史消息
        - 调用 GroupDownload 执行下载任务

    使用方式：
        在命令行运行：

        .. code-block::

            python download.py

    :return: None
    :rtype: None
    """
    check_version_or_exit(label="download")
    _build_parser().parse_known_args()
    logger = get_logger()
    # sanity check
    if not group_names or not download_dir or not checkin_dir:
        logger.error(f"❌ 下载微信群或下载工作目录或家长学号映射目录没有在环境文件里设置！")
        return

    try:
        os.makedirs(download_dir, exist_ok=True)
    except:
        logger.error(f"❌ 创建目录{download_dir}失败")
        return

    try:
        os.makedirs(checkin_dir, exist_ok=True)
    except:
        logger.error(f"❌ 创建目录{checkin_dir}失败")
        return

    lastweek = datetime.today() - timedelta(days=7)
    date_str = input(
        f"导出从现在到输入日期（格式 yyyy-mm-dd 或 yyyy/mm/dd，例如 {lastweek.strftime('%Y-%m-%d')}）："
    ).strip()

    # ------------------------------
    # 解析输入日期
    # ------------------------------
    date_formats = ["%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"]
    target_date = None

    for fmt in date_formats:
        try:
            target_date = datetime.strptime(date_str, fmt)
            logger.info(f"识别日期成功：{target_date}")
            break
        except Exception:
            continue

    if target_date is None:
        logger.warning("无法识别输入的日期格式，将自动使用“上周”（7日前）作为目标日期。")
        target_date = lastweek

    # ------------------------------
    # 初始化微信
    # ------------------------------
    try:
        wx = WeChat(ads=False)
        logger.info("WeChat SDK 初始化成功！")
    except Exception as e:
        logger.error(f"初始化 WeChat SDK 失败：{e}")
        return

    # 加载“家长昵称 → 学号”映射
    mapping = {}
    try:
        wx2id = Wxname2StudentID(directory=checkin_dir)
        mapping = wx2id.load_mapping()
        logger.info(f"学生映射表加载成功，共 {len(mapping)} 条。")
    except Exception as e:
        logger.error(f"加载映射表失败，将使用空映射：{e}")

    logger.info(f"准备处理微信群：{list(group_names.keys())}")

    # ------------------------------
    # 获取微信窗口
    # ------------------------------
    hwnd = safe_get_wechat_window(logger)
    if hwnd:
        safe_bring_wechat_to_front(hwnd, logger)
    move_mouse_to_chat_center()

    # ------------------------------
    # 主流程：逐个处理群
    # ------------------------------
    for groupname in group_names:
        if not ask_yes_no(f"要处理 {groupname} 吗？", default=True):
            logger.warning(f"跳过群：{groupname}")
            continue

        if not safe_open_chat(wx, groupname, logger):
            continue

        # 再次置顶窗口
        if hwnd:
            safe_bring_wechat_to_front(hwnd, logger)

        # 向上滚动加载历史聊天
        safe_scroll(scroll_times=8*((datetime.now()-target_date).days), logger=logger)

        # 获取所有消息
        msgs = safe_get_all_messages(wx, logger)

        # ------------------------------
        # 执行下载任务
        # ------------------------------
        try:
            grp = GroupDownload(groupname, msgs, target_date, logger)
            grp.download_group(mapping)
            logger.info(f"{groupname} 下载任务已完成！")
        except Exception as e:
            logger.error(f"处理群 {groupname} 发生错误：{e}")

    logger.info("所有群组处理已结束！")

if __name__ == "__main__":
    main()
