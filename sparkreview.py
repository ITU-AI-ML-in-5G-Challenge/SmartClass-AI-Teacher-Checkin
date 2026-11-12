"""
Spark 图像点评模块（SparkImageReviewer）。

主要功能：
    - 检查图片大小、自动压缩为符合 Spark 限制的 base64
    - 构造 Spark 图像模型（imagev3）的 WebSocket payload
    - 调用 Spark 模型进行作业图片点评
    - 自动重试、超时控制、异常捕获
    - 提供压缩 PNG → JPEG 的智能转换（避免无损 PNG 导致体积过大）
    - 所有流程带日志输出，便于调试

适用场景：
    - 自动点评机器人（英语作业）
    - 需要按 Spark imagev3 模型点评的图像分类/评分任务
"""
import os
import time
import json
import base64
import hashlib
import hmac
import threading
import ssl
import logging
from urllib.parse import urlencode, urlparse
from wsgiref.handlers import format_date_time
from time import mktime
from config import get_env
from io import BytesIO
from PIL import Image
import websocket
from logger import get_logger

def is_base64_over_limit(image_path: str, limit_mb: int, logger: logging.Logger = None) -> bool:
    """
    判断图片的 Base64 大小是否超过指定 limit_mb。

    流程：
        - 读取原图 raw bytes
        - 转为 base64（约增加 1.33 倍）
        - 比较 base64 长度是否超过 limit_mb

    :param image_path: 图片路径
    :type image_path: str
    :param limit_mb: 限制大小（MB）
    :type limit_mb: int
    :param logger: 日志对象
    :type logger: logging.Logger or None
    :return: True 表示超过限制，False 表示未超过
    :rtype: bool
    """
    limit_bytes = limit_mb * 1024 * 1024  # MB → Byte

    # 读取文件
    with open(image_path, "rb") as f:
        raw = f.read()

    # base64 后的大小（注意：base64 会变大约 1.33 倍）
    b64 = base64.b64encode(raw)
    b64_size = len(b64)

    if logger:
        logger.info(f"原文件大小：{len(raw)/1024:.1f} KB")
        logger.info(f"Base64大小：{b64_size/1024:.1f} KB")

    return b64_size > limit_bytes

def compress_image_to_base64(image_path: str, limit_mb: int, logger: logging.Logger = None) -> str:
    """
    将图片压缩到符合 base64 大小限制以内，并返回压缩后的 base64 字符串。

    特性：
        - PNG 自动转 JPG（避免无损过大）
        - JPEG 质量逐步下降（从 95 降至最小 20）
        - 降至最低质量仍超限 → 返回最小质量版本

    :param image_path: 原图片路径
    :type image_path: str
    :param limit_mb: 限制大小（MB）
    :type limit_mb: int
    :param logger: 日志对象
    :type logger: logging.Logger or None
    :return: base64 字符串
    :rtype: str
    """
    limit_bytes = limit_mb * 1024 * 1024  # MB → Byte

    # 打开图片
    img = Image.open(image_path)

    # PNG 转 JPG（PNG 无损，无法有效降低大小）
    if img.format == "PNG":
        img = img.convert("RGB")

    quality = 95  # 初始质量
    min_quality = 20  # 最低不能低于 20，否则太糊

    while True:
        # 把图片存入内存
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=quality)
        raw = buffer.getvalue()

        # 转 base64
        b64 = base64.b64encode(raw)
        size = len(b64)

        if logger:
            logger.info(f"当前质量: {quality}, Base64 大小: {size/1024:.1f} KB")

        # 如果小于限制，直接返回
        if size <= limit_bytes:
            if logger:
                logger.info("压缩成功，已低于限制")
            return b64.decode()

        # 如果已经很低还不行，只能返回当前压缩
        if quality <= min_quality:
            if logger:
                logger.warning("最低质量也压不动了，返回当前最小版本")
            return b64.decode()

        # 否则继续降低质量
        quality -= 5

def get_safe_base64(image_path: str, limit_mb: int, logger: logging.Logger = None) -> str:
    """
    返回一个 *保证* 大小不超过 limit_mb 的 base64 字符串。

    逻辑：
        - 先尝试直接读取原图 base64
        - 若原图 base64 已超过限制 → 自动压缩
        - 始终返回符合大小限制的 base64

    :param image_path: 图片路径
    :type image_path: str
    :param limit_mb: 限制大小（MB）
    :type limit_mb: int
    :param logger: 日志对象
    :return: base64 字符串
    :rtype: str
    """
    limit_mb = int(limit_mb)
    # 先尝试直接读
    with open(image_path, "rb") as f:
        raw = f.read()

    # 原始 base64
    b64 = base64.b64encode(raw)
    size = len(b64)

    if logger:
        logger.info(f"原始 Base64 大小：{size/1024:.1f} KB（限制 {limit_mb} MB）")

    # 不超过限制，直接返回
    if size <= limit_mb * 1024 * 1024:
        if logger:
            logger.info("图片未超过限制，直接使用原图")
        return b64.decode()

    # 超过 → 自动压缩
    if logger:
        logger.warning("图片 Base64 超过限制，自动压缩中…")

    return compress_image_to_base64(image_path, limit_mb, logger=logger)


