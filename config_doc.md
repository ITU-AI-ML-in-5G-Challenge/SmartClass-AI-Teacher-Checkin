# `config.py` 模块文档

## 1. 模块定位

`config.py` 是整个项目的 **配置中心**，用于统一从 `.env` 环境文件中读取配置项，并做类型转换。

它解决的问题：

- `.env` 里所有变量都是“字符串”，但代码希望得到：  
  - 单个字符串（比如：工作目录路径）  
  - 集合 `set[str]`（比如：要处理的微信群名列表）  
  - 字典 `dict`（比如：群名 → 要下载的消息类型）  

---

## 2. 环境变量加载

模块使用 `python-dotenv` 的 `load_dotenv()`：

- 默认从当前工作目录的 `.env` 文件中读取变量
- 你可以在 `.env` 里写类似：

```env
CHECKIN_DIR=./WeChatDownload
CHECKIN_GROUP_NAMES=语文群,英语群,数学群

DOWNLOAD_GROUP_NAMES={"学校群": ["image", "file"], "家长群": ["file"]}
```

---

## 3. 核心函数

### 3.1 `get_env(name: str, default=None, required=False, logger=None)`

- **作用**：从环境中读一个变量，返回字符串或默认值。  
- **参数**：
  - `name`：变量名
  - `default`：未设置时的默认值
  - `required`：若为 True 且没找到，会打印错误日志
  - `logger`：传入自定义 logger（否则内部用默认）

**使用示例**：

```python
from config import get_env

work_dir = get_env("CHECKIN_DIR", default="./WeChatDownload")
```

---

### 3.2 `get_env_set(name: str, default=set(), required=False, logger=None)`

- **作用**：将某个环境变量解析为 `set[str]`
- **支持两种格式**：
  1. 逗号分隔：`"语文群, 英语群, 数学群"`
  2. JSON 数组：`["语文群", "英语群", "数学群"]`
- 内部会：
  - 去除空白
  - 解析失败时记录日志，并返回空集合

**使用示例**：

```python
from config import get_env_set

group_names = get_env_set("CHECKIN_GROUP_NAMES")
# 结果类似：{"语文群", "英语群", "数学群"}
```

---

### 3.3 `get_env_map(name: str, default=dict(), required=False, logger=None)`

- **作用**：把一个 JSON 字符串解析为 `dict`。  
- **典型格式**：

```env
DOWNLOAD_GROUP_NAMES={"学校群": ["image", "file"], "家长群": ["file"]}
```

- 行为：
  - 用 `json.loads()` 解析
  - 必须解析为字典类型，否则视为错误
  - 出错时记录日志，并返回空 dict

**使用示例**：

```python
from config import get_env_map

download_groups = get_env_map("DOWNLOAD_GROUP_NAMES")
# 结果：{"学校群": ["image", "file"], "家长群": ["file"]}
```

---

## 4. 设计要点

- 所有“读取配置”的逻辑都集中在这个模块，避免在各处手写 `os.getenv()` + 解析。  
- 利用日志输出可以快速定位：是 `.env` 配错了，还是没写。  

**一句话总结**：  
> `config.py` 就是：**“统一从 .env 把字符串读出来，顺便转成你真正想用的 Python 类型”**。  
