# `export.py` 模块文档

## 1. 模块定位

`export.py` 是 **“微信群文字打卡导出与统计”** 的核心模块，负责：

- 从微信群读取打卡相关消息（文字 / 图片 / 文件 ）
- 把这些消息整理成结构化记录
- 生成：
  - 文本统计（txt）
  - 机器可读的统计表（csv）
- 为不同学科（英语 / 数学 / 语文）提供不同的统计规则

可以理解为：**“把一堆聊天记录，变成一张清楚的打卡统计表”**。

---

## 2. 主要结构

- **State / StateMachine**：用于描述“一个学生的打卡进度状态”
- **Group（基类）**：对某个微信群的通用导出逻辑
- **EnglishGroup**：英语群的专用导出逻辑（三类打卡）
- **MathsGroup**：数学/语文群共用逻辑
- **GroupFactory**：根据群名创建对应的 Group 子类
- **main()**：命令行入口，交互式导出多个群的打卡数据

---

## 3. 状态机相关

### 3.1 `State` 枚举

典型状态：

- `Start`：尚未开始打卡
- `ImageProcceeded`：收到了图片（作业照片），还没文本
- `TextProceeded`：收到了文本说明
- `FileProceeded`：收到了文件（PDF/Word等）
- `Success`：认定为“完成打卡”
- `Error`：异常状态（多次错乱等）

### 3.2 `StateMachine`

用于跟踪单个学生在当日的打卡流程：

- `transition(input)`：根据收到的消息类型（image/text/file）转状态
- `is_succeeded()`：判断是否完成一次有效打卡
- `is_almost_succeeded()`：用于识别“只发了图片，还没发文本”的半完成状态
- `reset()`：在一次打卡完成或异常后重置状态

**这套状态机在英语群中尤其重要，因为需要识别三类不同打卡内容。**

---

## 4. 基类 `Group`

### 4.1 初始化

```python
g = Group(name="英语群", msgs=msg_list, target_date=datetime.today(), logger=logger)
```

- `name`：群名
- `msgs`：原始微信消息列表
- `target_date`：仅处理这一天的打卡
- `logger`：日志输出

### 4.2 `export_group(mapping)`

这是 Group 中**最重要的函数**之一：

1. 遍历消息列表，筛出某天的消息（配合 `message.filter_messages`）
2. 根据发送者昵称 / 备注内容，识别学生学号：
   - 优先使用 `mapping.xlsx`
   - 若昵称中就包含学号或消息文本中带学号，会更新映射
3. 处理各种消息类型（图 / 文 / 文件），组合成“打卡记录文本列表 `text_msgs`”
4. 返回这个 `text_msgs`，交给具体学科子类做进一步统计

### 4.3 抽象方法

- `save_text_csv(text_msgs)`：由子类实现，负责：
  - 写 txt
  - 写 csv
  - 返回统计 summary 文本和 csv 文件路径
- `classify_check_in_from_string(input_string)`：由子类实现，用于：
  - 根据一条文本消息判断该学生“本次打卡属于哪一类 / 算几次”

---

## 5. `EnglishGroup` —— 英语打卡专用

英语群需要统计三类打卡：

1. 单词词组默写  
2. 课文背诵  
3. 语法知识点学习  

### 5.1 核心逻辑

- 使用 `StateMachine` 和 `shadow_records` 处理复杂的打卡顺序：
  - 有时家长会先发“今天背了 U3 的单词”，然后再发图片
  - 有时会先发图片再发说明
- `update_student_records(...)`：负责更新三类打卡次数（取当日最大值，避免一条消息重复计数）
- `save_text_csv(text_msgs)`：
  - 扫描所有记录，为每个学号统计当日三类打卡次数
  - 生成按学号排序的 csv：

示意结构：

```text
学号, 当天单词词组默写次数, 当天课文背诵次数, 当天语法知识点学习次数, 是否有图片/文件
```

---

## 6. `MathsGroup` —— 数学 / 语文共用

特点：

- 对数学、语文打卡，一般只关心“是否打卡”、“累计打卡次数”
- 规则相对简单：
  - 某条记录只要满足条件（有图片或文件，或文本中包含某些关键词），就算该生当日“打卡 1 次”
- `save_text_csv(text_msgs)`：
  - 结合 `checkinstats.py` 读到的 Excel 累计次数
  - 将今天是否打卡叠加上去
  - 生成新的每日 csv，用于后续 Excel 更新

---

## 7. `GroupFactory`

根据群名决定创建哪种 Group：

- `"英语群"` → `EnglishGroup`
- `"数学群" / "语文群" / 测试群` → `MathsGroup`

调用方式：

```python
factory = GroupFactory()
group = factory.create_group(groupname, msgs, target_date, logger)
text_msgs = group.export_group(mapping)
summary, csv_path = group.save_text_csv(text_msgs)
```

---

## 8. `main()` 命令行入口

大致流程：

1. 读取 `.env`：群名、工作目录
2. 询问用户要处理哪一天的打卡
3. 初始化 `WeChat`，逐个群进行：
   - 打开群聊
   - 加载消息 & 滚动历史
   - 调用 `GroupFactory` 处理
4. 生成 txt / csv 文件
5. 可选：将统计结果发回群里

---

## 9. 总结

**`export.py` 的定位可以用一句话概括：**

> 把微信群里的“原始聊天记录”，变成“有学号、有分类、可统计”的打卡记录表。  

配合 `download.py`（下载作业图片）、`update.py`（更新 Excel）、`checkin.py`（定时任务），就构成了完整的一套 **自动打卡统计流水线**。  
