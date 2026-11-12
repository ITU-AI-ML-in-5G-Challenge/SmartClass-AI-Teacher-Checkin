# 学校AI落地案例：智能班级打卡与作业点评系统（SmartClass AI Tracker & Reviewer）

本项目是一套面向企业即学校 **班主任 / 任课老师** 的 Windows 本地工具，用来做三件事：

1. **微信群打卡统计自动化**  
   - 自动从微信 PC 版群聊中读取打卡消息（文字 / 图片 / 文件）。  
   - 识别学生学号，判断是否完成打卡。  
   - 生成每日 CSV / Excel 统计表。  

2. **作业图片自动点评（AI 老师）**  
   - 家长在群里发作业照片并 @ 机器人。  
   - 机器人自动下载图片，调用星火图像模型 + 通义千问多模态模型。  
   - 返回“初中英语女老师风格”的点评文字。 

3. **定时守护进程**  
   - 每天固定时间自动跑一遍“导出 + 更新 Excel”。  
   - 可长期在一台电脑上稳定运行。  

---

## 0. 参加决赛评审说明文档（参考review.md说明）

| 文件名                              | 简介                       |
|------------------------------------|--------------------------|
| `PRESENTATION.mp4`                 | 演讲视频mp4文件             |
| `SmartClass AI：教师工作自动化革命`  | 演讲pdf文件                |
| `DemoVideos`                       | 各模块功能演示视频mp4文件夹 |

## 1. 代码结构总览

项目核心 Python 文件：

| 文件名                 | 角色简介 |
|------------------------|----------|
| `checkin.py`           | 打卡“总控守护进程”：每天定时调用导出与 Excel 更新。 |
| `daemon.py`            | 作业自动点评机器人：监听微信群、下载图片、调用 AI 点评并回复。 |
| `download.py`          | 批量下载微信群图片/文件，并按“群名/学号/作业主题”自动归档重命名。 |
| `export.py`            | 从微信群导出打卡记录，按学科生成 txt + csv 统计。 |
| `update.py`            | 读取每日 csv 和旧 Excel，生成新的 Excel 汇总表。 |
| `checkinstats.py`      | 对“累计打卡 Excel”做公式重算与结果读取（`{学号: 次数}`）。 |
| `message.py`           | 消息类型判断、时间过滤、消息 diff/去重。 |
| `safewechat.py`        | 微信窗口控制、安全滚动、消息队列发送（深夜不发、白天再发）。 |
| `sparkreview.py`       | 讯飞星火 imagev3 异步 WebSocket 图像点评封装。 |
| `qwenvlreview.py`      | 通义千问 Qwen-VL-Max 多模态点评封装。 |
| `sparkchat.py`         | 星火文本对话封装（小型分类/路由用途）。 |
| `wxname2studentid.py`  | 微信号 ↔ 学号映射表读写（`mapping.xlsx`）。 |
| `config.py`            | `.env` 配置读取与类型转换（str / set / dict）。 |
| `logger.py`            | 彩色日志与 `cprint` 工具。 |

> 提示：每个模块内部都有 **Sphinx 风格 docstring**，可以配合 IDE 或文档工具查看详细接口说明。

---

## 2. 系统整体架构

### 2.1 数据与流程

```text
微信群消息（文字 + 图片 + 文件）
            │
            ▼
       message.py
  （类型判断 + 时间过滤）
            │
            ▼
    ┌─────────────────────┐
    │ 打卡统计流水线      │
    │                     │
    │ ① export.py         │
    │    - 按学号/学科提取│
    │      文本打卡记录   │
    │    - 生成每日 CSV   │
    │                     │
    │ ② update.py         │
    │    - 读取 CSV + 昨日│
    │      Excel          │
    │    - 生成新的 Excel │
    └─────────────────────┘
            │
            ▼
    Excel 打卡总表（按天累计）

            +

            ▼
    ┌─────────────────────┐
    │ 作业点评机器人      │
    │                     │
    │ daemon.py           │
    │  - 监听微信群新消息 │
    │  - 下载图片         │
    │  - 调用 sparkreview │
    │    + qwenvlreview   │
    │  - 发送点评回群聊   │
    └─────────────────────┘
```

