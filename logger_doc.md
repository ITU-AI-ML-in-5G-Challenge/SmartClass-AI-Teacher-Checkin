# `logger.py` 模块文档

## 1. 模块定位

`logger.py` 提供了本项目通用的 **彩色日志工具**，包括：

- 标准的 `logging.Logger`（写文件 + 彩色控制台输出）
- 一个轻量级的 `cprint` 类（直接 `print` 带颜色）
- 一个 `get_logger()` 适配函数，在 **没传 logger 时自动退化为 `cprint`**

---

## 2. 颜色规则

无论是 `ColorFormatter` 还是 `cprint`，颜色映射规则一致：

- `DEBUG`：白色  
- `INFO`：亮绿  
- `WARNING`：亮黄  
- `ERROR`：亮红  
- `CRITICAL`：红底白字  

这保证了在终端中各模块日志颜色一致。

---

## 3. 核心组件

### 3.1 `ColorFormatter(logging.Formatter)`

- 用于 `logging` 的彩色输出：
  - 文件 handler：普通 Formatter（无颜色）
  - 控制台 handler：`ColorFormatter`（带 ANSI 颜色前后缀）
- 重写 `format()` 方法：
  - 根据 `record.levelname` 选择颜色前缀
  - 调用父类 `super().format(record)` 获取原始字符串
  - 再包上一层颜色转义码 + RESET

### 3.2 `cprint`

- 一个简单的 **静态方法集合**，直接 `print` 彩色文本：

```python
from logger import cprint

cprint.info("程序启动")
cprint.warning("这个参数有点怪")
cprint.error("出错啦")
```

- 适合：
  - 快速脚本
  - 还不想上 `logging` 的小模块
  - 或者作为默认 logger（见 `get_logger`）

### 3.3 `setup_logger(log_file: str = "autoreview_bot.log") -> logging.Logger`

- 创建带两个 handler 的 logger：
  1. 文件 handler：写入 `log_file`，格式包括时间 / 模块名 / 等级 / 文件名 / 行号
  2. 控制台 handler：使用 `ColorFormatter`，彩色输出
- 若 logger 已经有 handler，就不会重复添加。
- 最后 `logger.propagate = False`，防止日志冒泡到根 logger 而重复输出。

**典型用法**：

```python
from logger import setup_logger

logger = setup_logger("checkin.log")
logger.info("打卡守护进程已启动")
```

### 3.4 `get_logger(logger=None)`

- 实现了一个实用的小约定：

```python
def get_logger(logger=None):
    return logger or cprint
```

这意味着，在别的模块里写：

```python
self.logger = get_logger(logger)
```

- 若调用方传入了真正的 `logging.Logger` → 直接用这个 logger  
- 若没传 → 自动退化为 `cprint`，依然可以：

```python
self.logger.info("...")
self.logger.error("...")
```

（因为 `cprint` 的方法名和 logger 的方法名保持一致。）

---

## 4. 使用建议

- 在“主入口”脚本里（如 `daemon.py` / `checkin.py`），推荐调用 `setup_logger()`，给整套系统统一一个 logger 实例。
- 在“工具模块”里，推荐接受一个 `logger=None` 参数，并用：

```python
from logger import get_logger

self.logger = get_logger(logger)
```

这样做的好处：

- 既支持统一 logger 管理
- 又允许模块在“单独跑”时仍然能看到彩色输出（借助 `cprint`）

**一句话总结**：  
> `logger.py` 为整个项目统一了日志风格：**“要么是真 logger，要么是假 logger，但你总能 `.info()` / `.error()` 打出彩色日志”。**  
