"""
Excel 打卡统计更新模块。

主要功能：
    - 将前一日 Excel 的统计数据平移到其他列（“历史数据”）
    - 读取当日 CSV（英语群 / 数学群 / 语文群）
    - 根据 CSV 更新 Excel 对应列（新的一天数据）
    - 自动处理列名不一致、编码 BOM、多余空格等问题
    - 自动生成 new_excel_file 文件名（按 mmdd 命名）
    - 可独立运行，也可由守护进程调用

模块结构：
    - Updater：基类，负责 CSV 加载、Excel 打开与计算
    - EnglishUpdater：英语群专用（3 类打卡）
    - MathsUpdater：数学/语文群专用（二类打卡）
    - UpdaterFactory：自动创建正确的 Updater 实例
"""
import argparse
import sys


def _build_parser():
    """构建命令行参数解析器（仅依赖 argparse，供 --help 提前退出复用）。"""
    return argparse.ArgumentParser(
        description="根据日期更新所有群的 Excel 文件。",
    )


# 提前处理 --help / -h：只打印帮助即退出，避免加载 pandas/xlwings 等重模块
if "--help" in sys.argv or "-h" in sys.argv:
    _build_parser().print_help()
    sys.exit(0)


import pandas as pd
import openpyxl
import re
import os
from datetime import datetime, timedelta
from difflib import get_close_matches
import xlwings as xw
from logger import get_logger
from config import get_env, get_env_set, ask_yes_no, find_excel_file
from version import check_version_or_exit

group_names = get_env_set("CHECKIN_GROUP_NAMES")
checkin_dir = get_env("CHECKIN_DIR")

class Updater:
    """
    Excel 更新基类，包含读取 CSV、验证文件、重新计算 Excel 等通用逻辑。

    :param csv_file: 当天打卡 CSV 文件路径
    :type csv_file: str
    :param old_excel_file: 昨日 Excel（提供历史累计）
    :type old_excel_file: str
    :param new_excel_file: 待生成的今日 Excel 文件路径
    :type new_excel_file: str
    :param logger: 日志器
    :type logger: logging.Logger or None
    """
    def __init__(self, csv_file: str, old_excel_file: str, new_excel_file: str, logger = None):
        """
        初始化 Updater，并检查 CSV 与旧 Excel 文件是否存在。

        若文件缺失 → 抛出异常。

        :param csv_file: CSV 文件路径
        :param old_excel_file: 前一天 Excel 文件路径
        :param new_excel_file: 输出 Excel 文件路径
        :param logger: 日志器
        """        
        self.csv_file = csv_file
        self.old_excel_file = old_excel_file
        self.new_excel_file = new_excel_file
        self.logger = get_logger(logger)

        # 检查输入文件是否存在
        missing_files = []
        if not os.path.exists(csv_file):
            missing_files.append(csv_file)
        if not os.path.exists(old_excel_file):
            missing_files.append(old_excel_file)

        if missing_files:
            self.logger.error("❌ 以下输入文件不存在：")
            for f in missing_files:
                self.logger.error("   -", f)
            self.logger.error("请检查文件路径或日期是否正确。")
            raise Exception("文件不存在")


    def recalc_and_save_excel(self, filepath=None):
        """
        使用 xlwings 打开 Excel，并强制重新计算所有公式。

        用途：
            - Excel 中可能有跨表公式，需要刷新计算后缓存值才正确。
            - 默认 recalc self.old_excel_file，也可传入其他路径。

        :param filepath: 要重算的 Excel 路径，默认 self.old_excel_file
        :return: None
        """
        if filepath is None:
            filepath = self.old_excel_file
        self.logger.info(f"📘 打开 Excel 文件计算公式：{filepath}")

        app = xw.App(visible=False, add_book=False)
        app.screen_updating = False
        app.display_alerts = False
        try:
            wb = app.books.open(filepath)
            wb.app.calculate()        # 强制计算所有公式
            wb.save()
            self.logger.info("✅ Excel 已重新计算并保存。")
        finally:
            wb.close()
            app.quit()

    def ask_overwrite(self):
        """
        若 new_excel_file 已存在，提示用户是否覆盖。

        :return: True 表示允许覆盖；False 表示取消
        :rtype: bool
        """        
        if os.path.exists(self.new_excel_file):
            if not ask_yes_no(f"⚠️ 文件 {self.new_excel_file} 已存在，是否覆盖？", default=True):
                return False
        return True
  
    def load_csv_safely(self, columns):
        """
        安全加载 CSV 文件，并将列名标准化为指定 columns。

        包含容错：
            - 自动尝试 utf-8-sig / utf-8 / gbk 三种编码
            - 标准化列名：去除 BOM、空格、制表符、全角空格
            - 模糊匹配轻微差异列名（如少一个字、符号不一致）
            - 若最终仍缺列 → 抛 KeyError

        :param columns: 期望的标准列名列表
        :type columns: list[str]
        :return: DataFrame
        :rtype: pandas.DataFrame
        """        
        # 先试 utf-8-sig（可自动去掉BOM），不行再退GBK
        for enc in ("utf-8-sig", "utf-8", "gbk"):
            try:
                df = pd.read_csv(self.csv_file, encoding=enc)
                break
            except Exception:
                df = None
        if df is None:
            raise RuntimeError("无法读取CSV，请检查编码。")

        # 标准化列名：去BOM、去空格（含全角）、去制表符
        def norm(s: str) -> str:
            s = str(s).replace("\ufeff", "")  # 去BOM
            s = s.replace("\u3000", " ")      # 全角空格→半角
            s = re.sub(r"\s+", "", s)         # 去所有空白
            return s

        # 期望列名（标准化前）
        expected_names = columns

        # 构建标准化映射：规范名 -> 原始名
        cols_by_norm = {norm(c): c for c in df.columns}

        # 尝试把期望列名映射到实际列名（容错：空白/轻微差异）
        resolved = {}
        for need in expected_names:
            key = norm(need)
            if key in cols_by_norm:
                resolved[need] = cols_by_norm[key]
            else:
                # 允许轻微差异，用模糊匹配兜底（比如多了/少了一个字、斜杠）
                candidates = get_close_matches(key, cols_by_norm.keys(), n=1, cutoff=0.75)
                if candidates:
                    resolved[need] = cols_by_norm[candidates[0]]

        # 检查缺失
        missing = [c for c in expected_names if c not in resolved]
        if missing:
            raise KeyError(
                "CSV缺少这些列（或列名有差异）："
                + "、".join(missing)
                + f"\n实际列名：{list(df.columns)}"
            )

        # 用解析到的真实列名重命名，后续代码就用“标准名”访问
        df = df.rename(columns={v: k for k, v in resolved.items()})
        return df

    def update_excel_with_target_data(self):
        """
        子类必须实现：将 df_csv 的数据写入到 Excel。

        :return: 生成后的 Excel 路径或 None
        :rtype: str or None
        """        
        pass


