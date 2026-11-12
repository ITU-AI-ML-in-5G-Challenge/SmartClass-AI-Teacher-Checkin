"""
打卡统计 Excel 处理模块。

主要功能：
    - 使用 xlwings 自动重新计算 Excel 中的所有公式（适用于含 SUM、VLOOKUP、跨表引用等情况）
    - 从 Excel 文件中安全读取统计值（data_only=True，确保读取到公式结果）
    - 将 Excel 作为“累计打卡次数”的权威来源供数学群、语文群统计使用

适用场景：
    - 数学群、语文群统计模块（update.py）依赖本模块读取每日的累计结果
    - Daily check-in 守护进程（checkin.py）在执行 update 前调用 recalc_and_save_excel()
"""
import openpyxl
from openpyxl import Workbook
import xlwings as xw
import os

class CheckInStats:
    """
    用于读取、计算和维护“打卡统计 Excel 文件”的工具类。

    功能包括：
        - 重新计算 Excel 中的公式，并保存处理后的文件。
        - 从 Excel 文件中加载打卡统计数据。
    
    :param excel_path: Excel 文件路径。
    :type excel_path: str
    """    
    def __init__(self, excel_path):
        """
        初始化 CheckInStats 对象，并立即对 Excel 执行一次公式重新计算。

        :param excel_path: Excel 文件路径。
        :type excel_path: str
        """        
        self.excel_path = excel_path
        self.recalc_and_save_excel()

    def recalc_and_save_excel(self):
        """
        使用 Excel COM 接口（xlwings）打开指定 Excel 文件，
        强制重新计算所有公式（包括跨工作表或依赖公式），并保存结果。

        本方法使用“隐藏模式”打开 Excel，不会显示窗口。
        若文件无法打开或保存，异常将由调用者捕获。

        :return: 无返回值。
        :rtype: None
        """        
        filepath = self.excel_path

        # 文件不存在时跳过公式重算：load_stats() 会因此返回空字典 {}，
        # 由调用方（如 MathsGroup.save_text_csv）按“打卡第一天”初始化为 0。
        if not os.path.isfile(filepath):
            return

        app = xw.App(visible=False)  # 不显示 Excel 窗口
        wb = None
        try:
            wb = app.books.open(filepath)
            wb.app.calculate()        # 强制计算所有公式
            wb.save()
        finally:
            if wb:
                wb.close()
            app.quit()

    def load_stats(self):
        """
        从 Excel 中读取打卡统计数据，返回 ``{student_id: times}`` 的映射。

        说明：
            - 仅读取活动工作表。
            - 默认从第 3 行开始读取（跳过表头）。
            - 若文件不存在，返回空字典而不报错。
            - ``data_only=True`` 保证读取的是公式计算后的值。

        :return: 一个字典，键为学生学号，值为对应打卡次数。
        :rtype: dict[int, int]
        """

        try:
            wb = openpyxl.load_workbook(self.excel_path, data_only=True)
            sheet = wb.active
        except FileNotFoundError:
            return {}  # 文件不存在 → 空字典

        stats = {}

        for row in sheet.iter_rows(min_row=3, values_only=True):
            student_id, times = row[0], row[1]
            if student_id and times:
                stats[int(str(student_id).strip())] = int(str(times).strip())

        return stats

