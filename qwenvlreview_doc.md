# `qwenvlreview.py` 模块文档

## 1. 模块定位

`qwenvlreview.py` 封装了 **阿里云 DashScope 的通义千问多模态模型（Qwen-VL-Max）**，用于对作业图片做自动点评。

特点：

- 输入：**本地图片路径**
- 输出：**一段 150 字以内的中文点评文字**
- 模拟“年轻、严格但温柔的初中英语女老师”的口吻

在机器人里通常作为 **第二个点评模型**，和 `sparkreview.py` 的星火模型搭配使用。

---

## 2. 依赖关系

- `dashscope`：阿里 DashScope SDK
- `config.get_env`：读取 `DASHSCOPE_API_KEY`
- `logging`：日志输出
- `base64`：图片转 Base64
- `time`：重试间隔

---

## 3. 核心类：`QwenVLReviewer`

### 3.1 初始化

```python
from qwenvlreview import QwenVLReviewer

reviewer = QwenVLReviewer()
```

主要参数：

- `api_key`：可显式传入；若为空，从环境变量 `DASHSCOPE_API_KEY` 中读取  
- `logger`：日志对象，默认 `logging.getLogger("QwenVLReviewer")`  
- `max_image_size`：图片最大字节数（默认 8MB）  
- `max_length`：点评最大长度（内部目前按 150 字来设计）  
- `timeout_seconds`：DashScope 请求超时秒数  

初始化时：

- 会设置 `dashscope.base_http_api_url = "https://dashscope.aliyuncs.com/api/v1"`  
- 若缺少 `DASHSCOPE_API_KEY` 会直接抛 `RuntimeError`。

---

## 4. Prompt 设计：`_build_prompt()`

内部使用一个较长的中文 Prompt 来约束回复风格：

- 人设：初中英语女老师，年轻、温和但要求严格  
- 要求：
  - 先理解图片中的题目 & 学生作答
  - 给一个总体评价
  - 指出明显错误或不足
  - 给出鼓励性建议（学习方法 / 打卡习惯 / 书法 / 态度）
  - 不出现“模型”“AI”等字眼
  - 不要用列表形式，而是一整段自然语言
  - 不超过 150 字（避免被系统截断）

这一段 Prompt 是整个点评风格的核心，后续若你想调整老师口吻，只需在这里改。

---

## 5. 点评流程

### 5.1 `review(image_path: str, retry: int = 2) -> str`

完整的“对一张图片做点评”的流程：

1. **图片大小检查**  
   - 调用 `os.path.getsize(image_path)`  
   - 若超过 `max_image_size` → 返回 `"【图片过大，请压缩后再试】"`  

2. **读取图片并转 Base64**  
   - `with open(image_path, "rb") as f`  
   - `img_base64 = base64.b64encode(f.read()).decode()`  
   - 失败 → 日志记录 `"图片读取失败"` / `"读取失败"`，并返回 `"【无法读取图片】"`  

3. **重试机制**  
   - 最多尝试 `retry + 1` 次（默认 3 次）  
   - 每次调用 `_review_once(img_base64)`  
   - 如遇异常，记录日志并 `time.sleep(1)` 后重试  

4. **所有尝试都失败**  
   - 返回 `"【点评服务暂不可用，请稍后再试】"`  

### 5.2 `_review_once(img_base64: str) -> str`

- 构造一个 Data URI：`"data:image/jpeg;base64,..."`  
- 构造 `messages` 列表（多模态对话）：
  - 第一部分是图片 `{"image": data_uri}`  
  - 第二部分是文本 prompt `{"text": prompt}`  
- 调用：

```python
rsp = dashscope.MultiModalConversation.call(
    api_key=self.api_key,
    model="qwen-vl-max",
    messages=messages,
    timeout=self.timeout_seconds,
)
```

- 从返回中提取文本内容：
  - `rsp.output.choices[0].message.content` 是一个 `parts` 列表
  - 将其中每个 `item["text"]` 拼接起来，得到最终点评文本  

- 若解析失败，则返回 `"【未收到有效点评结果】"`。

---

## 6. 使用示例

```python
from qwenvlreview import QwenVLReviewer

reviewer = QwenVLReviewer()

result = reviewer.review("sample_homework.jpg")
print("模型点评：", result)
```

在机器人逻辑中，通常写成：

```python
spark_comment = spark_reviewer.review(image_path)
qwen_comment = qwen_reviewer.review(image_path)

final_text = f"[星火点评]\n{spark_comment}\n\n[通义千问点评]\n{qwen_comment}"
```

---

## 7. 总结

`qwenvlreview.py` 的角色可以用一句话来概括：

> 它是一个“小而专注”的适配层，帮你把 **“本地图片” → “通义千问多模态 API” → “中文老师风格点评”** 这一整条链路封装成一个 `review()` 函数。  