class EnglishUpdater(Updater):
    """
    英语群 Excel 更新器（3 类打卡：单词/课文/语法）。

    Excel 结构规则：
        - BCD/E 列：昨日累计（由昨日 Excel 得出）
        - FGH 列：今日打卡次数（由今日 CSV 得出）
        - IJKL 列：将昨日数据平移到这些列
    """
    def __init__(self, name="英语群", csv_file: str = None, old_excel_file: str = None, new_excel_file: str = None, logger = None):
        """
        初始化英语群更新器，并指定 CSV 需要的列名。

        :param name: 群名称
        :type name: str
        :param csv_file: CSV 文件路径
        :type csv_file: str
        :param old_excel_file: 昨日 Excel
        :type old_excel_file: str
        :param new_excel_file: 输出 Excel
        :type new_excel_file: str
        :param logger: 日志器
        """        
        self.name = name
        # 期望列名（标准化前）
        self.csv_file_titles = [
            "学号",
            "当天单词词组默写次数",
            "当天课文背诵次数",
            "当天语法知识点学习次数",
        ]
        super().__init__(csv_file, old_excel_file, new_excel_file, logger)

    def load_csv_safely(self):
        """
        加载 CSV 并确保包含英语群所需 4 列。

        :return: DataFrame
        :rtype: pandas.DataFrame
        """        
        return super().load_csv_safely(self.csv_file_titles)

    def update_excel_with_target_data(self):
        """
        将英语 CSV 数据更新到 Excel：

        流程：
            1) 读取昨日 Excel（data_only=True）
            2) 读取今日 CSV，并转换为 dict
            3) 将昨日 B/C/D/E 列平移到 I/J/K/L 列
            4) 将今日 B/C/D 填入 F/G/H
            5) 保存新的 Excel 文件

        :return: 新 Excel 文件路径或 None
        :rtype: str or None
        """        
        # 读取 CSV 文件（学号与打卡次数数据）
        df_csv = self.load_csv_safely()
        if df_csv.empty:
            self.logger.warning(f"⚠️ CSV 文件为空：{self.csv_file}")
            return None

        # 用 data_only=True 打开“值视图”，读取 BCDE 的已计算数值
        wb_vals = openpyxl.load_workbook(self.old_excel_file, data_only=True, read_only=True)
        sh_vals = wb_vals.active

        # 读取 Excel 数据
        wb = openpyxl.load_workbook(self.old_excel_file)
        sheet = wb.active
        
        if sheet.max_row < 2:
            self.logger.warning(f"⚠️ Excel 文件中没有学生数据：{self.old_excel_file}")
            return None

        # 获取昨天的 Excel 数据（这里假设昨天的数据已经放置到 CDE 列）
        yesterday_data = {}
        for row in range(1, sh_vals.max_row + 1):
            student_id = sh_vals.cell(row=row, column=1).value
            if not student_id:
                continue
            student_id_str = str(student_id).strip()  # 去掉空格
            if not student_id_str.isdigit():
                continue

            yesterday_data[student_id] = {
                'B': sh_vals.cell(row=row, column=2).value,
                'C': sh_vals.cell(row=row, column=3).value,
                'D': sh_vals.cell(row=row, column=4).value,
                'E': sh_vals.cell(row=row, column=5).value,
            }
        
        # 获取 CSV 文件中当天的数据
        target_date_data = {}
        for _, row in df_csv.iterrows():
            sid = str(row[self.csv_file_titles[0]]).strip()
            if not sid.isdigit():
                continue
            target_date_data[int(sid)] = {
                "B": row[self.csv_file_titles[1]],
                "C": row[self.csv_file_titles[2]],
                "D": row[self.csv_file_titles[3]],
            }
        
        # 更新 Excel 文件
        for row in range(1, sheet.max_row + 1):
            student_id = sheet.cell(row=row, column=1).value
        
            if student_id in yesterday_data:
                # 将前一天的数据平移到 IJK 列
                sheet.cell(row=row, column=9, value=yesterday_data[student_id]['B'])
                sheet.cell(row=row, column=10, value=yesterday_data[student_id]['C'])
                sheet.cell(row=row, column=11, value=yesterday_data[student_id]['D'])
                sheet.cell(row=row, column=12, value=yesterday_data[student_id]['E'])
            
            if student_id in target_date_data:
                # 更新当天的数据到 FGH 列
                sheet.cell(row=row, column=6, value=target_date_data[student_id]['B'])
                sheet.cell(row=row, column=7, value=target_date_data[student_id]['C'])
                sheet.cell(row=row, column=8, value=target_date_data[student_id]['D'])

        # 保存新的 Excel 文件，命名为当天的日期（mmdd格式）
        new_file_path = None
        try:
            wb.save(self.new_excel_file)
            new_file_path = self.new_excel_file
        except PermissionError:
            self.logger.error(f"❌ 无法保存文件：{self.new_excel_file}\n可能是该文件正在被 Excel 打开，请关闭后重试。")
        
        wb.close()
        wb_vals.close()
        return new_file_path

