# `download.py` 模块文档

## 1. 模块定位

`download.py` 负责 **从微信群批量下载图片和文件**，并根据家长备注和学号映射进行合理的目录归类与命名。

它解决的问题：

- 群里家长发很多作业图片 / PDF / 文档，想要 **自动整理到本地文件夹**：
  - 按照：群名 / 学生名或学号 / 作业主题（备注） 来建立目录结构
  - 文件名带有学号 + 日期 + 随机数，避免重名覆盖
- 支持“连续多张图片 + 一条备注”这种发送模式，整体归为同一份作业。

---

## 2. 依赖关系

- `wxauto`：和微信交互、下载图片 / 文件
- `safewechat`：安全滚动聊天窗口、打开群聊
- `sparkchat`：AI 帮忙判断备注对应什么目录（高级用法）
- `wxname2studentid`：微信号 ↔ 学号 映射
- `message`：识别消息类型（图片 / 文件 / 文本 / 时间）
- `config`：读取环境变量配置，如下载目录、要处理的群名列表

---

## 3. 核心类：`GroupDownload`

### 3.1 初始化

```python
from download import GroupDownload

gd = GroupDownload(
    name="学校群",
    msgs=wx.GetAllMessage(),
    target_date=datetime.today(),
    logger=logger,
)
```

- `name`：微信群名称  
- `msgs`：从 `wxauto.GetAllMessage()` 获取的消息列表  
- `target_date`：仅处理这一天的作业（通常是今天或昨天）  
- `logger`：日志对象  

---

## 4. 关键方法概览

### 4.1 `make_subdir(directory: str, name: str) -> str`

- 在指定 `directory` 下创建子目录 `name`，若不存在就自动创建。
- 返回创建好的子目录完整路径。

### 4.2 `sanitize_filename(name: str) -> str`

- 清除 Windows 文件名中不允许的字符：`\ / : * ? " < > |`
- 防止保存文件时报错。

### 4.3 `parse_path_pattern(raw: str, group_name: str, directory: str) -> str | None`

- 将家长发的“备注文本”转换为一个 **目录路径前缀**。  
- 支持的形式举例：
  - `作业1` → `目录/群名/作业1`
  - `数学/作业1` → `目录/群名/数学/作业1`
  - `数学-作业1` → 自动当成 `数学/作业1`
- 自动规则：
  - 如果开头是 `/` 会被去掉
  - 如果第一层不是群名，会自动加上群名

### 4.4 `clean_prefix(prefix: str, max_len: int = 50) -> str`

- 用于生成文件名前缀（通常是“学生昵称或学号 + 作业标题”）。
- 会：
  - 去掉非法字符
  - 合并多余空白
  - 控制整体长度不超过 `max_len`

### 4.5 `rename_file(full_path: str, prefix: str, date: datetime) -> str`

- 对已经下载好的文件进行重命名：

格式类似：

```text
{prefix}_{YYYYMMDD}_{随机数}.{原扩展名}
```

- 如已有同名文件，会先删除旧文件再重命名，避免异常。
- 返回重命名后的新路径。

### 4.6 `safe_download(m, img_dir, max_retries=3)`

- 封装图片下载逻辑（与 `daemon` 中的类似）：
  - 多次重试
  - 滚动聊天窗口预加载
  - 校验文件大小

### 4.7 `process_images_files_with_remark(start_index, base_dir, sender_remark, target_date)`

**这是整个模块的“核心处理函数”之一**：

针对 **同一个发送者的一批连续图片/文件 + 备注文本**，做这些事情：

1. 从 `msgs[start_index:]` 开始，收集：  
   - 同一人的连续图片消息 / 文件消息
   - 第一条跟随其后的文本备注（如果有）
2. 根据备注解析目录路径（`parse_path_pattern`）；
   - 实在解析不了时，可以用 AI（Spark）辅助决定目录
3. 为每张图片/文件生成安全的文件名前缀，并调用 `rename_file`
4. 将这一批处理过的消息从 `msgs` 中剔除
5. 返回新的消息列表和“下一条消息”的 index，供外部 while 循环继续处理

### 4.8 `download_group(mapping)`

- 对整个群的所有消息执行下载逻辑：

大致步骤：

1. 只处理 `target_date` 当天的消息  
2. 忽略自己发的消息  
3. 遇到图片 / 文件消息时：
   - 找到这条消息在 `msgs` 中的 index
   - 调用 `process_images_files_with_remark(...)`
   - 得到更新后的 `msgs` 和新的 index（因为已经删掉了若干条消息）  

---

## 5. `main()` 命令行入口

- 初始化：
  - 读取 `.env` 中的 `DOWNLOAD_GROUP_NAMES` 和下载目录
  - 初始化 `WeChat`、logger、映射表等
- 交互式：
  - 让用户选择本次要处理的群
  - 打开群、滚动加载历史消息
  - 创建 `GroupDownload` 并调用 `download_group()`

典型使用：

```bash
python download.py
```

---

## 6. 使用建议

- **调试期**：可以先只处理一个“小测试群”，观察目录结构是否符合预期。  
- **正式使用**：
  - 建议先备份下载目录
  - 确保 `mapping.xlsx` 里的“微信号 ↔ 学号”映射基本完整
  - 备注规范约定好（比如：统一写“数学/第3次作业”）  

**一句话总结**：  
> `download.py` 的使命就是：**把微信群里乱七八糟的作业图片，变成你硬盘上结构清晰的一整套“学生作业归档系统”**。  
