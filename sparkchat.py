"""
讯飞星火 Spark API（WebSocket）对话调用模块。

主要功能：
    - 使用 WebSocket 调用 Spark v1.5 / spark-x 等对话模型
    - 自动带 HMAC-SHA256 鉴权
    - 支持多轮对话：内部维护 history
    - 自动构建 system prompt（强制限制回答风格）
    - 带手动超时控制（兼容 websocket-client 老版本）
    - 自动处理流式返回的文本内容
    - 提供 chat() 作为唯一对外调用入口

适用场景：
    - 下载程序中 AI 分类目录
    - 自动点评机器人中对文本的快速判断
    - 任何需要 Spark API 的轻量对话任务
"""
import json
import base64
import hashlib
import hmac
import ssl
import logging
import websocket
from urllib.parse import urlparse, urlencode
from wsgiref.handlers import format_date_time
from time import mktime
from datetime import datetime, timedelta
import _thread as thread
from config import get_env

class SparkChatClient:
    """
    Spark Chat 对话封装客户端（基于 WebSocket）。

    特性：
        - HMAC-SHA256 鉴权
        - WebSocket 流式响应
        - 自动追加 system prompt 控制行为
        - 内置多轮对话 history
        - 全流程异常捕获、自动关闭连接
        - 手动超时机制（避免 run_forever 阻塞）

    :param app_id: Spark 应用 ID
    :type app_id: str or None
    :param api_key: API Key
    :type api_key: str or None
    :param api_secret: API Secret  
    :type api_secret: str or None
    :param url: WebSocket 接口 URL
    :type url: str or None
    :param domain: 模型 domain，默认 "spark-x"
    :type domain: str
    :param timeout: 超时时间（秒）
    :type timeout: int
    :param logger: 可选 logger
    :type logger: logging.Logger or None
    """
    def __init__(self, app_id=None, api_key=None, api_secret=None, url=None,
                 domain="spark-x", timeout=30, logger: logging.Logger = None):
        """
        初始化 SparkChatClient。

        动作：
            - 读取 app_id / api_key / api_secret / url
            - 载入 system prompt（强制简短中文回答）
            - 初始化多轮对话 history
            - 初始化 logger

        :param app_id: 应用 ID
        :type app_id: str or None
        :param api_key: API Key
        :type api_key: str or None
        :param api_secret: API Secret
        :type api_secret: str or None
        :param url: WebSocket URL
        :type url: str or None
        :param domain: 模型 domain
        :type domain: str
        :param timeout: 超时时间（秒）
        :type timeout: int
        :param logger: 日志对象
        :type logger: logging.Logger or None
        """
        # 优先使用传入的 key，否则去环境变量里拿       
        self.app_id = app_id or get_env("SPARKAI_APP_ID", required=True)
        if not self.app_id:
            raise RuntimeError("SPARKAI_APP_ID 未设置，请在 .env 中配置")
        self.api_key = api_key or get_env("SPARKAI_API_KEY", required=True)
        if not self.api_key:
            raise RuntimeError("SPARKAI_API_KEY 未设置，请在 .env 中配置")
        self.api_secret = api_secret or get_env("SPARKAI_API_SECRET", required=True)
        if not self.api_secret:
            raise RuntimeError("SPARKAI_API_SECRET 未设置，请在 .env 中配置")
        self.url = url or get_env("SPARKAI_1.5_URL", required=True)
        if not self.url:
            raise RuntimeError("SPARKAI_1.5_URL 未设置，请在 .env 中配置")
        self.domain = domain or get_env("SPARKAI_DOMAIN", required=True)
        if not self.domain:
            raise RuntimeError("SPARKAI_DOMAIN 未设置，请在 .env 中配置")

        # 新增：加入强制风格控制 System Prompt
        self.system_prompt = {
            "role": "system",
            "content": "你必须用简短、直接、扼要中文汉语的方式回答问题。不允许自我介绍、不允许长篇解释、不允许列清单、不允许使用Markdown格式。回答用一句话或两句话即可。"
        }

        self.timeout = timeout

        # 多轮对话历史
        self.history = []

        # logger
        self.logger = logger or logging.getLogger("SparkChatClient")

        # 模型返回的内容
        self.answer = ""


    # ========================= 鉴权 URL =========================
    def _create_url(self):
        """
        构建带鉴权参数的 WebSocket URL。

        动作：
            - 根据 host / date / request-line 生成 HMAC-SHA256 签名
            - 将 signature / date / host 作为 query 参数加入 URL

        :return: 完整带鉴权的 URL
        :rtype: str
        """        
        host = urlparse(self.url).netloc
        path = urlparse(self.url).path

        date = format_date_time(mktime(datetime.now().timetuple()))

        signature_origin = f"host: {host}\n" \
                           f"date: {date}\n" \
                           f"GET {path} HTTP/1.1"

        signature_sha = hmac.new(
            self.api_secret.encode(),
            signature_origin.encode(),
            hashlib.sha256
        ).digest()

        signature = base64.b64encode(signature_sha).decode()
        authorization_origin = (
            f'api_key="{self.api_key}", algorithm="hmac-sha256", '
            f'headers="host date request-line", signature="{signature}"'
        )
        authorization = base64.b64encode(authorization_origin.encode()).decode()

        url = self.url + "?" + urlencode({
            "authorization": authorization,
            "date": date,
            "host": host
        })

        return url


    # ========================= WebSocket 回调 =========================
    def _on_open(self, ws):
        """
        WebSocket 打开回调。

        作用：
            - 异步发送 payload（使用 thread.start_new_thread）
            - 发送失败自动关闭连接

        :param ws: WebSocketApp 实例
        :type ws: websocket.WebSocketApp
        """        
        def run(*args):
            try:
                payload = json.dumps(self._build_payload())
                ws.send(payload)
            except Exception as e:
                self.logger.error(f"发送失败: {e}")
                ws.keep_running = False
                try:
                    ws.close()
                except:
                    pass

        thread.start_new_thread(run, ())

    def _on_message(self, ws, message):
        """
        接收 WebSocket 返回的消息。

        流程：
            - 解析 JSON
            - 检查 header.code 是否为 0
            - 取出 payload.choices.text 的内容增量
            - 若 status == 2 表示结束 → 关闭连接

        :param ws: WebSocketApp 实例
        :param message: WebSocket 文本消息
        :type message: str
        """        
        try:
            data = json.loads(message)
            if data["header"]["code"] != 0:
                self.logger.error(f"模型错误: {data}")
                ws.keep_running = False
                ws.close()
                return

            text = data["payload"]["choices"]["text"][0]
            content = text.get("content", "")
            if content:
                self.answer += content

            if data["payload"]["choices"]["status"] == 2:
                ws.keep_running = False
                ws.close()

        except Exception as e:
            self.logger.error(f"解析失败: {e}")
            ws.keep_running = False
            try:
                ws.close()
            except:
                pass

    def _on_error(self, ws, error):
        """
        WebSocket 出错时回调。

        :param ws: WebSocketApp
        :param error: 异常对象或错误信息
        """        
        self.logger.error(f"WebSocket 错误: {error}")
        try:
            ws.close()
        except:
            pass

    def _on_close(self, ws, *args):
        """
        WebSocket 关闭时回调。

        :param ws: WebSocketApp 实例
        :param args: 其他参数（不使用）
        """        
        pass  # 不输出任何东西


    # ========================= 构建 payload =========================
    def _build_payload(self):
        """
        构建发送给 Spark 的对话 payload。

        包含：
            - header：包含 app_id
            - parameter.chat：包含 domain, temperature, top_k 等参数
            - payload.message.text：包含 system_prompt + history

        :return: payload 字典
        :rtype: dict
        """        
        return {
            "header": {
                "app_id": self.app_id,
                "uid": "user_001",
            },
            "parameter": {
                "chat": {
                    "domain": self.domain,
                    "temperature": 0.1,
                    "top_k": 1,
                    "top_p": 0.2,
                    "max_tokens": 1024
                }
            },
            "payload": {
                "message": {
                    "text": [self.system_prompt] + self.history
                }
            }
        }

    def clear_history(self):
        """
        清空多轮对话历史。

        常用于下一次分类/点评之前重置状态。

        :return: None
        """
        try:
            self.history = []
            self.logger.info("🆕 已清空对话历史，开始新的对话轮次。")
        except Exception as e:
            self.logger.error(f"清空对话历史失败: {e}")


    # ========================= 对外唯一入口 =========================
    def chat(self, user_query: str) -> str:
        """
        执行一次对话请求（多轮上下文生效）。

        流程：
            1. 追加用户消息到 history
            2. 构建鉴权 URL
            3. 初始化 WebSocketApp
            4. 手动超时循环 + run_forever()
            5. 将模型输出写入 answer
            6. 将 assistant 内容加入 history
            7. 返回最终回答文本

        特点：
            - 自动带 system prompt 约束风格
            - 超时后自动终止 WebSocket
            - 所有异常捕获、打印日志并返回 fallback 文本

        :param user_query: 用户查询文本
        :type user_query: str
        :return: 模型输出文本
        :rtype: str
        """
        self.answer = ""
        user_query = user_query + "（请用一句话回答，禁止长篇解释。）"

        # 添加用户消息
        self.history.append({"role": "user", "content": user_query})

        try:
            ws_url = self._create_url()

            ws = websocket.WebSocketApp(
                ws_url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close
            )

            # ---------------------
            #  手动超时机制（替代 timeout 参数）
            # ---------------------
            end_time = datetime.now() + timedelta(seconds=self.timeout)

            while True:
                # 旧版 websocket-client 不支持 timeout= 参数，这里只传 sslopt
                ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})

                # 当 _on_message 或 _on_error 中把 keep_running 设为 False 时退出
                if not ws.keep_running:
                    break

                # 超时判断
                if datetime.now() > end_time:
                    self.logger.error(f"⏰ 超时（>{self.timeout}s），已中断 WebSocket。")
                    ws.keep_running = False
                    break

            # 写入历史（多轮对话）
            if self.answer:
                self.history.append({"role": "assistant", "content": self.answer})

            return self.answer.strip()

        except Exception as e:
            self.logger.error(f"chat 调用失败: {e}")
            return "调用失败，请查看日志。"


# def main():
#     client = SparkChatClient()
#     a = client.chat("1+1=?")
#     print(a)

# if __name__ == "__main__":
#     main()