class MathsUpdater(Updater):
    """
    数学 / 语文群 Excel 更新器（单一打卡次数）。
    """
    def __init__(self, name="数学群", csv_file: str = None, old_excel_file: str = None, new_excel_file: str = None, logger = None):
        """
        初始化数学/语文群更新器，指定 CSV 所需列名。

        :param name: 群名称
        :param csv_file: CSV 文件路径
        :param old_excel_file: 昨日 Excel
        :param new_excel_file: 输出 Excel
        :param logger: 日志器
        """        
        self.name = name
        # 期望列名（标准化前）
        self.csv_file_titles = [
            "学号",
            f"当天{self.name[0:2]}打卡"
        ]
        super().__init__(csv_file, old_excel_file, new_excel_file, logger)

    def load_csv_safely(self):
        """
        加载数学 CSV 并确保包含 2 列：

            - 学号
            - 当天数学打卡（或语文打卡）

        :return: DataFrame
        :rtype: pandas.DataFrame
        """        
        return super().load_csv_safely(self.csv_file_titles)

    def update_excel_with_target_data(self):
        """
        将数学 CSV 数据更新到 Excel。

        Excel 结构规则：
            - B 列：昨日打卡
            - C 列：今日打卡
            - D 列：昨日平移数据

        :return: 新 Excel 文件路径
        :rtype: str or None
        """        
        # 读取 CSV 文件（学号与打卡次数数据）
        df_csv = self.load_csv_safely()
        if df_csv.empty:
            self.logger.warning(f"⚠️ CSV 文件为空：{self.csv_file}")
            return None

        # 用 data_only=True 打开“值视图”，读取 BCDE 的已计算数值
        wb_vals = openpyxl.load_workbook(self.old_excel_file, data_only=True, read_only=True)
        sh_vals = wb_vals.active

        # 读取 Excel 数据
        wb = openpyxl.load_workbook(self.old_excel_file)
        sheet = wb.active
        
        if sheet.max_row < 2:
            self.logger.warning(f"⚠️ Excel 文件中没有学生数据：{self.old_excel_file}")
            return None

        # 获取昨天的 Excel 数据（这里假设昨天的数据已经放置到 CDE 列）
        yesterday_data = {}
        for row in range(1, sh_vals.max_row + 1):
            student_id = sh_vals.cell(row=row, column=1).value
            if not student_id:
                continue
            student_id_str = str(student_id).strip()  # 去掉空格
            if not student_id_str.isdigit():
                continue

            yesterday_data[student_id] = {
                'B': sh_vals.cell(row=row, column=2).value,
               }
        
        # 获取 CSV 文件中当天的数据
        target_date_data = {}
        for _, row in df_csv.iterrows():
            sid = str(row[self.csv_file_titles[0]]).strip()
            if not sid.isdigit():
                continue
            target_date_data[int(sid)] = {
                "B": row[self.csv_file_titles[1]],
            }
        
        # 更新 Excel 文件
        for row in range(1, sheet.max_row + 1):
            student_id = sheet.cell(row=row, column=1).value

            if student_id in yesterday_data:
                # 将前一天的数据平移到 D 列
                sheet.cell(row=row, column=4, value=yesterday_data[student_id]['B'])

            if student_id in target_date_data:
                # 更新当天的数据到 C 列
                sheet.cell(row=row, column=3, value=target_date_data[student_id]['B'])

        # 保存新的 Excel 文件，命名为当天的日期（mmdd格式）
        new_file_path = None
        try:
            wb.save(self.new_excel_file)
            new_file_path = self.new_excel_file
        except PermissionError:
            self.logger.error(f"❌ 无法保存文件：{self.new_excel_file}\n可能是该文件正在被 Excel 打开，请关闭后重试。")
        
        wb.close()
        wb_vals.close()
        return new_file_path

