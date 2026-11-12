# `checkin.py` 模块文档

## 1. 模块定位

`checkin.py` 是整个“打卡系统”的 **守护进程调度器**，负责在每天固定时间驱动两件事：

- **导出微信群打卡数据**（调用 `export.py`）
- **更新 Excel 汇总表**（调用 `update.py`）

你可以把它理解为：**一个只负责“每天几点钟启动其它模块”的总控脚本**。

---

## 2. 核心职责

- 计算下一次运行时间（例如每天 02:00）
- 到点后依次调用：
  - `run_export_for_date(target_date, logger)`：对指定日期做“文字导出 + 统计”
  - `run_update_for_date(target_date, logger)`：对指定日期做“Excel 更新合并”
- 提供一个长期运行的 `main_loop()`：
  - 循环睡眠 → 到点执行任务 → 再算下次时间
  - 支持键盘退出（如 ESC / Ctrl+C）

---

## 3. 主要函数

### 3.1 `run_export_for_date(target_date, logger=None)`

- **作用**：对某一天（通常是“昨天”）执行一次 **导出微信群打卡 + 生成 CSV/TXT** 的逻辑。
- **内部会做的事**（大致流程）：
  1. 设置环境变量（工作目录、群名列表等）
  2. 调用 `export.GroupFactory` 为每个需要处理的群创建 `Group` 对象
  3. 使用 wxauto 打开对应微信群，读取消息，产出打卡文本和 CSV
  4. 视配置情况，可能还会**把统计结果发回微信群**

你一般不会直接调用 `export.main()`，而是通过这个函数间接调用。

---

### 3.2 `run_update_for_date(target_date, logger=None)`

- **作用**：对某一天执行一次 **Excel 汇总更新**。
- **内部会做的事**：
  1. 使用 `UpdaterFactory` 为每个群构造 `EnglishUpdater` / `MathsUpdater`
  2. 每个 updater：
     - 打开昨日 Excel，刷新公式
     - 读取当日 CSV
     - 把昨天数据平移到“历史列”，把今天数据写到“今日列”
     - 生成新的 Excel 文件（以日期命名）

---

### 3.3 `get_next_run_time(target_hour=6, target_minute=0)`

- **作用**：计算“下一次调度时间”。
- 默认策略：
  - 每天 **target_hour:target_minute** 执行
  - 若当前时间已过今天的time，则返回 **明天time**，否则返回 **今天time**

---

### 3.4 `main_loop()`

- **作用**：真正的守护进程主循环。
- 大致逻辑：
  1. 启动时先算一次 `next_run_time`
  2. 在 `while True` 循环中：
     - 如果还没到点 → 睡一小会（避免 CPU 空转）
     - 到点了：
       - 先跑 `run_export_for_date()`
       - 再跑 `run_update_for_date()`
       - 重新计算下一次执行时间
  3. 捕获异常，保证即使某一天任务失败，守护进程不会直接退出。

---

## 4. 使用方式

最常见的用法：直接把 `checkin.py` 作为 **长期后台进程** 跑起来：

```bash
python checkin.py
```

在实际部署时，你可能会：

- 用 Windows 计划任务 / Linux systemd 拉起此脚本
- 或者用 `nohup`、`screen`、`tmux` 之类挂在后台

---

## 5. 和其它模块的关系

- 依赖：
  - `export.py`：负责“从微信导出文字 + 统计”
  - `update.py`：负责“Excel 汇总更新”
  - `logger.py`：输出日志
- 不做的事：
  - 不直接处理微信消息
  - 不直接操作 Excel 表格内容
  - 不关心打卡的具体分类（英语 / 数学 / 语文）

**一句话总结**：  
> `checkin.py` = 负责“什么时候跑”的大总管，其它模块负责“具体怎么干活”。
