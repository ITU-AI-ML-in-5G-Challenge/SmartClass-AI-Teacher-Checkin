# `daemon.py` 模块文档

## 1. 模块定位

`daemon.py` 是整个项目里最“重量级”的一个模块，它实现了：

- **自动微信作业点评机器人**（AutoReviewBot）
- 长期运行的守护进程：
  - 监听微信群消息
  - 下载学生作业图片
  - 调用 AI 模型自动生成点评（星火 + 通义千问）
  - 将点评发回微信群
  - 同时还和打卡统计模块整合（调用 `checkin.py` 的每日任务）

可以理解为：**“一边盯着群里的作业图片，一边自动当老师点评”**。

---

## 2. 依赖关系

- `wxauto`：操控 Windows 版微信
- `sparkreview.py`：星火图像点评
- `qwenvlreview.py`：通义千问图像点评
- `safewechat.py`：安全封装微信窗口、发送消息 & 队列
- `message.py`：消息过滤与增量比较（只处理新消息）
- `checkin.py`：每日 02:00 触发打卡统计任务
- `logger.py`：彩色 logger

---

## 3. 核心类：`AutoReviewBot`

### 3.1 初始化参数

典型创建方式（大致伪代码）：

```python
from wxauto import WeChat
from sparkreview import SparkImageReviewer
from qwenvlreview import QwenVLReviewer
from daemon import AutoReviewBot
from logger import setup_logger

wx = WeChat()
spark = SparkImageReviewer()
qwen = QwenVLReviewer()
logger = setup_logger("autoreview_bot.log")

bot = AutoReviewBot(
    wx=wx,
    reviewer1=spark,
    reviewer2=qwen,
    group_name="英语群",
    img_dir="./WeChatDownload/review_images",
    logger=logger,
)
```

关键参数：

- `wx`：wxauto 的 `WeChat` 实例
- `reviewer1`：星火图片点评模型
- `reviewer2`：通义千问图片点评模型
- `group_name`：要监听的微信群名称
- `img_dir`：下载图片存放目录
- `logger`：日志对象
- `poll_interval`：轮询间隔秒数
- `heartbeat_interval`：心跳打印频率
- `max_image_size`：最大允许图片字节数（保护模型和带宽）

---

### 3.2 `run()` —— 主循环

`run()` 是真正干活的地方，内部逻辑大致如下：

1. 通过 `safe_get_wechat_window()` 定位微信窗口，置顶
2. 打开目标群聊窗口
3. 初始化一个 `MessageComparer` / `MessageDiffFast`，用于 **只找新消息**
4. 进入无限循环：
   - 使用 `wx.GetAllMessage()` 获取最新消息列表
   - 通过 `diff_and_merge()` 找出“相对于旧列表的新消息”
   - 对新消息调用 `_process_new_messages(new_msgs, msg_q)`
   - 定期调用 `safe_clean_msg_queue()` 清理夜间积压的发送队列
   - 定期打印心跳日志（证明机器人还活着）
   - 若检测到微信异常（掉线、窗口关闭） → 尝试恢复
5. 支持键盘退出：ESC / Ctrl+C 等

---

### 3.3 `_process_new_messages(new_msgs, msg_q)`

处理一批“新增消息”，主要逻辑：

- 忽略自己发的消息（`msg.attr == "self"`）  
- 如果是 **图片消息**：
  - 把图片消息放到 `msg_q[sender]` 缓存里
  - 等待后续是否有 @ 触发点评
- 如果是 **文本消息** 且包含 @ 机器人：
  - 触发 `_handle_mention(m, msg_q)` 进行点评
- （可扩展其他规则，例如关键字触发等）

---

### 3.4 `safe_download(m, max_retries=3)`

用于“超鲁棒”地下载一张图片消息：

- 多级重试策略：
  - 小重试：应对“消息刚到、图片缓存还没写完”
  - 滚动预加载：通过滚动聊天窗口促使微信加载图片
- 文件完整性检查：
  - 路径存在
  - 文件大小 > 0
- 返回：成功时返回图片路径；失败时返回 `None`。

---

### 3.5 `_handle_mention(m, msg_q)`

当某条消息中 @ 到机器人时，触发点评流程：

1. 从 `msg_q` 中找到该发送者最近的一张图片消息  
2. 调用 `safe_download()` 下载图片  
3. 若图片过大或下载失败 → 给出友好提示文本  
4. 调用星火点评 + 通义千问点评：
   - `reviewer1.review(image_path)`
   - `reviewer2.review(image_path)`
5. 将点评文本通过 `safe_send_msg_queue()` 或 `safe_send_msg_immediately()` 发回微信群  
6. 删除本地临时图片、清理缓存队列  

---

## 4. `main()` 入口

`daemon.py` 的 `main()` 一般做这些事情：

1. 初始化 logger  
2. 读取环境变量（群名、图片保存目录等）  
3. 初始化 `WeChat` 实例  
4. 创建 `SparkImageReviewer` / `QwenVLReviewer`  
5. 初始化 `AutoReviewBot`  
6. 调用 `bot.run()`，开始守护进程循环  

通过命令行直接启动：

```bash
python daemon.py
```

---

## 5. 总体设计思路

- **高容错**：几乎所有关键步骤都包了 `try/except`，即使某一条消息或一次模型调用失败，也不会把机器人整体干掉。
- **与“每日打卡统计”集成**：守护进程中可以顺便判断当前时间，若到了某个时间点，可以调用 `checkin.run_export_for_date()` + `run_update_for_date()`。
- **分层明确**：
  - `daemon.py`：控制流程、业务逻辑
  - `safewechat.py`：所有和微信窗口、发送消息有关的低层细节
  - `sparkreview.py` / `qwenvlreview.py`：只关心“给一张图片，返回一段点评文本”

**一句话总结**：  
> `daemon.py` 就是整个“自动点评机器人”的大脑和心脏，负责“盯群 + 理消息 + 叫 AI 来写评语”。
