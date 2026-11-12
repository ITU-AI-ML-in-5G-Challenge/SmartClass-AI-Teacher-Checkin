# `safewechat.py` 模块文档

## 1. 模块定位

`safewechat.py` 是所有 **“和微信客户端打交道”的底层封装**，目标是：

> 在各种奇怪情况（微信没打开、窗口被挡、深夜发消息风控等）下，程序不至于直接崩掉，而是“优雅地失败”。

它主要负责：

- 查找并激活微信主窗口  
- 安全地滚动聊天窗口（加载历史消息）  
- 打开指定群聊  
- 安全地获取所有消息  
- 实现 **“深夜消息排队、白天再发”** 的安全队列发送机制  

---

## 2. 微信窗口相关方法

### 2.1 `safe_get_my_group_nickname(wx, logger=None)`

- 通过 `wxauto.WeChat.GetMyInfo()` 获取自己的信息
- 尝试返回自己的昵称（用于后续排除自己发的消息）
- 失败时记录错误日志并返回 `None`

### 2.2 `get_wechat_main_window()` / `enum_windows(...)`

- 使用 `win32gui.EnumWindows` 枚举所有窗口
- 只挑出标题为“微信”的窗口，并按样式层级排序
- 返回“层级最高”的那个窗口句柄，认为是主窗口
- 若找不到，抛异常 `"未找到微信主窗口"`

### 2.3 `bring_wechat_to_front(hwnd)`

- 使用 `ShowWindow` + `SetForegroundWindow` 把微信窗口激活到前台

### 2.4 `safe_get_wechat_window(logger=None)`

- 包装后的安全版本：
  - 内部调用 `get_wechat_main_window()`
  - 找到则记录 “找到微信窗口句柄：...”
  - 未找到或异常 → 记录错误，并返回 `None`

### 2.5 `safe_bring_wechat_to_front(hwnd, logger=None)`

- 对 `bring_wechat_to_front` 再套一层 `try/except`，防止异常导致程序退出。

---

## 3. 滚动加载历史消息

### 3.1 `_safe_scroll(pixels, desc="滚动", logger=None)`

- 调用 `pyautogui.scroll(pixels)` 实际执行滚动
- 捕获异常并输出 warning 日志

### 3.2 `safe_scroll(scroll_times=SCROLL_TIMES, logger=None)`

- 默认向上滚动 `SCROLL_TIMES` 次，每次 `SCROLL_PIXEL` 像素，中间 sleep `SCROLL_INTERVAL` 秒。
- 用于：
  - 切换到某个群之后，主动向上翻多页，把历史消息都加载进来，再执行 `GetAllMessage()`。

---

## 4. 打开群聊 & 获取消息

### 4.1 `safe_open_chat(wx, groupname, logger=None)`

- 调用 `wx.ChatWith(groupname)` 打开某个群聊
- 成功则返回 True，失败则记录错误并返回 False

### 4.2 `safe_get_all_messages(wx, logger=None)`

- 调用 `wx.GetAllMessage()` 获取当前聊天窗口的所有消息
- 若返回 `None`，则转换为 `[]` 并给出 warning 日志
- 若抛异常，则记录错误并返回 `[]`

这两个方法是所有上层模块（`download.py`, `daemon.py`, `export.py`）获取消息的入口。

---

## 5. 夜间发送安全队列

### 5.1 全局队列：`SEND_QUEUE = deque()`

- 用来缓存“深夜不宜发送”的消息（避免触发风控）。  

队列元素结构：`(msg, receiver)`。

### 5.2 `safe_clean_msg_queue(...)`

- 策略：
  - 若当前小时在 `[night_start, night_end)` 区间内（默认 0~6 点）：  
    - 直接记录日志：`"深夜 X 点，消息队列不处理"`，返回  
  - 若不在深夜：
    - 取出队列中的所有 `(old_msg, old_recv)`
    - 逐条调用 `_real_send(...)` 发送  

- 参数说明：
  - `max_retry`：每条消息最多重试几次  
  - `min_delay` / `max_delay`：发送前随机延迟区间  
  - `night_start` / `night_end`：深夜时间段定义  

### 5.3 `safe_send_msg_queue(wx, msg, receiver, ...)`

- **推荐使用的发送函数**：具备“夜间排队、白天自动清理”的行为。

逻辑：

1. 若当前时间在 `[night_start, night_end)`：  
   - 只做一件事：`SEND_QUEUE.append((msg, receiver))`  
   - 记录日志：“深夜 X 点，消息已加入队列，不立即发送”  
2. 若不在深夜：  
   - 先调用 `safe_clean_msg_queue(...)` 把队列里旧消息发掉  
   - 再调用 `_real_send(wx, msg, receiver, ...)` 发送当前消息  

### 5.4 `safe_send_msg_immediately(wx, msg, receiver, ...)`

- 不考虑时间段，**立刻发送**，不走队列：
  - 直接调用 `_real_send`  
- 适合：
  - 手动触发的命令回复
  - 与微信风控无关的内部通知

### 5.5 `_real_send(wx, msg, receiver, max_retry, min_delay, max_delay, logger)`

- 发送逻辑：
  1. 先随机 sleep 一段时间（`[min_delay, max_delay]`）
  2. 最多尝试 `max_retry` 次：
     - 调用 `wx.SendMsg(msg, receiver)`
     - 成功则记录“发送成功”，返回 True
     - 失败则记录“发送失败：...”，并随机等待 2~5 秒然后再试
  3. 若所有尝试都失败，记录“最终失败，放弃发送”，返回 False

这种 **“延迟 + 多次重试 + 日志记录”** 的模式，能最大程度地减小被微信风控的风险。

---

## 6. 总结

`safewechat.py` 将所有容易出问题的地方（窗口句柄、滚动、夜间发送、异常）都包装成 **“即使失败也不会拖垮主流程”** 的函数，是整个系统的一个重要“减震器”：

> 它的目标不是“功能复杂”，而是 **“凡是跟微信交互这种容易出错的地方，都给你兜个底”**。  