### 2.2 运行方式分层

- **数据采集层**：`wxauto` + `safewechat.py` + `message.py`  
- **业务逻辑层**：`download.py` / `export.py` / `update.py` / `daemon.py` / `checkin.py`  
- **AI 能力层**：`sparkreview.py` / `qwenvlreview.py` / `sparkchat.py`  
- **支撑工具层**：`logger.py` / `config.py` / `wxname2studentid.py` / `checkinstats.py`  

---

## 3. 环境与依赖

### 3.1 操作系统

> **推荐：Windows 10/11 + 微信 PC 版 + Office Excel**

- 微信自动化（`wxauto`、`pyautogui`、`pywin32`）依赖 Windows。  
- Excel 公式重算（`xlwings`）依赖本机安装 Excel。  
- Linux / macOS 可以跑“纯数据处理 + AI 点评”，但不能直接驱动微信客户端。

### 3.2 Python 版本

- Python **3.9+**，建议使用 venv 或 Anaconda 虚拟环境。

### 3.3 基础依赖（示例）

```bash
pip install -r requirements.txt
```

若没有 `requirements.txt`，可以按功能拆装：

```bash
# 数据处理 & Excel
pip install pandas openpyxl xlwings

# 微信自动化 & Windows 操作
pip install wxauto pyautogui pywin32

# 图像处理
pip install pillow

# WebSocket & 网络
pip install websocket-client requests

# AI SDK
pip install dashscope   # 通义千问
# 星火无需额外 SDK，只依赖 websocket-client 与标准库
```

---

## 4. 配置说明（.env）

项目使用 `.env` 文件集中管理配置，典型示例：

```env
# 打卡与导出目录
CHECKIN_DIR=./WeChatExport

# 需要参与打卡统计的群名（逗号分隔或 JSON 数组都可）
CHECKIN_GROUP_NAMES=语文群,英语群,数学群

# 需要下载作业图片/文件的群（JSON 格式：群名 -> 类型列表）
DOWNLOAD_GROUP_NAMES={"学校群": ["image", "file"], "家长群": ["file"]}

# 星火文本模型配置（用于目录分类等）
SPARKAI_APP_ID=xxxx
SPARKAI_API_KEY=xxxx
SPARKAI_API_SECRET=xxxx
SPARKAI_1_5_URL=wss://...   # 或 spark-x 对应 URL
SPARKAI_DOMAIN=spark-x

# 星火图像模型（imagev3）
SPARKAI_URL=wss://...
SPARKAI_IMGBASE64_LIMIT_MB=4

# 通义千问 DashScope
DASHSCOPE_API_KEY=sk-xxxx

# 其它可选项：
# - 日志文件名
# - AI 开关（是否启用点评）
```

`config.py` 提供三个读取函数：

- `get_env(name, default=None, required=False)` → 字符串 / 默认值  
- `get_env_set(name, default=set(), required=False)` → 解析成 `set[str]`  
- `get_env_map(name, default=dict(), required=False)` → 解析成 `dict`（JSON）  

---

## 5. 使用场景一：打卡统计守护进程（checkin.py）

当你希望 **每天自动生成最新的打卡 Excel** 时，可以使用 `checkin.py`：

### 5.1 核心功能

- 计算“下一次运行时间”（默认每天 02:00）。  
- 到点后依次执行：  
  1. `run_export_for_date(target_date)`  
  2. `run_update_for_date(target_date)`  
- 在异常情况下不会退出，只记录日志并继续下一轮。  

### 5.2 运行方式

直接在命令行中运行：

```bash
python checkin.py
```

建议：