def reset_monthly_excel(excel_path, groupname, logger=None):
    """
    月初清零：将上月底 Excel 数据列清零，保留学号和公式列。

    月初第一天运行时，直接将数据列归零，使得当月打卡从零开始。
    公开仓库保留上月底原始文件作为备份，无需本地副本。

    清零规则：
        - 数学群 / 语文群：C 列（当天打卡）和 D 列（过往天数）→ 0
        - 英语群：F 列及之后全部 → 0（B~E 列为公式，自动重算为 0）

    注意：Excel 第 1 行是合计公式行（=SUM(...)），第 2 行才是表头，
    数据从第 3 行开始。清零时只处理 A 列为纯数字学号的数据行，
    跳过合计行和表头行，避免把表头文字（如“当天语文打卡”）清零。

    :param excel_path: 上月底 Excel 文件路径
    :type excel_path: str
    :param groupname: 群名称
    :type groupname: str
    :param logger: 日志器
    """
    log = get_logger(logger)

    if not os.path.exists(excel_path):
        log.warning(f"月初清零：文件不存在 → {excel_path}")
        return

    # 清零数据列
    try:
        wb = openpyxl.load_workbook(excel_path)
        sheet = wb.active

        if groupname == "英语群":
            # F 列及之后全部清零（A=学号，B~E=公式），跳过合计/表头行
            for row in sheet.iter_rows(min_row=2):
                sid = row[0].value
                if sid is None or not str(sid).strip().isdigit():
                    continue
                for cell in row[5:]:
                    cell.value = 0
        elif groupname in ("数学群", "语文群"):
            # C 列和 D 列清零（A=学号，B=公式），跳过合计/表头行
            for row in sheet.iter_rows(min_row=2):
                sid = row[0].value
                if sid is None or not str(sid).strip().isdigit():
                    continue
                if len(row) >= 4:
                    row[2].value = 0  # C 列
                    row[3].value = 0  # D 列
        else:
            log.warning(f"月初清零：未知群类型 {groupname}，跳过")
            wb.close()
            return

        wb.save(excel_path)
        wb.close()
        log.info(f"月初清零：已清零 → {excel_path}")
    except Exception as e:
        log.error(f"月初清零：清零失败 → {e}")