class SparkImageReviewer:
    """
    Spark 图像点评客户端（WebSocket）。

    功能：
        - 调用 Spark imagev3 模型对图片进行作业点评
        - 自动处理图片大小、压缩、base64 转换
        - 构造 WebSocket URL（含 HMAC-SHA256 鉴权）
        - 处理模型流式返回内容
        - 自动重试、超时、异常保护

    配置：
        - app_id / api_key / api_secret / url 从 .env 获取或构造时传入
        - max_image_size：文件最大允许大小（字节）
        - max_image_base64_limit_mb：Spark 官方 base64 限制（一般为 4MB）
        - timeout_seconds：WebSocket 超时
        - max_length：点评文本长度（模型端已限制）

    :param app_id: Spark 应用 ID
    :param api_key: API Key
    :param api_secret: API Secret
    :param url: Spark WebSocket URL
    :param logger: 日志器
    :param timeout_seconds: 超时时间（秒）
    :param max_image_size: 原始文件大小限制（bytes）
    :param max_length: 点评文本最大长度
    """    
    def __init__(
        self,
        app_id=None,
        api_key=None,
        api_secret=None,
        url=None,
        logger: logging.Logger = None,
        timeout_seconds: int = 25,
        max_image_size: int = 8 * 1024 * 1024,
        max_length: int = 150,
    ):
        """
        初始化 SparkImageReviewer。

        动作：
            - 读取 app_id / api_key / api_secret / url
            - 从 .env 获取 SPARKAI_IMGBASE64_LIMIT_MB（默认 4MB）
            - 创建 logger
            - 设置最大图像大小与超时时间

        :param app_id: 应用 ID
        :param api_key: API Key
        :param api_secret: API Secret
        :param url: Spark WebSocket URL
        :param logger: 日志器
        :param timeout_seconds: 超时秒数
        :param max_image_size: 原始图片大小限制
        :param max_length: 点评文本长度最大值
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
        self.url = url or get_env("SPARKAI_URL", required=True)
        if not self.url:
            raise RuntimeError("SPARKAI_URL 未设置，请在 .env 中配置")
        
        # 一些Spark里的限制
        raw = get_env("SPARKAI_IMGBASE64_LIMIT_MB")
        self.max_image_base64_limit_mb =  int(raw) if raw else 4

        self.timeout_seconds = timeout_seconds
        self.max_image_size = max_image_size
        self.max_length = max_length

        self.logger = logger or logging.getLogger("SparkImageReviewer")

    def _create_url(self):
        """
        基于 HMAC-SHA256 构建带鉴权签名的 WebSocket URL。

        包含：
            - host / date / request-line
            - Base64(HMAC-SHA256(SignatureOrigin))
            - 完整拼接 authorization / date / host 到 query 参数

        :return: 完整 WebSocket URL
        :rtype: str
        """        
        try:
            host = urlparse(self.url).netloc
            path = urlparse(self.url).path

            # HTTP 时间戳
            http_date = format_date_time(mktime(time.localtime()))

            # 信息串
            signature_origin = (
                f"host: {host}\n"
                f"date: {http_date}\n"
                f"GET {path} HTTP/1.1"
            )

            # HMAC-SHA256 签名
            signature_sha = hmac.new(
                self.api_secret.encode("utf-8"),
                signature_origin.encode("utf-8"),
                digestmod=hashlib.sha256,
            ).digest()

            signature_base64 = base64.b64encode(signature_sha).decode()

            # authorization 构造
            authorization_origin = (
                f'api_key="{self.api_key}", algorithm="hmac-sha256", '
                f'headers="host date request-line", signature="{signature_base64}"'
            )
            authorization = base64.b64encode(authorization_origin.encode()).decode()

            params = {
                "authorization": authorization,
                "date": http_date,
                "host": host,
            }

            return self.url + "?" + urlencode(params)

        except Exception as e:
            self.logger.error(f"生成 WebSocket URL 失败：{e}")
            raise


    def _gen_payload(self, img_base64: str):
        """
        构造 Spark imagev3 模型的 payload。

        内容包含：
            - 图片：content_type="image"
            - 统一的英语老师角色 prompt：content_type="text"
            - 模型参数：temperature、top_k、max_tokens 等

        :param img_base64: 图片 base64 字符串
        :type img_base64: str
        :return: payload 字典
        :rtype: dict
        """        
        prompt = """