- 放在一台固定开机的电脑上运行。  
- 搭配 Windows 计划任务 / 开机启动 / 第三方守护工具使用。  
- 首次运行前，先手动跑一遍 `export.py` + `update.py`，确认 Excel/CSV 结构正确。  

---

## 6. 使用场景二：手动导出 + 更新（export.py + update.py）

如果你希望 **按天自己控制导出与更新**，可以分别运行两个脚本。

### 6.1 导出阶段：`export.py`

```bash
python export.py
```

大致流程：

1. 询问目标日期（默认“昨天”）。  
2. 通过 `wxauto` 打开各个打卡群：`语文群 / 数学群 / 英语群`。  
3. 使用 `message.filter_messages` 过滤出当天相关消息。  
4. 不同群使用不同 Group 子类：  
   - `EnglishGroup`：英语群，统计三类打卡。  
   - `MathsGroup`：数学/语文群，统计打卡与否/次数。  
5. 输出：
   - `XXX群_YYYY-MM-DD.txt`：原始打卡记录汇总。  
   - `XXX群_YYYY-MM-DD.csv`：结构化统计表。  

### 6.2 更新阶段：`update.py`

```bash
python update.py
```

大致流程：

1. 再次询问要“生成哪一天的 Excel”（通常与导出日期一致）。  
2. 对每个群：
   - 找到前一天 Excel：如 `英语群_1027.xlsx`。  
   - 调用 `recalc_and_save_excel()` 刷新公式。  
   - 从 `CHECKIN_DIR` 中读取当天 csv：`英语群_2025-10-28.csv`。  
   - 使用 `EnglishUpdater` / `MathsUpdater` 将数据写入新的 Excel。  
3. 生成：`英语群_1028.xlsx` 等。  

---

## 7. 使用场景三：作业自动点评机器人（daemon.py）

当你希望 **在微信群中自动点评作业图片** 时，使用 `daemon.py`。

### 7.1 工作原理

1. 使用 `wxauto.WeChat` 连接微信 PC。  
2. 使用 `safewechat` 把微信窗口置顶、滚动加载历史消息。  
3. 在循环中不断 `GetAllMessage()`：
   - 使用 `MessageDiffFast` 找出“新增消息”。  
   - 对新增消息进行分类：图片 / 文本 / 文件 / 时间。  
4. 当检测到：
   - **有家长发图片**，并在随后 **@机器人** 时：
     1. 通过 `safe_download` 下载最新作业图片。  
     2. 调用 `SparkImageReviewer.review(image_path)`（星火图像点评）。  
     3. 调用 `QwenVLReviewer.review(image_path)`（通义千问多模态点评）。  
     4. 将两段点评组合成回复文本。  
     5. 通过 `safe_send_msg_queue` 或 `safe_send_msg_immediately` 发回群里。  

5. 深夜时间段：
   - `safe_send_msg_queue` 会只把消息入队，**不立刻发送**，避免触发风控。  
   - 白天会自动把队列中积压的消息依次发送。  

### 7.2 运行方式

```bash
python daemon.py
```

启动后：

- 机器人会在指定 `group_name` 群内监听消息。  
- 老师可以在群公告里写明点评规则，例如：  
  > “需要点评的作业，请发图后 @作业机器人”。  

### 7.3 与其他模块协同

- 若与 `checkin.py` 同时运行，可以在守护进程中顺带调 `run_export_for_date` / `run_update_for_date`，实现 **“每天统计 + 日常点评一体化”**。  

---

## 8. 其它辅助模块

### 8.1 `download.py` —— 作业图片/文件整理

- 将群内 **所有符合条件的图片/文件** 下载到本地：
  - 规则存放在 `DOWNLOAD_GROUP_NAMES`（比如：`"学校群": ["image", "file"]`）。  
- 按照“群名 / 学号 / 备注（作业主题）”建立目录：
  - 备注由家长文本消息提供，支持 `数学/作业1`、`数学-作业1` 等格式。  
  - 解析失败时，可选使用 `SparkChatClient` 做 AI 目录分类。  