class UpdaterFactory:
    """
    Excel 更新器工厂。

    负责：
        - 根据群名称自动选择 EnglishUpdater 或 MathsUpdater
        - 自动生成 CSV / old_excel / new_excel 的文件路径
        - 打印输入、输出文件路径日志
    """
    def create_updater(self, updatername: str, target_date: datetime = None, logger = None) -> Updater:
        """
        创建适配 updatername 的 Updater 实例。

        自动规则：
            - CSV 文件：{群名}_{yyyy-mm-dd}.csv
            - 昨日 Excel：{群名}_{yyyymmdd}.xlsx（兼容旧 mmdd 格式）
            - 今日 Excel：{群名}_{yyyymmdd}.xlsx

        :param updatername: 群名称（英语群/数学群/语文群）
        :type updatername: str
        :param target_date: 目标日期
        :type target_date: datetime.datetime
        :param logger: 日志器
        :return: Updater 子类实例
        :rtype: Updater
        """        
        if target_date is None:
            target_date = datetime.today()

        logger = get_logger(logger)
        yesterday = target_date - timedelta(days=1)
        # 自动生成文件路径
        csv_file = os.path.join(checkin_dir, f"{updatername}_{target_date.strftime('%Y-%m-%d')}.csv")
        old_excel_file = find_excel_file(checkin_dir, updatername, yesterday)
        new_excel_file = os.path.join(checkin_dir, f"{updatername}_{target_date.strftime('%Y%m%d')}.xlsx")
        
        # 打印检查信息
        logger.info(f"输入 CSV 文件: {csv_file}")
        logger.info(f"输入 Excel 文件: {old_excel_file}")
        logger.info(f"输出 Excel 文件: {new_excel_file}")

        if updatername == "英语群":
            return EnglishUpdater(updatername, csv_file, old_excel_file, new_excel_file, logger)
        elif updatername == "数学群":        
            return MathsUpdater(updatername, csv_file, old_excel_file, new_excel_file, logger)
        elif updatername == "语文群":
            return MathsUpdater(updatername, csv_file, old_excel_file, new_excel_file, logger)
        else:
            raise Exception("不认识的群名称")

def main():
    """
    命令行入口：根据日期更新所有群的 Excel 文件。

    工作流程：
        1. 读取 .env 配置（CHECKIN_GROUP_NAMES, CHECKIN_DIR）
        2. 输入目标日期（兼容多种格式）
        3. 遍历各群名称
        4. 生成 Updater，并询问是否覆盖旧 Excel
        5. 执行：recalc → update_excel_with_target_data
        6. 输出生成成功的 Excel 文件路径

    :return: None
    """
    check_version_or_exit(label="update")
    _build_parser().parse_known_args()
    logger = get_logger()
    # sanity check
    if not group_names or not checkin_dir:
        logger.error(f"❌ 打卡微信群和打卡工作目录没有在环境文件里设置！")
        return

    try:
        os.makedirs(checkin_dir, exist_ok=True)
    except:
        logger.error(f"❌ 创建目录{checkin_dir}失败")
        return
    
    # 用户输入完整日期
    yesterday_date = datetime.today() - timedelta(days=1)
    date_str = input(f"请输入要生成的日期（格式 yyyy-mm-dd 或 yyyy/mm/dd，例如 {yesterday_date.strftime('%Y-%m-%d')}）：").strip()

    # 兼容不同日期格式
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
        logger.warning("无法识别输入的日期格式，将自动使用“昨天”作为目标日期。")
        target_date = yesterday_date

    factory = UpdaterFactory()
    for name in group_names:
        if not ask_yes_no(f"要处理{name}吗？", default=True):
            continue

        updater = factory.create_updater(name, target_date, logger)

        if not updater.ask_overwrite():
            continue

        # 调用你写好的核心函数
        try:
            updater.recalc_and_save_excel()

            # 月初第一天：先清零上月底文件，再基于它生成新文件
            if target_date.day == 1 and os.path.exists(updater.old_excel_file):
                reset_monthly_excel(updater.old_excel_file, name, logger)
                updater.recalc_and_save_excel()  # 清零后刷新公式缓存

            new_excel_file = updater.update_excel_with_target_data()
            if not new_excel_file is None:
                logger.info(f"✅ 已成功生成：{new_excel_file}")
        except PermissionError:
                logger.error(f"❌ 无法保存 {new_excel_file}，请关闭该文件后重试。")
        except Exception as e:
                logger.error(f"❌ 发生错误：{e}")
    
if __name__ == "__main__":
    main()