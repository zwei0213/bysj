"""
基于开源统一网关库 `litellm` 的大模型调用适配层：
- 默认 `local` 模式：零外部依赖，离线可跑通全部功能与基准测试；
- `openai`（兼容 OpenAI / DeepSeek / 通义千问 / vLLM / Ollama）与 `gemini` 统一走 `litellm.acompletion`，
  自动获得重试、真实 Token 统计、多模型路由与原生流式 (`stream_text`) 能力；
- 远端异常时记录告警并优雅回退至本地确定性生成 (`degraded=True`)。
"""
from collections.abc import AsyncIterator
from dataclasses import dataclass
import json
import logging
import re
from typing import Any
from app.core.config import settings

logger = logging.getLogger(__name__)

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


@dataclass
class LlmCallResult:
    content: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    degraded: bool = False


class LlmProviderClient:
    def _resolve_litellm_params(self) -> tuple[str, dict[str, Any]] | None:
        provider = settings.ai_provider.lower().strip()
        if provider == "openai" and settings.openai_api_key:
            model = settings.openai_model.strip() or "gpt-4o-mini"
            model_id = model if "/" in model else f"openai/{model}"
            kwargs: dict[str, Any] = {
                "api_key": settings.openai_api_key,
                "timeout": settings.llm_timeout_seconds,
            }
            if settings.openai_base_url:
                kwargs["api_base"] = settings.openai_base_url.rstrip("/")
            return ("openai", {"model": model_id, **kwargs})

        if provider == "gemini" and settings.gemini_api_key:
            model = settings.gemini_model.strip() or "gemini-2.5-flash"
            model_id = model if "/" in model else f"gemini/{model}"
            return (
                "gemini",
                {
                    "model": model_id,
                    "api_key": settings.gemini_api_key,
                    "timeout": settings.llm_timeout_seconds,
                },
            )
        return None

    async def complete_text(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        fallback_text: str,
        temperature: float = 0.3,
    ) -> LlmCallResult:
        est_prompt_tokens = max(12, (len(system_prompt) + len(user_prompt)) // 2)
        resolved = self._resolve_litellm_params()
        if not resolved:
            return LlmCallResult(
                content=fallback_text,
                provider="local",
                model="local-hybrid-v1",
                prompt_tokens=est_prompt_tokens,
                completion_tokens=max(10, len(fallback_text) // 2),
                degraded=False,
            )

        provider_name, call_kwargs = resolved
        try:
            import litellm

            resp = await litellm.acompletion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                num_retries=1,
                **call_kwargs,
            )
            text = (resp.choices[0].message.content or "").strip()
            usage = getattr(resp, "usage", None)
            p_toks = getattr(usage, "prompt_tokens", None) or est_prompt_tokens
            c_toks = getattr(usage, "completion_tokens", None) or max(10, len(text) // 2)
            return LlmCallResult(
                content=text or fallback_text,
                provider=provider_name,
                model=call_kwargs["model"],
                prompt_tokens=int(p_toks),
                completion_tokens=int(c_toks),
                degraded=not bool(text),
            )
        except Exception as exc:
            logger.warning("LLM provider '%s' failed, degraded to local fallback: %s", provider_name, exc)
            return LlmCallResult(
                content=fallback_text,
                provider="local-fallback",
                model="local-deterministic-v1",
                prompt_tokens=est_prompt_tokens,
                completion_tokens=max(10, len(fallback_text) // 2),
                degraded=True,
            )

    async def stream_text(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        fallback_text: str,
        temperature: float = 0.3,
    ) -> AsyncIterator[str]:
        """流式生成文本切片（供 SSE 实时打字机问答使用）。"""
        resolved = self._resolve_litellm_params()
        if not resolved:
            yield fallback_text
            return

        provider_name, call_kwargs = resolved
        try:
            import litellm

            stream = await litellm.acompletion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                stream=True,
                **call_kwargs,
            )
            emitted = False
            async for chunk in stream:
                delta = getattr(chunk.choices[0].delta, "content", None) if chunk.choices else None
                if delta:
                    emitted = True
                    yield delta
            if not emitted:
                yield fallback_text
        except Exception as exc:
            logger.warning("LLM streaming '%s' failed, degraded to fallback: %s", provider_name, exc)
            yield fallback_text

    async def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        fallback_obj: Any,
    ) -> tuple[Any, LlmCallResult]:
        fallback_str = json.dumps(fallback_obj, ensure_ascii=False)
        res = await self.complete_text(system_prompt, user_prompt, fallback_text=fallback_str)
        if res.provider == "local":
            return fallback_obj, res
        try:
            cleaned = _CODE_FENCE_RE.sub("", res.content.strip()).strip()
            return json.loads(cleaned), res
        except Exception as exc:
            logger.warning("Failed to parse JSON from LLM response, using fallback: %s", exc)
            res.degraded = True
            return fallback_obj, res


llm_client = LlmProviderClient()
