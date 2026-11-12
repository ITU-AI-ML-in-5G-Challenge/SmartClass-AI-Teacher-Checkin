# `message.py` 模块文档

## 1. 模块定位

`message.py` 是 **与微信消息结构打交道的工具集**，主要负责：

- 判断一条消息属于哪种类型（文本 / 图片 / 文件 / 时间 / 笔记）  
- 根据“时间消息”判断该消息是属于哪一天  
- 过滤掉早于目标日期的旧消息、系统消息、自发消息  
- 对消息列表做 **增量 diff**：找出“相对于旧列表新增了哪些消息”

整体目标是：**在机器人主循环中，快速、安全地识别“新的、需要处理的消息”。**

---

## 2. 消息类型判断函数

这些函数的输入通常是 `wxauto` 的消息对象。

- `is_text_message(msg)`：`msg.type` 为 `"text"` 或 `"quote"`  
- `is_pic_message(msg)`：`msg.type == "image"`  
- `is_time_message(msg)`：`msg.message_type_name == "TimeMessage"`  
- `is_file_message(msg)`：`msg.type == "file"`  
- `is_memo_message(msg)`：`msg.type == "other"` 且内容包含 `"笔记"`  

它们是其他模块（如 `download.py`、`daemon.py`）处理消息时的基础判断。

---

## 3. 时间解析与过滤

### 3.1 `_is_target_date(date_string, target_date)`

内部函数，用于将微信中的时间字符串转为“**与 target_date 相差几天**”：

支持多种格式：

1. `"03:30 PM"`（12 小时制）  
2. `"2025-10-13 15:30:00.123"`（带毫秒）  
3. `"2025-10-13 15:30:00"`  
4. `"15:30"`（只有时间，则默认为 **今天**）  
5. `"2025-10-13"`（只有日期）  

返回值：一个整数：

- 正数：该时间在 `target_date` 之后  
- 0：同一天  
- 负数：早于 `target_date`  

### 3.2 `compare_message_date(msg, target_date)`

- 输入一条时间消息 `msg`（`is_time_message(msg) == True`）  
- 调用 `_is_target_date(msg.time, target_date)`  
- 返回同样的天数差。

### 3.3 `filter_messages(msg_list, target_date=datetime.today())`

用途：**从一大串历史消息中，剪掉过早的消息，只保留“目标日期及之后”的部分**。

逻辑：

1. 从头扫描 `msg_list`，寻找第一条“时间消息”使得 `days >= 0`：  
   - 在这之前的消息全部跳过  
2. 对剩下的消息做二次过滤：  
   - `m.sender` 存在且不为 `"system"` / `"self"`  
3. 返回新的列表

注意事项：

- 同一个 `msg_list` 不要重复调用 `filter_messages`，否则有可能剪成空列表。

---

## 4. 哈希与 diff 工具

### 4.1 `MessageHasher`

- 核心方法：`get_hash(msg)`  
- 生成基于以下字段的 SHA256 哈希：

```python
key = f"{msg.sender}|{msg.content}|{msg.attr}|{msg.type}"
```

- 会把结果缓存在 `msg._hash_cache` 中，避免重复计算。

### 4.2 `MessageComparer` —— 普通版 diff

- 对两份消息列表 `old_list` / `new_list` 做 **尾部匹配**：

步骤：

1. 找出 `old_list` 的“最长尾部”在 `new_list` 中出现的位置  
2. 匹配要求：  
   - 若 `old_list` 长度 ≥ 3 → 至少匹配 3 条  
   - 长度为 2 → 至少匹配 2 条  
   - 长度为 1 → 匹配 1 条  
3. 若找到匹配：
   - 匹配尾部之后的部分视为 “新增消息”  
4. 若找不到匹配：  
   - 整个 `new_list` 视为新增  

返回 `(new_items, merged)`：

- `new_items`：新增的消息列表  
- `merged`：`old_list + new_items` 合并后的完整列表  

### 4.3 `MessageDiffFast` —— 高性能 diff

- 使用滚动哈希（Rabin-Karp 风格）优化匹配过程：

流程：

1. 使用 `MessageHasher` 得到每个消息的 SHA256  
2. 取每个 SHA256 的前 64bit，转成整数  
3. 对 `old_list` 的所有可能“尾部长度”进行 rolling hash 匹配  
4. 同样使用“最小匹配长度 = min(3, len(old_list))”规则  
5. 匹配成功则返回新增部分，否则视为全部新增

优点：

- 时间复杂度为 O(n)  
- 适合消息量比较大的群聊场景

---

## 5. 典型使用方式

在长轮询机器人中，通常这样使用：

```python
from message import MessageDiffFast

diff = MessageDiffFast()
old_list = []

while True:
    all_msgs = wx.GetAllMessage() or []
    new_items, old_list = diff.diff_and_merge(old_list, all_msgs)

    # 只处理 new_items
    for m in new_items:
        ...
```

再配合 `filter_messages(...)`，可以确保：

- 机器人不会重复处理历史消息
- 只对真正新增的消息触发逻辑

---

## 6. 总结

`message.py` 是一个非常关键但“看起来不显山露水”的模块：

> 它专注于回答两个问题：  
> 1. “这条消息是什么类型？”  
> 2. “这条消息相对于我上一次看到的，是不是新的？”  

所有需要稳定监听微信群消息的模块（如 `daemon.py` / `download.py` / `export.py`）都会把它当作基础设施来使用。  
