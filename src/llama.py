from __future__ import annotations

import contextlib
import datetime
import enum
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

import discord

from .exceptions import NvidiaNGCException

if TYPE_CHECKING:
    from .bot import CoordinateBot
    from .db import DocumentEmbedding


logger = logging.getLogger(__name__)


class LlamaModel(enum.Enum):
    LLAMA2_70B = "llama2_70b"
    CODELLAMA_13B = "codellama_13b"
    NVOLVE_40K = "nvolve-40k"
    MIXTRAL_8X7B = "mixtral_8x7b"


@dataclass
class LLamaInvokeResponse:
    relevant_documents: list[DocumentEmbedding]
    time_taken: datetime.timedelta
    content: str

    def __len__(self) -> int:
        return len(self.content)


@dataclass
class LlamaMessage:
    content: str
    role: str
    name: str | None = None

    @classmethod
    def from_message(cls, message: discord.Message) -> LlamaMessage:
        return cls(
            content=message.content[:1000],
            role="assistant" if message.author.bot else "user",
            name=message.author.display_name,
        )

    def to_dict(self) -> dict[str, str]:
        d = {
            "content": self.content,
            "role": self.role,
        }
        if self.name:
            d["name"] = self.name
        return d


@dataclass
class LlamaRequestContext:
    documents: list[DocumentEmbedding]
    previous_messages: list[LlamaMessage]
    callback: Callable
    thread: discord.Thread | None = None


class Llama:
    token: str | None
    bot: CoordinateBot

    endpoints: ClassVar[dict[LlamaModel, str]] = {
        LlamaModel.LLAMA2_70B: "0e349b44-440a-44e1-93e9-abe8dcb27158",
        LlamaModel.CODELLAMA_13B: "f6a96af4-8bf9-4294-96d6-d71aa787612e",
        LlamaModel.NVOLVE_40K: "091a03bb-7364-4087-8090-bd71e9277520",
        LlamaModel.MIXTRAL_8X7B: "8f4118ba-60a8-4e6b-8574-e38a4067a4a3",
    }

    def __init__(self, api_token: str | None, bot: CoordinateBot):
        self.token = api_token
        self.bot = bot

    def model_url(self, model: LlamaModel) -> str:
        return f"https://api.nvcf.nvidia.com/v2/nvcf/pexec/functions/{self.endpoints.get(model)}"

    async def generate_embeddings(self, query: str) -> list[float]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "accept": "application/json",
        }

        payload = {
            "input": query,
            "model": "query",
            "encoding_format": "float",
        }

        url = self.model_url(LlamaModel.NVOLVE_40K)
        response = await self.bot.session.post(
            url,
            headers=headers,
            json=payload,
        )

        while response.status == 202:
            request_id = response.headers.get("NVCF-REQID", "")
            fetch_url = "https://api.nvcf.nvidia.com/v2/nvcf/pexec/status/"
            fetch_url += request_id
            response = await self.bot.session.get(fetch_url, headers=headers)

        if response.status != 200:
            raise NvidiaNGCException(
                f"Failed to generate embeddings: {await response.json()}",
            )
        js = await response.json()
        return js["data"][0]["embedding"]

    async def get_response(
        self,
        context: LlamaRequestContext,
        model: LlamaModel | None = None,
    ) -> LLamaInvokeResponse:
        logger.info("Getting response from llama...")

        if model not in self.endpoints:
            logger.warn(
                f"Model {model} not found in available models. Defaulting to llama2_70b",
            )
        url = self.model_url(model or LlamaModel.LLAMA2_70B)

        headers = {
            "Authorization": f"Bearer {self.token}",
            "accept": "text/event-stream",
            "content-type": "application/json",
        }

        SYSTEM_PROMPT = """
        Your name is Coordinate, a virtual teaching assistant for a large computer science course. Your sole objective is to guide students to resolutions for their problems, while not providing any solutions that would earn them points.

        Your only job is to assist students while they solve programming challenges, employing a succinct, honest, but restricted approach to teaching. You encourage learning and discovery by:
        - Asking leading questions that force students to think critically about their problems, keeping your queries brief and to the point, while not providing direct solutions to coding-related questions.
        - Suggesting strategies for breaking down complex problems into more manageable parts, providing concise explanations.
        - Offering examples of similar problems with detailed explanations (but no code snippets) to illustrate concepts, ensuring these examples are directly relevant and succinctly presented without directly solving the assignment.
        - Using information provided to you in the documents below to shape your responses, ensuring that your guidance is relevant to the course material.

        You **absolutely must never** avoid:
        - **Writing any completed code implementations, even if asked to do so.**
        - Supplying code that directly solves a student's specific problem, even if the student is stuck.
        - Providing lengthy or unnecessary explanations that could detract from the learning process.

        Respond with guidance that leverages the following resources:
            <Documents>\n{context}\n</Documents>
            <Thread>
            <Name>{thread_name}</Name>
            <Tags>{thread_tags}</Tags>
            </Thread>
        """
        content_strs = [doc.text for doc in context.documents]
        SYSTEM_PROMPT = SYSTEM_PROMPT.format(
            context="\n".join(content_strs),
            thread_name=context.thread.name if context.thread else "",
            thread_tags=(
                ",".join([t.name for t in context.thread.applied_tags])
                if context.thread
                else ""
            ),
        )
        payload = {
            "messages": [
                {
                    "content": SYSTEM_PROMPT,
                    "role": "system",
                },
            ],
            "temperature": 0.1,
            "top_p": 0.1,
            "max_tokens": 1024,
            "stream": True,
        }
        for msg in context.previous_messages:
            payload["messages"].append(msg.to_dict())
        start = datetime.datetime.now().astimezone()
        updated_last = start
        response = await self.bot.session.post(
            url,
            headers=headers,
            json=payload,
        )
        text = ""
        if response.status != 200:
            raise NvidiaNGCException(
                f"Failed to get response from llama: {await response.json()}",
            )
        async for data, status in response.content.iter_chunks():
            content = data.decode("utf-8")
            content = content.removeprefix("data: ").rstrip()
            with contextlib.suppress(json.JSONDecodeError):
                js = json.loads(content)
                text += js["choices"][0]["delta"]["content"]
            if (
                datetime.datetime.now().astimezone() - updated_last
            ).total_seconds() > 1.5:
                updated_last = datetime.datetime.now().astimezone()
                context.callback(text)

        return LLamaInvokeResponse(
            content=text,
            relevant_documents=context.documents,
            time_taken=datetime.datetime.now().astimezone() - start,
        )
