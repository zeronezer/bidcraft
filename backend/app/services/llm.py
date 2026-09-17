"""LLM 客户端：阿里百炼 OpenAI 兼容端点（文本 + 视觉统一 qwen3.8-flash）。"""
import base64
import json
import time

import httpx

from app.core.config import settings

# 重试参数：连续调用触发限流/断连时自动重试（长任务健壮性）。
# 并发已提至 10（2026-09-10），退避基数加大 + 随机抖动，避免并发下同时重试打爆限流。
MAX_RETRIES = 5
RETRY_BASE_DELAY = 3.0  # 指数退避基数（秒）
RETRY_JITTER = 1.0      # 随机抖动 ±秒，防并发重试"惊群"


def _client(timeout: float = 300.0) -> httpx.Client:
    """统一走直连（trust_env=False），避免系统代理偶发 502 中断长任务。"""
    return httpx.Client(transport=httpx.HTTPTransport(trust_env=False), timeout=timeout, follow_redirects=True)


def _is_retryable(exc: Exception) -> bool:
    """判断是否值得重试：连接被断、超时、限流(429)、5xx。"""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    if isinstance(exc, (httpx.ReadError, httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadTimeout, httpx.ConnectTimeout)):
        return True
    return False


class LLMClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None, send_enable_thinking: bool = True):
        # 参数留空则回退全局配置（阿里百炼）；send_enable_thinking=False 用于
        # 不识别 enable_thinking 参数的 OpenAI 兼容端点（如 deepseek/其他代理）。
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.text_model
        self.send_enable_thinking = send_enable_thinking

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _post_with_retry(self, payload: dict, timeout: float, path: str = "/chat/completions") -> httpx.Response:
        """带指数退避重试的 POST，处理限流/断连。"""
        last_exc = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                with _client(timeout=timeout) as c:
                    resp = c.post(
                        f"{self.base_url}{path}",
                        headers=self._headers(),
                        json=payload,
                    )
                resp.raise_for_status()
                return resp
            except Exception as e:  # noqa: BLE001
                last_exc = e
                if attempt >= MAX_RETRIES or not _is_retryable(e):
                    raise
                import random as _random

                delay = RETRY_BASE_DELAY * (2 ** attempt) + _random.uniform(-RETRY_JITTER, RETRY_JITTER)
                delay = max(0.1, delay)
                time.sleep(delay)
        raise last_exc  # pragma: no cover

    def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        timeout: float = 300.0,
        enable_thinking: bool = False,
    ) -> str:
        """普通文本对话，返回 assistant 回复文本。

        messages: [{"role": "system"|"user"|"assistant", "content": str | list}]
        视觉消息 content 传 list：[{"type": "text", ...}, {"type": "image_url", ...}]
        enable_thinking：qwen3 系列默认开思考模式（reasoning tokens），
        结构化抽取/正文生成等任务默认关闭可提速约 3 倍、省 token。
        """
        payload: dict = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if self.send_enable_thinking:
            payload["enable_thinking"] = enable_thinking
        if max_tokens:
            payload["max_tokens"] = max_tokens
        resp = self._post_with_retry(payload, timeout)
        return resp.json()["choices"][0]["message"]["content"]

    def embed(self, texts: list[str], timeout: float = 120.0) -> list[list[float]]:
        raise NotImplementedError("本项目已移除向量库，无需文本向量化")

    def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str | None = None,
        temperature: float = 0.3,
        timeout: float = 120.0,
        enable_thinking: bool = False,
    ) -> dict:
        """带 Function Calling 的对话，返回 assistant 消息原文（含 tool_calls）。"""
        payload = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if self.send_enable_thinking:
            payload["enable_thinking"] = enable_thinking
        if tools:
            payload["tools"] = tools  # 空数组部分端点会报错，无工具时不传
        resp = self._post_with_retry(payload, timeout)
        return resp.json()["choices"][0]["message"]

    def chat_stream_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        sink: dict,
        model: str | None = None,
        temperature: float = 0.3,
        timeout: float = 300.0,
        enable_thinking: bool = False,
    ):
        """流式对话（带 Function Calling），逐段 yield 文本增量。

        流式解析 tool_calls 增量片段（OpenAI 分片协议：index/id/function.name/arguments）。
        事件：{"type": "content", "content": 文本增量}；工具调用不产出事件（由调用方
        决定如何提示）。生成结束后把完整 assistant 消息写入 sink["message"]
        （{"role","content","tool_calls"?}），调用方据此继续工具循环或收尾。
        """
        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": True,
            "max_tokens": 16384,  # 长表格/长正文需完整输出，防止中途截断
        }
        if self.send_enable_thinking:
            payload["enable_thinking"] = enable_thinking
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        msg: dict = {"role": "assistant", "content": ""}
        tc_buf: dict[int, dict] = {}
        with _client(timeout=timeout) as c, c.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=payload,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                choices = json.loads(data).get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                if delta.get("content"):
                    msg["content"] += delta["content"]
                    yield {"type": "content", "content": delta["content"]}
                for tcd in delta.get("tool_calls") or []:
                    idx = tcd.get("index", 0)
                    buf = tc_buf.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    if tcd.get("id"):
                        buf["id"] = tcd["id"]
                    fn = tcd.get("function") or {}
                    if fn.get("name"):
                        buf["name"] = fn["name"]
                    if fn.get("arguments"):
                        buf["arguments"] += fn["arguments"]

        if tc_buf:
            msg["tool_calls"] = [
                {
                    "id": buf["id"] or f"call_{i}",
                    "type": "function",
                    "function": {"name": buf["name"], "arguments": buf["arguments"]},
                }
                for i, buf in sorted(tc_buf.items())
            ]
        sink["message"] = msg

    def chat_with_image(self, prompt: str, image_bytes: bytes, mime: str = "image/png") -> str:
        """视觉理解：图片描述与要点提取（VISION_MODEL）。"""
        b64 = base64.b64encode(image_bytes).decode()
        return self.chat(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        },
                    ],
                }
            ],
            model=settings.vision_model,
        )

    def chat_stream(self, messages: list[dict], model: str | None = None, enable_thinking: bool = False):
        """流式对话（SSE 场景用），逐段 yield 文本增量。enable_thinking 默认关（提速省 token）。"""
        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": True,
        }
        if self.send_enable_thinking:
            payload["enable_thinking"] = enable_thinking
        with _client(timeout=600.0) as c, c.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=payload,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                choices = json.loads(data).get("choices") or []
                if not choices:
                    continue  # 部分 chunk 只含 usage 等元数据，无 choices
                delta = choices[0].get("delta") or {}
                if delta.get("content"):
                    yield delta["content"]


# 默认实例
llm = LLMClient()

# 对话 Agent 专用客户端：配了 CHAT_BASE_URL 就走独立端点（不发送 qwen 专属的
# enable_thinking 参数）；未配置则与主模型共用实例。
chat_llm = (
    LLMClient(settings.chat_base_url, settings.chat_api_key, settings.chat_model,
              send_enable_thinking=False)
    if settings.chat_base_url else llm
)