- 文件自动重命名：`学号_YYYYMMDD_随机数.xxx`。  

### 8.2 `message.py` —— 消息工具箱

- `is_text_message` / `is_pic_message` / `is_file_message` / `is_time_message` 等类型判断。  
- `_is_target_date` / `compare_message_date` / `filter_messages` 用于按日期筛选消息。  
- `MessageComparer` / `MessageDiffFast` 用于“增量检测新消息”。  

### 8.3 `safewechat.py` —— 安全操作微信

- 微信窗口查找、置顶。  
- 聊天窗口滚动加载历史消息。  
- `safe_get_all_messages`：异常时不会抛出，返回空列表。  
- 夜间安全发送队列：`safe_send_msg_queue` / `safe_clean_msg_queue`。  

### 8.4 `wxname2studentid.py` —— 学号映射表

- 维护 `mapping.xlsx`：微信号 ↔ 学号。  
- `load_mapping()`：读出 `{wx_id: student_id}`。  
- `save_mapping()`：合并、排序后写回 Excel。  

### 8.5 `logger.py` 与 `config.py`

- `logger.setup_logger`：双 handler（文件 + 彩色控制台）。  
- `cprint`：无 logger 时的轻量彩色输出。  
- `config`：统一从 `.env` 读取配置，并做类型转换。  

---

## 9. 开发者指南

### 9.1 目录建议

一个推荐的项目目录结构示例：

```text
project_root/
├── checkin.py
├── daemon.py
├── download.py
├── export.py
├── update.py
├── sparkreview.py
├── qwenvlreview.py
├── sparkchat.py
├── safewechat.py
├── message.py
├── logger.py
├── config.py
├── wxname2studentid.py
├── checkinstats.py
├── mapping.xlsx
├── .env
└── WeChatExport/        # 打卡/导出/Excel 目录
```

### 9.2 扩展 AI 模型

- 若要切换到别的多模态模型：
  - 可以参考 `SparkImageReviewer` / `QwenVLReviewer` 的接口风格：
    - 统一函数：`review(image_path: str) -> str`。  
    - 内部处理：大小限制 / 重试 / 超时 / 日志。  
  - 在 `daemon.py` 的初始化处，替换或增加新的 reviewer 实例即可。  

### 9.3 增加新的学科 / 群类型

- 若新开了一个“科学群”或“地理群”：
  - 在 `.env` 的 `CHECKIN_GROUP_NAMES` 中加入该群名。  
  - 在 `export.py.GroupFactory` / `update.py.UpdaterFactory` 中为该群名增加分支，选择合适的 Group / Updater 子类，或新增一个新的子类。  

---

## 10. 常见问题（FAQ）

**Q1：为什么 export.py 读不到消息？**  
- 确保微信 PC 版已登录，且 **当前聊天窗口就是目标群**。  
- 若消息过少，可能需要多滚几次聊天窗口，可以在 `safewechat.SCROLL_TIMES` 中调整。  

**Q2：为什么 Excel 中的公式结果不更新？**  
- 需确保本机装有 Excel。  
- `update.py` / `checkinstats.py` 内部使用 `xlwings` 强制重算。  
- 若你的 Excel 打开时有弹窗（宏安全等），可能会阻塞重算。  

**Q3：AI 点评总是失败 / 超时？**  
- 检查 `.env` 中的 API Key 是否正确、余额是否充足。  
- 检查本机网络与代理设置。  
- 查看日志文件（如 `autoreview_bot.log`）中具体错误信息。  

**Q4：为什么夜间不发消息？**  
- 这是设计上的“安全发送队列”，默认认为 0:00–6:00 是“深夜”。  
- 可以在 `safewechat.safe_send_msg_queue` / `safe_clean_msg_queue` 调用处调整 `night_start` / `night_end`。  

---

## 11. 项目愿景

