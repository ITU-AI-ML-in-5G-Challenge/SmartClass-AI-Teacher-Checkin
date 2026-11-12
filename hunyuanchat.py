"""
腾讯混元 Responses API 封装（推荐版）

特点：
    - 使用 Responses API（官方推荐）
    - 服务端维护上下文
    - chat() 接口与 SparkChatClient 保持一致
    - clear_history() 开启新会话
    - 从 .env 自动读取 API Key

依赖：

pip install -U openai python-dotenv
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI
from config import get_env

class HunyuanChatClient:

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "hy3-preview",
        timeout: int = 30,
        logger: Optional[logging.Logger] = None,
    ):

        load_dotenv()

        self.api_key = api_key or get_env(
            "TENCENT_TOKENHUB_API_KEY",
            required=True,
        )

        self.model = model

        self.logger = logger or logging.getLogger("HunyuanChatClient")

        self.client = OpenAI(
            api_key=self.api_key,
            base_url="https://tokenhub.tencentmaas.com/v1",
            timeout=timeout,
        )

        self.system_prompt = (
            "你必须用简短、直接、扼要中文汉语的方式回答问题。"
            "不允许自我介绍。"
            "不允许长篇解释。"
            "不允许使用Markdown格式。"
            "回答用一句话或两句话即可。"
            "不允许列清单"
        )

        #
        # Responses API 的上下文ID
        #
        self.previous_response_id = None

    def clear_history(self):
        """
        开启新会话
        """

        self.previous_response_id = None

        self.logger.info("🆕 已清空对话历史，开始新的对话轮次。")

    def chat(self, user_query: str) -> str:

        try:

            kwargs = {
                "model": self.model,
                "instructions": self.system_prompt,
                "input": user_query,
                "stream": False,
            }

            #
            # 第二轮开始自动带上下文
            #
            if self.previous_response_id is not None:
                kwargs["previous_response_id"] = self.previous_response_id

            response = self.client.responses.create(**kwargs)

            #
            # 保存上下文ID
            #
            self.previous_response_id = response.id

            return response.output_text.strip()

        except Exception as e:
            self.logger.error(f"SDK 调用失败: {e}")
            return "调用失败，请查看日志。"

# ---------------------------
# 用法示例（本地跑一跑）
# ---------------------------
if __name__ == "__main__":
     client = HunyuanChatClient(
         timeout=30
     )
     print(client.chat("1+1=?"))
     print(client.chat("那2+2呢？（测试多轮）"))