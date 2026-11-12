# `sparkchat.py` 模块文档

## 1. 模块定位

`sparkchat.py` 封装了 **讯飞星火大模型（Spark）聊天接口** 的调用逻辑，基于 WebSocket。

特点：

- 使用 HMAC-SHA256 做鉴权，手动构造带签名 URL  
- 支持多轮对话（内部维护 `history`）  
- 使用 **System Prompt** 强制约束回答风格为“短句、扼要、中文、不用 Markdown”  
- 提供一个简单的对外方法：`chat(user_query: str) -> str`

适用于：

- 快速做一些文本类判断 / 分类
- 为下载模块等提供小型“智能路由”功能  
- 不追求长篇大论，只要一两句回答即可的场景  

---

## 2. 初始化配置

```python
from sparkchat import SparkChatClient

client = SparkChatClient()
```

若不传参数，会自动从 `.env` 读取：

- `SPARKAI_APP_ID`  
- `SPARKAI_API_KEY`  
- `SPARKAI_API_SECRET`  
- `SPARKAI_1.5_URL`（WebSocket 地址）  
- `SPARKAI_DOMAIN`（模型 domain，例如 `"spark-x"`）  

缺少任何一个，都会抛出 `RuntimeError`，提醒你在 `.env` 中配置。

额外属性：

- `system_prompt`：固定为一条 system 消息，要求：
  - 简短中文回答  
  - 禁止自我介绍  
  - 禁止长篇解释、列清单、Markdown 等  

- `history`：对话历史列表，每个元素形如 `{"role": "user"/"assistant", "content": "..."}`  
- `timeout`：手动超时时间（秒）  
- `answer`：最近一次模型回复的累积字符串  

---

## 3. 核心内部方法

### 3.1 `_create_url()`

- 负责构建带鉴权的 WebSocket URL：  
  - 取出 host 和 path  
  - 生成 `date`（HTTP 时间）  
  - 拼接 `signature_origin = "host: ...
date: ...
GET ... HTTP/1.1"`  
  - 使用 `api_secret` 做 HMAC-SHA256 签名并 base64 编码  
  - 按星火要求构造 `authorization` 字符串，再 base64  
  - 最终将 `authorization`, `date`, `host` 作为 query 参数拼到 `self.url` 后面  

### 3.2 ` _on_open / _on_message / _on_error / _on_close`

这四个是传给 `websocket.WebSocketApp` 的回调：

- `_on_open`：在一个新线程中发送 payload，payload 来自 `_build_payload()`  
- `_on_message`：解析返回 JSON：
  - 检查 `header.code` 是否为 0，否则认为模型出错  
  - 从 `payload.choices.text[0].content` 取出文本追加到 `self.answer`  
  - 若 `status == 2`，代表流式输出结束，关闭连接  
- `_on_error`：记录错误并尝试关闭 WebSocket  
- `_on_close`：保持安静，不打印  

### 3.3 `_build_payload()`

构造发送给 Spark 的 payload：

```json
{
  "header": {"app_id": ...},
  "parameter": {
    "chat": {
      "domain": "...",
      "temperature": 0.1,
      "top_k": 1,
      "top_p": 0.2,
      "max_tokens": 1024
    }
  },
  "payload": {
    "message": {
      "text": [system_prompt, ...history]
    }
  }
}
```

这意味着每次调用 `chat()`：

- system prompt 固定放在最前，保证模型行为稳定  
- 后面是所有历史消息（含用户和助手），实现多轮对话  

### 3.4 `clear_history()`

- 清空 `self.history`，代表从头开始新一轮对话。

---

## 4. 对外入口：`chat(user_query: str) -> str`

完整流程：

1. 清空 `self.answer`  
2. 在用户问题后面附加一个约束：`"（请用一句话回答，禁止长篇解释。）"`  
3. 将 `{role: "user", content: user_query}` append 到 `self.history`  
4. 调用 `_create_url()` 生成带鉴权 URL  
5. 创建 `websocket.WebSocketApp(...)` 对象  
6. 手动超时控制：
   - 设置 `end_time = now + timeout`  
   - 在 `while True` 循环中调用 `ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})`  
   - 如果回调中把 `ws.keep_running` 设成 False，说明结束  
   - 若当前时间超过 `end_time`，则认为超时，记录日志并强制断开  
7. 若 `self.answer` 非空：
   - 将 `{"role": "assistant", "content": self.answer}` 追加到 `history`  
8. 返回 `self.answer.strip()`  

异常情况：

- 在任意环节出错时，记录日志并返回 `"调用失败，请查看日志。"`。

---

## 5. 使用示例

```python
from sparkchat import SparkChatClient

client = SparkChatClient()

resp = client.chat("帮我判断这句话是不是作业标题：Unit 3 课文背诵打卡")
print("模型回复：", resp)
```

可用于：

- 判断一条文本是不是有效的备注  
- 用一句话给出分类结果（比如“是/否”、“数学/英语/语文”等）  
- 配合 `download.py` 做“AI 目录归类”的决策辅助。

---

## 6. 总结

`sparkchat.py` 用较底层的 WebSocket 接法把星火模型封装成一个简单易用的 `chat()` 函数，同时在 System Prompt 层面强制约束“**短句中文回答、不啰嗦**”。

> 它非常适合做那种：**“我只想你简单回答一句结论，不要跟我解释一堆原理”** 的小任务。  
