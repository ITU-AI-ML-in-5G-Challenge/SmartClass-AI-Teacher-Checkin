# `update.py` 模块文档

## 1. 模块定位

`update.py` 负责 **把每日 CSV 打卡记录“合并”到 Excel 总表中**，是整个打卡统计链路的“后半段”：

- `export.py` 负责：从微信群导出打卡记录 → 生成每日 CSV  
- `update.py` 负责：读取每日 CSV + 昨日 Excel → 生成新的 Excel（含累计统计）  

可以理解为：**从“每天一张 CSV”变成“长期维护的一张总统计表”**。

---

## 2. 配置依赖

模块顶部使用：

```python
group_names = get_env_set("CHECKIN_GROUP_NAMES")
checkin_dir = get_env("CHECKIN_DIR")
```

需要在 `.env` 中配置：

- `CHECKIN_GROUP_NAMES`：要处理的群名集合（例如：语文群,英语群,数学群）  
- `CHECKIN_DIR`：打卡工作目录，用来存放 CSV 和 Excel  

---

## 3. 基类 `Updater`

### 3.1 初始化

```python
u = Updater(csv_file, old_excel_file, new_excel_file, logger)
```

会做：

1. 记录 `csv_file` / `old_excel_file` / `new_excel_file`  
2. 用 `get_logger` 统一 logger  
3. 检查 `csv_file` 与 `old_excel_file` 是否存在：
   - 缺哪个就打印错误列表并抛异常 `"文件不存在"`  

### 3.2 `recalc_and_save_excel()`

- 使用 `xlwings` 打开旧 Excel：

```python
app = xw.App(visible=False)
wb = app.books.open(filepath)
wb.app.calculate()
wb.save()
wb.close()
app.quit()
```

目的是：确保 Excel 中的公式被刷新。

### 3.3 `ask_overwrite()`

- 若 `new_excel_file` 已存在，则在终端询问：

```text
⚠️ 文件 xxx 已存在，是否覆盖？(y/n)：
```

- 返回 True / False，交给上层判断是否继续。

### 3.4 `load_csv_safely(columns)`

一个非常实用的 CSV 读取函数：

- 编码兼容：依次尝试 `"utf-8-sig"`, `"utf-8"`, `"gbk"`  
- 列名标准化：
  - 去 BOM
  - 全角空格 → 半角
  - 去除所有空白字符（包括 Tab）  
- 使用模糊匹配来处理“列名略有不同”的情况：
  - 例如：`当天数学打卡` 与 `当天数学打卡数`  
- 若最终仍然有期望列缺失，会抛 `KeyError`，并打印“缺少哪些列 + 实际列名”。

返回一个列名已重命名为“标准名”的 `DataFrame`。

---

## 4. `EnglishUpdater` —— 英语 Excel 更新器

### 4.1 初始化

```python
u = EnglishUpdater(
    name="英语群",
    csv_file="英语群_2025-10-13.csv",
    old_excel_file="英语群_1012.xlsx",
    new_excel_file="英语群_1013.xlsx",
    logger=logger,
)
```

内部设置：

```python
self.csv_file_titles = [
    "学号",
    "当天单词词组默写次数",
    "当天课文背诵次数",
    "当天语法知识点学习次数",
]
```

### 4.2 `load_csv_safely()`

- 只是一层封装：`return super().load_csv_safely(self.csv_file_titles)`  
- 确保 CSV 至少有：学号 + 三类打卡次数。

### 4.3 `update_excel_with_target_data()`

**主要逻辑：**

1. 用 `load_csv_safely()` 读取当天 CSV → `df_csv`  
2. 用 `openpyxl.load_workbook(self.old_excel_file, data_only=True)` 打开“值视图”（计算后的数值） → `wb_vals`  
3. 用 `openpyxl.load_workbook(self.old_excel_file)` 打开可写表 → `wb`  
4. 从 `wb_vals.active` 中读取：  
   - 第 1 列是学号  
   - 第 2~5 列是昨日累积的：三类次数 + 是否有图片/文件（示意）  
5. 用 `df_csv` 构造 `target_date_data`：
   - key：学号（int）  
   - value：当天三类次数（B/C/D 列）  
6. 遍历 `sheet` 的所有学生行：
   - 若该学号在 `yesterday_data` 中：
     - 把昨日 B/C/D/E 写到 `I/J/K/L` 列（即“把昨天的统计平移到右侧列”）
   - 若该学号在 `target_date_data` 中：
     - 把今天 B/C/D 写到 `F/G/H` 列  
7. 保存为新 Excel 文件 `self.new_excel_file`，并返回路径。

---

## 5. `MathsUpdater` —— 数学 / 语文 Excel 更新器

### 5.1 初始化

```python
u = MathsUpdater(
    name="数学群",  # 或“语文群”
    csv_file="数学群_2025-10-13.csv",
    old_excel_file="数学群_1012.xlsx",
    new_excel_file="数学群_1013.xlsx",
    logger=logger,
)
```

`self.csv_file_titles` 设置为：

```python
["学号", f"当天{self.name[0:2]}打卡"]
```

例如：数学群 → `"当天数学打卡"`；语文群 → `"当天语文打卡"`。

### 5.2 `update_excel_with_target_data()`

逻辑简化版：

- BC / D 列结构：

| 列 | 含义             |
|----|------------------|
| B  | 昨日累计打卡次数 |
| C  | 今日打卡次数     |
| D  | 昨日数据平移栏   |

实现步骤：

1. 从 `wb_vals` 中读出昨天的 B 列 → 写到新表的 D 列  
2. 从 CSV 中读出今天的打卡（0/1 或次数） → 写到 C 列  
3. 保存新的 Excel 文件。

---

## 6. `UpdaterFactory`

工厂类，根据群名与日期自动构造对应的 Updater：

```python
factory = UpdaterFactory()
u = factory.create_updater("英语群", target_date, logger)
```

会自动生成：

- `csv_file`：`{CHECKIN_DIR}/{群名}_{yyyy-mm-dd}.csv`  
- `old_excel_file`：`{CHECKIN_DIR}/{群名}_{昨天mmdd}.xlsx`  
- `new_excel_file`：`{CHECKIN_DIR}/{群名}_{今天mmdd}.xlsx`  

并：

- 打印这些路径，方便你检查是否对得上日期。  
- 按群名选择 `EnglishUpdater` / `MathsUpdater`。

---

## 7. `main()` 命令行入口

流程：

1. 读取 `CHECKIN_GROUP_NAMES` / `CHECKIN_DIR`  
2. 确保目录存在（`os.makedirs(checkin_dir, exist_ok=True)`）  
3. 让用户输入目标日期（支持 `yyyy-mm-dd` / `yyyy/mm/dd` / `yyyymmdd`）  
   - 解析失败则自动使用“昨天”为目标日期  
4. 遍历所有 `group_names`：
   - 询问是否处理该群（默认 y）  
   - 通过 `UpdaterFactory` 创建对应 Updater  
   - `ask_overwrite()` 决定是否覆盖旧 Excel  
   - 调用：
     - `recalc_and_save_excel()`  
     - `update_excel_with_target_data()`  
   - 打印生成成功的 Excel 文件路径  

---

## 8. 总结

`update.py` 的责任可以概括为：

> **把每天按群生成的 CSV，逐步累积到按天命名的 Excel 文件中，形成一个可长期查看的统计台账。**  

和 `export.py`、`checkinstats.py`、`checkin.py` 一起，构成了打卡系统的统计后台。  
