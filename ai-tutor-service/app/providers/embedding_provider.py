"""
稠密向量嵌入适配层 (Dense Embedding Provider，基于 `litellm` 统一云端接口 + 本地 64 维有符号特征哈希兜底)。
- 配置 OpenAI 兼容或 Gemini API Key 时，经 `litellm.embedding` / `litellm.aembedding` 生成高维语义向量；
- `local` 离线模式或网络异常时，自动回退至确定性 64 维有符号特征哈希归一化向量 (Signed Feature Hashing)。
"""
import hashlib
import logging
import math
import re
from typing import Any
from app.core.config import settings

logger = logging.getLogger(__name__)

LOCAL_EMBEDDING_DIM = 64
_STOP_CHARS = set("的了在是我有和就不人都一上也很到说要去你会着没看好这那吗呢吧啊请问么什如何怎样为能可中与及或把被让给对从向")


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a)) or 1.0
    norm_b = math.sqrt(sum(b * b for b in vec_b)) or 1.0
    return max(0.0, dot / (norm_a * norm_b))


def _extract_weighted_features(text: str) -> list[tuple[str, float]]:
    lowered = (text or "").lower()
    features: list[tuple[str, float]] = []

    ascii_tokens = re.findall(r"[a-z0-9_+#.-]{2,}", lowered)
    for idx, tok in enumerate(ascii_tokens):
        features.append((f"w:{tok}", 2.8))
        if idx + 1 < len(ascii_tokens):
            features.append((f"wb:{tok}_{ascii_tokens[idx + 1]}", 1.8))

    cjk_chars = re.findall(r"[\u4e00-\u9fff]", lowered)
    for idx in range(len(cjk_chars) - 1):
        bg = cjk_chars[idx] + cjk_chars[idx + 1]
        if bg[0] in _STOP_CHARS or bg[1] in _STOP_CHARS:
            continue
        features.append((f"bg:{bg}", 1.8))

    if not features:
        for ch in cjk_chars:
            if ch not in _STOP_CHARS:
                features.append((f"ch:{ch}", 1.0))
    return features


def compute_local_feature_hash_vector(text: str, dim: int = LOCAL_EMBEDDING_DIM) -> list[float]:
    """基于 Weinberger 有符号特征哈希生成确定性 64 维 L2 归一化向量。"""
    vec = [0.0] * dim
    for feat, weight in _extract_weighted_features(text):
        digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if (digest[4] & 1) == 0 else -1.0
        vec[bucket] += sign * weight

    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 1e-12:
        return vec
    return [round(v / norm, 6) for v in vec]


class EmbeddingClient:
    def embed_local_hash(self, texts: list[str], dim: int = LOCAL_EMBEDDING_DIM) -> list[list[float]]:
        return [compute_local_feature_hash_vector(t, dim=dim) for t in texts]

    def _resolve_litellm_embedding_kwargs(self) -> dict[str, Any] | None:
        provider = settings.ai_provider.lower().strip()
        if provider == "openai" and settings.openai_api_key and settings.openai_embedding_model:
            model = settings.openai_embedding_model.strip()
            kwargs: dict[str, Any] = {
                "model": model if "/" in model else f"openai/{model}",
                "api_key": settings.openai_api_key,
                "timeout": 20.0,
            }
            if settings.openai_base_url:
                kwargs["api_base"] = settings.openai_base_url.rstrip("/")
            return kwargs

        if provider == "gemini" and settings.gemini_api_key and settings.gemini_embedding_model:
            model = settings.gemini_embedding_model.strip()
            return {
                "model": model if "/" in model else f"gemini/{model}",
                "api_key": settings.gemini_api_key,
                "timeout": 20.0,
            }
        return None

    def embed_texts_sync(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        kwargs = self._resolve_litellm_embedding_kwargs()
        if not kwargs:
            return self.embed_local_hash(texts)
        try:
            import litellm

            resp = litellm.embedding(input=texts, **kwargs)
            data = sorted(resp.data, key=lambda x: x["index"])
            return [item["embedding"] for item in data]
        except Exception as exc:
            logger.warning("Sync embedding degraded to local 64d feature-hash vector: %s", exc)
            return self.embed_local_hash(texts)

    async def embed_texts_async(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        kwargs = self._resolve_litellm_embedding_kwargs()
        if not kwargs:
            return self.embed_local_hash(texts)
        try:
            import litellm

            resp = await litellm.aembedding(input=texts, **kwargs)
            data = sorted(resp.data, key=lambda x: x["index"])
            return [item["embedding"] for item in data]
        except Exception as exc:
            logger.warning("Async embedding degraded to local 64d feature-hash vector: %s", exc)
            return self.embed_local_hash(texts)


embedding_client = EmbeddingClient()