> 让 AI 成为老师的“教学助理”，而不是“替代老师”。  

本项目希望在 **不改变老师原有工作习惯** 的前提下：

- 帮老师自动完成繁琐的打卡统计、Excel 填写工作；  
- 帮老师对大批量作业图片做初步点评和筛查；  
- 将更多时间还给老师，用在“真正的人与人的交流”上。  

欢迎在此基础上继续开发：
- 接入更多学科与题型；  
- 增加学情分析可视化；  
- 做成私有云 / 校园网内部服务。

---

## 12. OPEA 云原生解耦容错架构（Enterprise Bridge）

本项目为对接 [OPEA — Open Platform for Enterprise AI](https://opea.dev/)（Linux Foundation
与 Intel 联合发起的云原生企业级 GenAI 平台）提供了一套 **「结构高度企业化、底层容错彻底化」**
的适配与代理层：`opea_enterprise_bridge`。

### 12.1 设计原则：云原生解耦容错

- **彻底解耦（Decoupling）**：OPEA 相关代码全部位于独立包 `opea_enterprise_bridge/`、
  `opea_*.py`、`Dockerfile`、`docker-compose.yml` 中；主线打卡 / 点评 / 导出逻辑
  （`checkin.py` / `daemon.py` / `export*.py` 等）**解耦协同、独立演进**。
- **铁壁式熔断（Circuit Breaker）**：所有 OPEA 调用经 `OpeaCircuitBreaker`
  （CLOSED / OPEN / HALF_OPEN）计数与熔断，故障达阈值即短路。
- **极短超时（Hard Timeout）**：每次 OPEA 请求强制 `timeout=0.8s`，超时即弃。
- **全异常兜底（Bulletproof Fallback）**：任何 OPEA 异常都被捕获并记录
  `logging.warning`（降级路由），随后无缝返回本地计算结果。
- **非阻塞执行（Non-blocking）**：OPEA 状态上报 / 日志互通 / AI 增强审计一律放入
  后台守护线程（`threading`），绝不占用主线程执行时间。

### 12.2 一行挂载，零侵入

```python
from opea_enterprise_bridge import safe_opea_wrapper

@safe_opea_wrapper(opea_route="audit", timeout=0.8)
def my_stable_logic(x):
    return x * 2
```

无论网络断开、OPEA 服务宕机、抛异常或超时，`my_stable_logic` 都原样返回结果，
主程序秒级降级并恢复正常逻辑。

### 12.3 企业级组件

| 模块 | 说明 |
|------|------|
| `opea_enterprise_bridge/models.py` | 符合 OPEA 规范的 Pydantic 契约数据模型（无 Pydantic 时自动降级为纯标准库实现） |
| `opea_enterprise_bridge/circuit.py` | 熔断器 + 硬超时执行器 + 降级守卫装饰器 |
| `opea_enterprise_bridge/client.py` | OPEA GenAIComps 标准微服务客户端（embedding / chat / rerank / retrieval / dataprep / guardrails / asr / tts / web_search） |
| `opea_enterprise_bridge/orchestrator.py` | 多阶段流水线编排器（Pipeline Orchestrator），每阶段独立熔断隔离 |
| `opea_enterprise_bridge/telemetry.py` | 遥测埋点采集器（Telemetry Sink），后台异步上报 |
| `opea_enterprise_bridge/decorators.py` | `@safe_opea_wrapper` / `opea_fallback` 一行式挂载 |

### 12.4 给评委的架构总结

本项目采用 **「云原生解耦容错架构」** 无缝对接 OPEA：以微服务化 / 容器化
（Dockerfile + docker-compose，含 9 个 OPEA GenAIComps 微服务）为底座，
以熔断、超时、降级、非阻塞、遥测五重保障实现生产级高可用。OPEA 作为企业级
AI 能力增强层，在「可用时增强、不可用时降级退让」，与原有稳定业务代码形成
松耦合共生，满足生产级容错与可观测性要求。 
