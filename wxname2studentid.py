"""
微信群昵称（微信号） ↔ 学号 映射读写模块。

主要功能：
    - 将微信群家长微信号与学生学号进行持久化映射
    - 保存到 mapping.xlsx（缺省文件名）
    - 当文件不存在时自动创建
    - 读取、写入均具备容错性
    - 写入时合并新旧映射，并按学号升序排序

适用场景：
    - 微信打卡统计程序
    - 自动点评机器人中的家长识别
    - 下载程序中用于构建文件名前缀
"""
import openpyxl
from openpyxl import Workbook
import os

MAPPING_EXCEL_NAME = "mapping.xlsx"

class Wxname2StudentID:
    """
    负责管理微信号 ↔ 学号 的 Excel 映射表。

    映射表格式（Sheet1）：
        A列：微信号
        B列：学号

    特性：
        - 文件不存在时不报错，读取返回空字典
        - 写入时自动合并并覆盖相同微信号
        - 写入后按学号升序排序，保持 Excel 美观一致

    :param directory: 存放映射表 Excel 的目录
    :type directory: str or None
    :param file: 文件名（默认 mapping.xlsx）
    :type file: str
    """
    def __init__(self, directory = None, file = MAPPING_EXCEL_NAME):
        """
        初始化映射器，确定 Excel 路径。

        若 directory 为空，则使用当前工作目录。

        :param directory: 目录路径
        :type directory: str or None
        :param file: 映射表文件名
        :type file: str
        """        
        if not directory:
            directory = os.getcwd()
        self.excel_path = os.path.join(directory, MAPPING_EXCEL_NAME)

    def load_mapping(self, sheet_name="Sheet1"):
        """
        读取 Excel 映射，返回 dict {wx_id: student_id}。

        行为说明：
            - 若文件不存在 → 返回 {}
            - 若 sheet 不存在 → 返回 {}
            - 自动跳过空行、空单元格
            - 自动 strip 去除多余空白字符

        :param sheet_name: 工作表名称
        :type sheet_name: str
        :return: 微信号到学号的映射表
        :rtype: dict[str, str]
        """
        try:
            wb = openpyxl.load_workbook(self.excel_path)
        except FileNotFoundError:
            return {}  # 文件不存在 → 空字典

        if sheet_name not in wb.sheetnames:
            return {}  # sheet 不存在 → 空字典

        sheet = wb[sheet_name]

        mapping = {}

        for row in sheet.iter_rows(min_row=2, values_only=True):
            wx_id, student_id = row[0], row[1]
            if wx_id and student_id:
                mapping[str(wx_id).strip()] = str(student_id).strip()

        return mapping

    def save_mapping(self, mapping, sheet_name="Sheet1"):
        """
        保存映射表到 Excel。

        行为：
            - 若文件不存在 → 自动创建
            - 若 sheet 不存在 → 自动创建
            - 读取 Excel 中现有的映射 existing_dict
            - 用传入 mapping 覆盖 existing_dict 中同微信号的项
            - 按学号升序排序（数字优先）
            - 清空旧数据并重写数据行
            - 自动写表头（若表为空）

        :param mapping: 要写入的微信号→学号 映射
        :type mapping: dict[str, str]
        :param sheet_name: 工作表名
        :type sheet_name: str
        :return: None
        """
        try:
            wb = openpyxl.load_workbook(self.excel_path)
            sheet = wb[sheet_name] if sheet_name in wb.sheetnames else wb.create_sheet(sheet_name)
        except FileNotFoundError:
            wb = Workbook()
            sheet = wb.active
            sheet.title = sheet_name

        # 如果空表 → 写表头
        if sheet.max_row == 1 and not sheet["A1"].value:
            sheet["A1"] = "微信号"
            sheet["B1"] = "学号"

        # 先把 Excel 里的数据读出来，构成 existing_dict
        existing_dict = {}
        for row in sheet.iter_rows(min_row=2, values_only=True):
            wx_id, student_id = row[0], row[1]
            if wx_id and student_id:
                existing_dict[str(wx_id).strip()] = str(student_id).strip()

        # 合并（mapping 更新 existing_dict）
        for wx_id in mapping:
            new_value = mapping.get(wx_id)
            if new_value:
                existing_dict[str(wx_id).strip()] = str(new_value).strip()

        # 排序（按学号升序）
        sorted_items = sorted(
            existing_dict.items(),
            key=lambda x: int(x[1]) if x[1].isdigit() else x[1]
        )

        # 清空并重写
        sheet.delete_rows(2, sheet.max_row)

        row_index = 2
        for wx_id, student_id in sorted_items:
            sheet[f"A{row_index}"] = wx_id
            sheet[f"B{row_index}"] = student_id
            row_index += 1

        wb.save(self.excel_path)
