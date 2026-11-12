# `wxname2studentid.py` 模块文档

## 1. 模块定位

`wxname2studentid.py` 用来维护一份 **“微信群昵称（或微信号） ↔ 学号”映射表**，以 Excel 形式长期保存。

在整个系统中的作用：

- 当家长在群里发作业时，机器人可以：
  - 根据 **发送者昵称 / 微信号** 找到对应的 **学生学号**  
  - 将打卡记录和下载的文件，准确归档到该学号名下  
- 映射关系是可增量维护的：  
  - 新出现的微信号，可以在代码中识别后写回 Excel  
  - 下次运行时就能自动识别。

---

## 2. Excel 文件格式

默认 Excel 文件名：`mapping.xlsx`。

默认 Sheet 名：`Sheet1`（可在调用时调整）。

表格列结构：

| 列 | 含义   |
|----|--------|
| A  | 微信号 |
| B  | 学号   |

文件不存在时，写入时会自动创建，并写上表头。

---

## 3. 核心类：`Wxname2StudentID`

### 3.1 初始化

```python
from wxname2studentid import Wxname2StudentID

mapper = Wxname2StudentID(directory="./config")
```

参数说明：

- `directory`：映射 Excel 所在目录，默认是当前工作目录 `os.getcwd()`  
- `file`：文件名，默认 `mapping.xlsx`  

内部会将：

```python
self.excel_path = os.path.join(directory, MAPPING_EXCEL_NAME)
```

---

## 4. 读取映射：`load_mapping(sheet_name="Sheet1") -> dict`

**功能**：从 Excel 中读取映射表，返回一个 `dict`：

```python
{
    "家长A微信号": "29101",
    "家长B微信号": "29102",
    ...
}
```

逻辑：

1. 尝试 `openpyxl.load_workbook(self.excel_path)`
   - 若文件不存在 → 返回 `{}`，不报错  
2. 检查 `sheet_name` 是否存在：
   - 若不存在 → 返回 `{}`  
3. 遍历工作表中从第 2 行开始的每一行：
   - 若微信号和学号都有值，则：
     - 转为 `str` 并 `strip()` 去空白
     - 存入字典 `mapping[wx_id] = student_id`  
4. 返回 `mapping`。

**典型用法**：

```python
mapper = Wxname2StudentID()
mapping = mapper.load_mapping()

student_id = mapping.get(msg.sender, None)
```

---

## 5. 写入映射：`save_mapping(mapping, sheet_name="Sheet1")`

**功能**：将一批新的映射信息写入 Excel，并与原有记录合并。

### 5.1 打开 / 创建工作簿

- 若 Excel 存在：
  - 打开它，若 `sheet_name` 不存在就新建一个工作表  
- 若 Excel 不存在：
  - 创建一个新工作簿 `Workbook()`，使用默认 `active` sheet，并改名为 `sheet_name`  

### 5.2 写表头（若是空表）

- 如果表当前只有第 1 行且 A1 为 `None`：
  - 写入：
    - `A1 = "微信号"`
    - `B1 = "学号"`

### 5.3 读取旧数据

- 从第 2 行开始，遍历原有数据：
  - 构建 `existing_dict[wx_id] = student_id`

### 5.4 合并新数据

- 对传入的 `mapping` 逐条更新：

```python
for wx_id, new_value in mapping.items():
    if new_value:
        existing_dict[str(wx_id).strip()] = str(new_value).strip()
```

- 相同微信号会被“覆盖为最新学号”。

### 5.5 排序并重写

- 以“学号升序”排序：

```python
sorted_items = sorted(
    existing_dict.items(),
    key=lambda x: int(x[1]) if x[1].isdigit() else x[1]
)
```

- 清空原数据行（保留表头）后，从第 2 行起写入：

| 行号 | A列     | B列     |
|------|---------|---------|
| 2    | 微信号1 | 学号1   |
| 3    | 微信号2 | 学号2   |
| ...  | ...     | ...     |

### 5.6 保存

- 最后 `wb.save(self.excel_path)`。

---

## 6. 使用示例

```python
from wxname2studentid import Wxname2StudentID

mapper = Wxname2StudentID(directory="./config")

# 读取现有映射
mapping = mapper.load_mapping()

# 新增或更新某些微信号
mapping_update = {
    "wxid_parent_A": "29101",
    "wxid_parent_B": "29102",
}

mapper.save_mapping(mapping_update)
```

---

## 7. 总结

`wxname2studentid.py` 虽然代码不多，但在整个系统中地位非常关键：

> 它是“微信群世界”和“学号世界”的桥梁，让所有打卡记录、下载的作业、统计表中的行号，都能最终对齐到一个 **稳定的学生编号** 上。  
