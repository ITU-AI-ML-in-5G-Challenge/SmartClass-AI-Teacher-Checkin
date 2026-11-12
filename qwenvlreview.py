"""
基于通义千问 Qwen-VL 的图片作业自动点评模块。

主要功能：
    - 加载 DashScope 多模态模型（Qwen-VL-Max）
    - 将作业图片转为 base64 发送给模型
    - 构造专业“初中英语老师”的点评 prompt
    - 生成 150 字以内的自然语言作业反馈
    - 内置自动重试、图片大小限制、异常处理

模块用途：
    - 微信打卡机器人用于点评学生提交的图片作业
    - 与 SparkImageReviewer 配合使用，实现双模型反馈
"""
import os
import time
import base64
import logging
import dashscope
from config import get_env

class QwenVLReviewer:
    """
    通义千问多模态（Qwen-VL-Max）点评器。

    主要职责：
        - 负责将图片转为 base64 并调用 DashScope API
        - 构造统一风格的点评 prompt
        - 生成自然语言点评内容（150 字以内）
        - 处理重试、超时、图片大小限制等异常情况

    :param api_key: DashScope API Key，若为空则从环境变量读取 ``DASHSCOPE_API_KEY``
    :type api_key: str or None
    :param logger: 日志对象
    :type logger: logging.Logger or None
    :param max_image_size: 图片最大允许字节数，默认 8MB
    :type max_image_size: int
    :param max_length: 输出最长字数限制（目前点评策略固定 150 字内）
    :type max_length: int
    :param timeout_seconds: API 超时秒数
    :type timeout_seconds: int
    """    
    def __init__(
        self,
        api_key=None,
        logger: logging.Logger = None,
        max_image_size: int = 8 * 1024 * 1024,
        max_length: int = 180,
        timeout_seconds: int = 20,
    ):
        """
        初始化 QwenVLReviewer，加载 API Key 与相关配置。

        :param api_key: API Key，若未传入则自动读取环境变量
        :type api_key: str or None
        :param logger: 自定义 logger，默认使用 logging.getLogger
        :type logger: logging.Logger or None
        :param max_image_size: 允许图片大小上限（字节）
        :type max_image_size: int
        :param max_length: 返回内容最大长度（字符）
        :type max_length: int
        :param timeout_seconds: 请求超时时间（秒）
        :type timeout_seconds: int
        """        
        # 优先使用传入的 key，否则去环境变量里拿
        self.api_key = api_key or get_env("DASHSCOPE_API_KEY", required=True)
        if not self.api_key:
            raise RuntimeError("DASHSCOPE_API_KEY 未设置，请在 .env 中配置")

        self.max_image_size = max_image_size
        self.max_length = max_length
        self.timeout_seconds = timeout_seconds

        self.logger = logger or logging.getLogger("QwenVLReviewer")

        dashscope.base_http_api_url = "https://dashscope.aliyuncs.com/api/v1"

    # -------------------------------------------------------------------------
    def _build_prompt(self):
        """
        构建发给 Qwen-VL 的点评提示词（prompt）。

        提示词要求：
            - 模拟初中英语女老师的语气
            - 内容温和但严格
            - 不超过 150 字
            - 对学生的作业进行自然语言点评（非列表）
            - 指出错误并给出改进建议
            - 保持人情味，不出现 AI 或系统描述

        :return: prompt 文本
        :rtype: str
        """        
        return """
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

    # -------------------------------------------------------------------------
    def review(self, image_path: str, retry: int = 2) -> str:
        """
        对指定的图片执行点评。

        流程：
            1. 检查图片大小
            2. 将图片读取为 base64
            3. 调用 `_review_once`
            4. 自动重试 retry 次
            5. 若所有重试失败，返回默认提示

        :param image_path: 图片路径
        :type image_path: str
        :param retry: 失败后重试次数
        :type retry: int
        :return: 点评文本（150 字内）
        :rtype: str
        """
        try:
            size = os.path.getsize(image_path)
            if size > self.max_image_size:
                return "【图片过大，请压缩后再试】"
        except Exception as e:
            self.logger.error(f"图片读取失败：{e}")
            return "【无法读取图片】"

        try:
            with open(image_path, "rb") as f:
                img_bytes = f.read()
                img_base64 = base64.b64encode(img_bytes).decode()
        except Exception as e:
            self.logger.error(f"读取失败：{e}")
            return "【无法读取图片】"

        for attempt in range(retry + 1):
            try:
                return self._review_once(img_base64)
            except Exception as e:
                self.logger.error(f"点评失败（第 {attempt+1} 次）：{e}")
                time.sleep(1)

        return "【点评服务暂不可用，请稍后再试】"

    # -------------------------------------------------------------------------
    def _review_once(self, img_base64: str) -> str:
        """
        对一张已编码为 base64 的图片执行一次点评请求。

        逻辑：
            - 拼接 data URI
            - 构造 prompt
            - 调用 DashScope MultiModalConversation 接口
            - 从返回内容中提取文本部分
            - 若解析失败 → 返回默认提示

        :param img_base64: 图片的 base64 字符串
        :type img_base64: str
        :return: 点评文本
        :rtype: str
        """
        data_uri = f"data:image/jpeg;base64,{img_base64}"

        prompt = self._build_prompt()

        messages = [
            {
                "role": "user",
                "content": [
                    {"image": data_uri},
                    {"text": prompt},
                ],
            }
        ]

        rsp = dashscope.MultiModalConversation.call(
            api_key=self.api_key,
            model="qwen-vl-max",
            messages=messages,
            timeout=self.timeout_seconds,
        )

        try:
            parts = rsp.output.choices[0].message.content
            text = "".join([item.get("text", "") for item in parts]).strip()
        except Exception:
            return "【未收到有效点评结果】"

        # 超长截断
        # if len(text) > self.max_length:
        #     text = text[: self.max_length - 1] + "…"

        return text
