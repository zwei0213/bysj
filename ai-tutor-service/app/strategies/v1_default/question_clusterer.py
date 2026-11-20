from collections import defaultdict
import re
import time
from typing import Any
from app.services.telemetry_store import telemetry_store

_STOPWORDS = {"请问", "什么", "怎么", "如何", "为什么", "一下", "老师", "助教", "这个", "那个", "可以", "能不", "讲解", "意思", "这里"}


class V1QuestionHotspotClusterer:
    """
    V1 学情看板提问热点聚类策略：
    对课程近期学生向 AI 助教的提问按核心概念词簇聚合，直接揭示课堂教学盲区。
    """

    strategy_name = "v1_keyword_semantic_clusterer"

    async def cluster_questions(self, payload: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        course_id: str = payload.get("course_id", "unknown")
        questions: list[dict[str, Any]] = payload.get("questions") or []
        known_topics: list[str] = payload.get("known_knowledge_points") or []

        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for q in questions:
            text = str(q.get("question", "")).strip()
            if not text:
                continue
            assigned = None
            for tp in known_topics:
                if tp and tp.lower() in text.lower():
                    assigned = tp
                    break
            if not assigned:
                assigned = self._extract_topic_label(text)
            buckets[assigned].append(q)

        clusters: list[dict[str, Any]] = []
        for topic, items in buckets.items():
            confidences = [float(i.get("confidence", 0.8)) for i in items]
            unanswered = sum(1 for i in items if i.get("needs_teacher_review"))
            avg_conf = round(sum(confidences) / len(confidences), 2) if confidences else 0.8
            clusters.append({
                "topic_label": topic,
                "question_count": len(items),
                "avg_confidence": avg_conf,
                "unanswered_count": unanswered,
                "sample_questions": [str(i.get("question")) for i in items[:3]],
                "teaching_suggestion": (
                    f"学生围绕「{topic}」提问密集且存在低置信度样本，建议补充课堂案例或将典型解答置顶至课程 FAQ。"
                    if unanswered > 0 or avg_conf < 0.65
                    else f"学生对「{topic}」关注度较高，可在阶段测验中检验实际掌握深度。"
                ),
            })

        clusters.sort(key=lambda c: (c["question_count"], c["unanswered_count"]), reverse=True)
        elapsed_ms = max(1, int((time.perf_counter() - started) * 1000))
        telemetry_store.record_usage(
            capability="question_hotspot_clustering",
            course_id=course_id,
            latency_ms=elapsed_ms,
            prompt_tokens=max(10, len(questions) * 12),
            completion_tokens=max(10, len(clusters) * 16),
            provider="local-clusterer",
            degraded=False,
        )

        return {
            "strategy_used": self.strategy_name,
            "clusters": clusters[:8],
        }

    @staticmethod
    def _extract_topic_label(text: str) -> str:
        en_terms = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text)
        if en_terms:
            return en_terms[0]
        cjk_chunks = re.findall(r"[\u4e00-\u9fff]{2,8}", text)
        for chunk in cjk_chunks:
            for sw in _STOPWORDS:
                chunk = chunk.replace(sw, "")
            chunk = chunk.strip()
            if len(chunk) >= 2:
                return chunk[:6]
        return "课程综合概念"