您是一位年轻漂亮的初中英语女老师，一方面对学生和蔼可亲，另一方面对学生严格要求。
你是一个喜欢用短句表达的老师，你的回答始终简洁、克制、150字以内。
下面是一位学生提交的作业图片，请你先识别图片中的题目与学生作答，根据你已有的知识对学生作业
进行判分，并给出以下对学生的反馈和评语，请严格输出一段150字以内的内容，反馈评语要求是：
- 有对学生本次作业完成情况的总体评价，使用自然语言，不要列点和列表
- 指出明显的错误或不完善之处，但语气依然温和
- 写一段鼓励性反馈，帮助学生改进打卡习惯、学习方法、书法，也可以是打卡态度等任何方面
- 语气像真正老师一样有温度，不要出现“模型”“AI”“程序”等描述
- 回答不能超过150字，否则会被系统自动截断。
你写评语的时候，可以引用学生作业里的原句，但不要机械性的反馈1234，而是要一气呵成说一段话。
要保持这段话完整，不要为了字数限制故意截断，不要讲套话，针对学生作业点评稍微有点深度，不要千遍一律。
语气方面，有的时候可以活泼调皮一些，有的时候可以是严肃一点，也可以增加一些学习鸡汤鼓励。
"""

        return {
            "header": {"app_id": self.app_id},
            "parameter": {
                "chat": {
                    "domain": "imagev3",
                    "temperature": 0.3,
                    "top_k": 2,
                    "max_tokens": 1500,
                }
            },
            "payload": {
                "message": {
                    "text": [
                        {"role": "user", "content": img_base64, "content_type": "image"},
                        {"role": "user", "content": prompt, "content_type": "text"},
                    ]
                }
            },
        }


    def review(self, image_path: str, retry: int = 2) -> str:
        """
        对一张图片执行点评（自动压缩 + 多次重试）。

        流程：
            1. 检查文件大小（字节）
            2. 获取符合限制的 base64（自动压缩）
            3. 进行最多 retry+1 次点评请求
            4. 所有尝试失败 → 返回提示文本

        :param image_path: 图片路径
        :type image_path: str
        :param retry: 失败重试次数
        :type retry: int
        :return: 点评文本
        :rtype: str
        """        
        # ---- 图片文件检查 ----
        try:
            size = os.path.getsize(image_path)
            if size > self.max_image_size:
                return "【图片太大，请压缩后重新发送】"

            img_base64 = get_safe_base64(image_path, self.max_image_base64_limit_mb, self.logger)
        except Exception as e:
            self.logger.error(f"图片读取失败：{e}")
            return "【无法读取图片，请重新发送】"

        # ---- 自动重试逻辑 ----
        for attempt in range(retry + 1):
            try:
                return self._review_once(img_base64)
            except Exception as e:
                self.logger.error(f"点评失败（第 {attempt + 1} 次）：{e}")
                time.sleep(1)

        return "【点评服务暂时不可用，请稍后再试】"


    def _review_once(self, img_base64: str) -> str:
        """
        发起一次 WebSocket 点评请求（不自动重试）。

        流程：
            - 创建 ws_url
            - 构造四个回调：on_open/on_message/on_error/on_close
            - 独立线程运行 WebSocketApp
            - timeout_seconds 内等待 thread.join()
            - 若超时 → 关闭连接并返回提示文本
            - 汇总所有返回的 content 文本
            - 返回最终点评内容

        :param img_base64: base64 字符串
        :type img_base64: str
        :return: 点评结果
        :rtype: str
        """        
        ws_url = self._create_url()
        final_answer = []

        # ---------------------------------------------------------------------
        # WebSocket 回调函数（全部带 try/except，防止崩溃）
        # ---------------------------------------------------------------------

        def on_message(ws, message):
            try:
                data = json.loads(message)

                # 若 code != 0，表示调用失败
                if data.get("header", {}).get("code") != 0:
                    self.logger.error(f"模型返回错误：{data}")
                    ws.close()
                    return

                # 提取内容
                content = (
                    data.get("payload", {})
                    .get("choices", {})
                    .get("text", [{}])[0]
                    .get("content", "")
                )
                final_answer.append(content)

                # status=2 表示结束
                if data.get("payload", {}).get("choices", {}).get("status") == 2:
                    ws.close()

            except Exception as e:
                self.logger.error(f"解析模型返回失败：{e}")
                ws.close()

        def on_error(ws, error):
            self.logger.error(f"WebSocket 错误：{error}")

        def on_close(ws, *args):
            self.logger.info("WebSocket 已关闭。")

        def on_open(ws):
            def run():
                try:
                    payload = json.dumps(self._gen_payload(img_base64))
                    ws.send(payload)
                except Exception as e:
                    self.logger.error(f"发送 payload 失败：{e}")
                    ws.close()

            threading.Thread(target=run).start()

        # ---------------------------------------------------------------------
        # 启动 WebSocket（使用线程 + 超时保护）
        # ---------------------------------------------------------------------

        ws = websocket.WebSocketApp(
            ws_url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )

        thread = threading.Thread(
            target=lambda: ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})
        )
        thread.daemon = True
        thread.start()

        # 超时等待
        thread.join(self.timeout_seconds)
        if thread.is_alive():
            self.logger.warning("WebSocket 超时，正在关闭连接…")
            try:
                ws.close()
            except:
                pass
            return "【点评超时，请稍后再试】"

        # ---------------------------------------------------------------------
        # 返回文本处理
        # ---------------------------------------------------------------------
        text = "".join(final_answer).strip()
        if not text:
            return "【未收到点评内容，请稍后再试】"

        # 超长截断
        # if len(text) > self.max_length:
        #     text = text[: self.max_length - 1] + "…"

        return text