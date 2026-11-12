# `checkinstats.py` 模块文档

## 1. 模块定位

`checkinstats.py` 是一个 **专门操作“打卡统计 Excel 文件”** 的小工具模块，主要做两件事：  

1. 用 Excel（通过 `xlwings`）重新计算所有公式  
2. 把计算后的结果读出来，变成 Python 字典 `{学号: 打卡次数}`

它通常被 **数学 / 语文打卡统计逻辑** 调用，用作“权威累计数据来源”。

---

## 2. 核心类：`CheckInStats`

### 2.1 初始化

```python
from checkinstats import CheckInStats

stats = CheckInStats(excel_path="数学群_累计.xlsx")
```

- **参数**：  
  - `excel_path`: Excel 文件完整路径
- 初始化时会 **立刻调用** `recalc_and_save_excel()`，确保表中的各种公式先被 Excel 计算好。

---

### 2.2 `recalc_and_save_excel()`

- 使用 `xlwings` 打开一个隐藏的 Excel 实例：
  - `App(visible=False)` → 不弹出 Excel 窗口
- 打开指定工作簿：`app.books.open(filepath)`
- 调用 `wb.app.calculate()`：强制 Excel 重新计算所有公式
- 保存并关闭工作簿，退出 Excel 进程

**使用场景**：  
在你想 “读取一个含大量公式的 Excel 的计算结果” 时，先来一遍这个函数，避免读到的还是旧值。

---

### 2.3 `load_stats()`

- 假设 Excel 的结构为：

| 行号 | A列     | B列       | 其他列… |
|------|---------|-----------|---------|
| 1    | 表头    | 表头      | …       |
| 2    | 表头    | 表头      | …       |
| 3    | 学号    | 打卡次数  | …       |
| 4    | 学号    | 打卡次数  | …       |
| ...  | ...     | ...       | ...     |

- 函数逻辑：
  1. 用 `openpyxl.load_workbook(excel_path, data_only=True)` 打开 Excel
  2. 只看活动工作表 `wb.active`
  3. 从 **第 3 行** 开始遍历
  4. 若 A、B 都有值，则：
     - 把 A 列的学号转成 `int`
     - 把 B 列的次数转成 `int`
  5. 组织成 `{student_id: times}` 这样一个字典返回

**异常处理**：  
- 如果文件不存在，会直接返回空字典 `{}`，而不是抛异常。

---

## 3. 典型用法示例

```python
from checkinstats import CheckInStats

stats = CheckInStats("数学群_累计.xlsx")

data = stats.load_stats()
# data 形如：{29101: 18, 29102: 21, ...}

pass_count = sum(1 for v in data.values() if v >= 21)
print(f"已达标人数：{pass_count}")
```

---

## 4. 和其它模块的关系

- 被 `export.py` 里的 **数学群统计逻辑** 用来获取最新累计次数
- 与 `update.py` 形成闭环：
  - `update.py` 负责生成新的累计 Excel
  - `checkinstats.py` 负责从累计 Excel 中读出数据

**一句话总结**：  
> `checkinstats.py` 专注于：**“让 Excel 把公式算一遍，然后把结果读出来”**。  
