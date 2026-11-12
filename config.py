# config.py
"""
读取 .env 环境变量的统一配置模块。

主要功能：
    - 使用 python-dotenv 自动加载当前目录下的 ``.env`` 文件。
    - 提供三类读取方法：
        1）``get_env``：读取单个环境变量
        2）``get_env_set``：将环境变量解析为集合（set）
        3）``get_env_map``：将环境变量解析为 dict（JSON 格式）

模块作用：
    - 统一解析、校验环境变量输入格式
    - 提供日志输出，便于排查环境变量错误
    - 全系统的配置项均依赖本模块读取

本模块被 export.py、update.py、checkin.py 等核心文件调用。
"""

import os
import json
from datetime import datetime
from dotenv import load_dotenv
from logger import get_logger

# 自动加载当前目录下的 .env
load_dotenv()

def get_env(name: str, default=None, required=False, logger=None):
    """
    通用环境变量读取函数。

    功能：
        - 从系统环境中读取指定名称的环境变量。
        - 支持默认值 ``default``。
        - 若 ``required=True`` 且变量不存在，会记录错误日志。

    :param name: 环境变量名称。
    :type name: str
    :param default: 若未设置环境变量时返回的默认值。
    :type default: Any or None
    :param required: 是否为必填环境变量。
    :type required: bool
    :param logger: 可选自定义 logger。
    :type logger: logging.Logger or None
    :return: 变量值（字符串），或默认值。
    :rtype: str or Any
    """
    logger = get_logger(logger)

    value = os.getenv(name, default)

    if required and (value is None or value == ""):
        logger.error(f"环境变量 {name} 未设置，返回空")

    return value

def get_env_set(name: str, default=set(), required=False, logger=None):
    """
    从环境变量读取并解析为 ``set``。

    支持两种输入格式：
        1. 逗号分隔： ``a,b,c``
        2. JSON 数组： ``["a", "b", "c"]``

    行为说明：
        - 若环境变量不存在且 ``required=True``，返回空集合并记录警告。
        - 若格式解析失败，会记录错误日志并返回空集合。
        - 自动去除两端空白字符，并对每个元素调用 ``strip()``。

    :param name: 环境变量名称。
    :type name: str
    :param default: 默认集合（很少使用）。
    :type default: set
    :param required: 是否必须存在该变量。
    :type required: bool
    :param logger: 可选自定义 logger。
    :type logger: logging.Logger or None
    :return: 一个 set 类型的集合。
    :rtype: set[str]
    """
    logger = get_logger(logger)

    raw = os.getenv(name)

    if required and raw is None:
        logger.warning(f"环境变量 {name} 未设置，返回空集合")
        return set()

    raw = raw.strip()
    if not raw:
        return set()

    # ① JSON 解析格式：["语文群", "数学群"]
    if raw.startswith("[") and raw.endswith("]"):
        try:
            data = json.loads(raw)
            return set(str(x).strip() for x in data)
        except Exception as e:
            logger.error(f"环境变量 {name} JSON 解析失败：{e}，原始：{raw}")
            return set()

    # ② 逗号分隔格式：语文群,英语群,数学群
    try:
        return set(x.strip() for x in raw.split(",") if x.strip())
    except Exception as e:
        logger.error(f"环境变量 {name} 解析失败：{e}，原始：{raw}")
        return set()

def get_env_map(name: str, default=dict(), required=False, logger=None):
    """
    从环境变量读取一个 JSON 字典（mapping）。

    环境变量必须是字典的 JSON 字符串，例如：

    .. code-block:: json

        {
            "学校群": ["image", "file"],
            "家长群": ["file"]
        }

    行为说明：
        - JSON 必须解析为字典；若为数组或其他类型则视为错误。
        - 若解析失败将记录错误信息并返回空 dict。
        - 若变量不存在，会记录警告并返回空 dict。

    :param name: 环境变量名称。
    :type name: str
    :param default: 默认返回字典（一般不使用）。
    :type default: dict
    :param required: 是否必须存在环境变量（未使用但保留接口）。
    :type required: bool
    :param logger: 可选 logger。
    :type logger: logging.Logger or None
    :return: 从 JSON 解析得到的字典。
    :rtype: dict
    """
    logger = get_logger(logger)

    raw = os.getenv(name)
    if raw is None:
        logger.warning(f"环境变量 {name} 未设置，返回空 dict()")
        return dict()

    raw = raw.strip()
    if not raw:
        return dict()

    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        else:
            raise ValueError("JSON 不是字典类型")
    except Exception as e:
        logger.error(f"解析环境变量 {name} 失败：{e}。原值：{raw}")
        return dict()


def find_excel_file(checkin_dir: str, groupname: str, date: datetime) -> str:
    """
    查找指定日期的 Excel 累计文件，兼容新旧两种命名格式。

    从 2026-08-09 起新文件使用 ``yyyymmdd`` 格式（含年份），
    旧文件使用 ``mmdd`` 格式。读取时优先尝试新格式，
    找不到再回退到旧格式，保证过渡期兼容。

    :param checkin_dir: 打卡工作目录
    :type checkin_dir: str
    :param groupname: 群名称（如 "英语群"）
    :type groupname: str
    :param date: 目标日期
    :type date: datetime
    :return: 存在的文件路径（优先新格式），若都不存在则返回新格式路径
    :rtype: str
    """
    new_path = os.path.join(checkin_dir, f"{groupname}_{date.strftime('%Y%m%d')}.xlsx")
    if os.path.exists(new_path):
        return new_path
    old_path = os.path.join(checkin_dir, f"{groupname}_{date.strftime('%m%d')}.xlsx")
    if os.path.exists(old_path):
        return old_path
    # 都不存在 → 返回新格式路径（后续代码会处理文件不存在的情况）
    return new_path


_AUTO_YES = False


def set_auto_yes(enabled=True):
    """
    启用/禁用「自动 Yes」模式（无人值守运行）。

    启用后 ask_yes_no 不再询问，一律返回 True（即 Yes）。
    供 export_v2.py 等入口脚本的 --yes / -y 参数调用。

    :param enabled: True 开启自动 Yes，False 恢复交互询问。
    :type enabled: bool
    """
    global _AUTO_YES
    _AUTO_YES = enabled


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    """
    询问用户 y/n，支持回车取默认值。

    提示文本会自动追加 ``(Y/n)`` 或 ``(y/N)`` 标识默认值。
    接受 ``y`` / ``yes`` 作为肯定回答，``n`` / ``no`` 作为否定回答。
    非法输入时会提示重新输入，直到给出合法回答。

    :param prompt: 提示文本（不含选择提示）。
    :type prompt: str
    :param default: 默认值，True=yes, False=no。
    :type default: bool
    :return: True 表示用户确认；False 表示否定。
    :rtype: bool
    """
    if _AUTO_YES:
        return True
    hint = "(Y/n)" if default else "(y/N)"
    full_prompt = f"{prompt} {hint}："
    # 询问前把控制台拉到前台，确保键盘输入有效
    try:
        from safewechat import bring_console_to_front
        bring_console_to_front()
    except Exception:
        pass
    while True:
        resp = input(full_prompt).strip().lower()
        if resp == "":
            return default
        if resp in ("y", "yes"):
            return True
        if resp in ("n", "no"):
            return False
        print(f"无效输入 '{resp}'，请输入 y/yes 或 n/no，或直接回车取默认值")