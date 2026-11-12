# `sparkreview.py` 模块文档

## 1. 模块定位

`sparkreview.py` 封装了 **讯飞星火多模态图像模型（imagev3）** 的调用逻辑，主要用于：

- 将学生提交的 **作业图片** 发送给星火模型  
- 生成一段“初中英语女老师风格”的中文点评文本（约 150 字以内）  
- 自动处理图片压缩、base64 大小限制、WebSocket 超时、网络异常等问题  

在整个系统中，它是 **自动作业点评的“第一模型”**（另一模型是 `QwenVLReviewer`）。

---

## 2. 图片预处理工具

模块开头有三个位于顶层的工具函数：

### 2.1 `is_base64_over_limit(image_path: str, limit_mb: int, logger=None) -> bool`

- 检查“将图片转成 base64 之后”的大小是否超过 `limit_mb`。  
- 有日志输出：原文件大小、base64 后大小。  

### 2.2 `compress_image_to_base64(image_path: str, limit_mb: int, logger=None) -> str`

- 若原图太大，通过降低 **JPEG 质量** 来进行压缩：  
  - PNG 会先转成 JPEG（因为 PNG 很难通过质量来减小）  
  - 从 `quality=95` 开始，逐步降低到 `min_quality=20`  
  - 每次尝试：
    - 把图像保存到内存 buffer
    - 转成 base64，检查长度是否低于 `limit_mb`  
  - 如果到了 20 还是超限，就返回当前最小版本（再降就太糊了）  

### 2.3 `get_safe_base64(image_path: str, limit_mb: int, logger=None) -> str`

- 对外统一接口：
  1. 先尝试直接 base64 原图  
  2. 若未超过限制，直接使用原图 base64  
  3. 若超限，则调用 `compress_image_to_base64(...)` 自动压缩  

这是后续 `SparkImageReviewer.review()` 里获取 base64 的唯一入口。

---

## 3. 核心类：`SparkImageReviewer`

### 3.1 初始化

```python
from sparkreview import SparkImageReviewer

reviewer = SparkImageReviewer()
```

参数来源：

- `SPARKAI_APP_ID`  
- `SPARKAI_API_KEY`  
- `SPARKAI_API_SECRET`  
- `SPARKAI_URL`（WebSocket 地址）  
- `SPARKAI_IMGBASE64_LIMIT_MB`（base64 大小限制，默认 4MB）  

如缺少重要配置，会抛出 `RuntimeError` 提示在 `.env` 中设置。

属性：

- `timeout_seconds`：WebSocket 请求超时（默认 25 秒）  
- `max_image_size`：原始图片大小限制（默认 8MB）  
- `max_length`：点评长度控制（目前被注释掉，只在逻辑上保留）  

---

## 4. WebSocket URL 构建：`_create_url()`

逻辑与 `sparkchat.py` 类似：

1. 解析 `self.url` 的 host 和 path  
2. 生成 HTTP 格式时间 `http_date`  
3. 构造签名字符串：

```text
host: {host}
date: {http_date}
GET {path} HTTP/1.1
```

4. 使用 `api_secret` 做 HMAC-SHA256，结果做 base64  
5. 再构造 `authorization` 字符串并 base64  
6. 最终拼到 query 参数中返回完整 URL  

若过程中出错，会记录日志并抛异常。

---

## 5. Payload 构造：`_gen_payload(img_base64: str)`

构造发给 imagev3 的 payload：

```json
{
  "header": {"app_id": ...},
  "parameter": {
    "chat": {
      "domain": "imagev3",
      "temperature": 0.3,
      "top_k": 2,
      "max_tokens": 1500
    }
  },
  "payload": {
    "message": {
      "text": [
        {"role": "user", "content": "<base64图像>", "content_type": "image"},
        {"role": "user", "content": "<长 prompt>", "content_type": "text"}
      ]
    }
  }
}
```

Prompt 的风格与 `QwenVLReviewer` 非常相似：

- 设定为初中英语女老师  
- 要求点评：总体评价 + 错误指正 + 鼓励建议  
- 不要提到“模型/AI/程序”等字眼  
- 字数控制在 150 字以内  
- 避免流水账、套话，要求对作业稍微有点深度的点评  

---

## 6. 点评主流程：`review(image_path: str, retry: int = 2) -> str`

1. **图片大小检查**：
   - `os.path.getsize(image_path)`  
   - 若超过 `max_image_size` → 返回 `"【图片太大，请压缩后重新发送】"`  

2. **获取安全 base64**：  
   - 调用 `get_safe_base64(image_path, self.max_image_base64_limit_mb, self.logger)`  
   - 若读图 / 压缩失败 → 返回 `"【无法读取图片，请重新发送】"`  

3. **自动重试**：  
   - 最多尝试 `retry + 1` 次，调用 `_review_once(img_base64)`  
   - 每次失败记录日志，并 `time.sleep(1)` 再试  

4. **最终失败**：  
   - 返回 `"【点评服务暂时不可用，请稍后再试】"`  

---

## 7. 单次 WebSocket 调用：`_review_once(img_base64: str) -> str`

1. 调用 `_create_url()` 得到 `ws_url`  
2. 定义 WebSocket 回调：
   - `on_message`：
     - 解析 JSON
     - 检查 `header.code`
     - 从 `payload.choices.text[0].content` 取出内容，追加到 `final_answer` 列表
     - 若 `status == 2`，关闭连接
   - `on_error`：记录错误  
   - `on_close`：记录“WebSocket 已关闭”  
   - `on_open`：在新线程中发送 `json.dumps(self._gen_payload(img_base64))`  

3. 创建 `WebSocketApp`：

```python
ws = websocket.WebSocketApp(
    ws_url,
    on_open=on_open,
    on_message=on_message,
    on_error=on_error,
    on_close=on_close,
)
```

4. 用一个 daemon 线程来执行 `ws.run_forever(...)`，主线程：
   - `thread.join(self.timeout_seconds)` 等待  
   - 若超时还在运行：关闭连接并返回 `"【点评超时，请稍后再试】"`  

5. 将 `final_answer` 中的片段拼接成完整点评文本 `text`：
   - 若为空 → `"【未收到点评内容，请稍后再试】"`  
   - 否则返回 `text` （可以按需再做长度截断）  

---

## 8. 使用示例

```python
from sparkreview import SparkImageReviewer

reviewer = SparkImageReviewer()

result = reviewer.review("homework.jpg")
print("星火点评：", result)
```

在机器人中，经常是与 `QwenVLReviewer` 搭配：

```python
spark_comment = spark_reviewer.review(image_path)
qwen_comment = qwen_reviewer.review(image_path)

reply = f"[星火点评]\n{spark_comment}\n\n[通义千问点评]\n{qwen_comment}"
```

---

## 9. 总结

`sparkreview.py` 把 **“图片压缩 + base64 限制 + WebSocket 鉴权 + 多模态调用 + 超时控制”** 这一整套复杂流程，封装成了一个简单的：

```python
SparkImageReviewer().review(image_path)
```

> 这使得上层业务（如 `daemon.AutoReviewBot`）几乎不需要关心任何底层细节，只需要管：“给你图片，你给我一段靠谱的点评文本”。  
