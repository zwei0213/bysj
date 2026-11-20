import time
from typing import Any
from app.services.telemetry_store import telemetry_store


class V1RuleSimilarityRecommender:
    """
    V1 轻量个性化学习内容推荐策略（规则引擎 + 知识点重叠相似度）：
    无状态接收工程端提供的候选章节/资料及学生近期错题、提问与观看完成度，输出带可解释理由的推荐列表。
    """

    strategy_name = "v1_rule_and_topic_similarity"

    async def recommend(self, payload: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        course_id: str = payload.get("course_id", "global")
        candidates: list[dict[str, Any]] = payload.get("candidate_items") or []
        history: dict[str, Any] = payload.get("learner_history") or {}
        top_k: int = max(1, min(10, int(payload.get("top_k") or 4)))

        completed_ids = set(history.get("completed_item_ids") or [])
        weak_points = [w.lower() for w in (history.get("weak_knowledge_points") or [])]
        asked_keywords = [q.lower() for q in (history.get("recent_asked_topics") or [])]

        scored: list[dict[str, Any]] = []
        for idx, item in enumerate(candidates):
            item_id = item.get("item_id", f"item_{idx}")
            title = item.get("title", "未命名学习单元")
            item_topics = [t.lower() for t in (item.get("knowledge_points") or [])]
            item_type = item.get("item_type", "chapter")
            text_blob = f"{title} {' '.join(item_topics)}".lower()

            score = 0.50
            reasons: list[str] = []

            # 规则 1：命中错题本薄弱知识点最高优先复习
            matched_weak = [w for w in weak_points if w and w in text_blob]
            if matched_weak:
                score += 0.35
                reasons.append(f"针对您近期测验薄弱知识点「{matched_weak[0]}」进行定向巩固")

            # 规则 2：命中近期向 AI 助教提问的疑惑主题
            matched_ask = [k for k in asked_keywords if k and k in text_blob]
            if matched_ask:
                score += 0.20
                reasons.append(f"与您近期向助教提问的「{matched_ask[0]}」高度关联")

            # 规则 3：尚未完成的核心章节按课程推进顺序推荐
            if item_id not in completed_ids:
                score += 0.15
                if not reasons:
                    reasons.append("衔接当前学习进度，建议按课程脉络继续推进")
            else:
                score -= 0.20
                if not reasons:
                    reasons.append("温故知新：巩固已学重点单元")

            priority_tag = "薄弱点攻坚" if matched_weak else ("疑问延伸" if matched_ask else "进度衔接")
            scored.append({
                "item_id": item_id,
                "item_type": item_type,
                "course_id": item.get("course_id", course_id),
                "video_id": item.get("video_id"),
                "chapter_id": item.get("chapter_id"),
                "start_sec": item.get("start_sec"),
                "title": title,
                "score": round(min(0.99, max(0.1, score)), 3),
                "priority_tag": priority_tag,
                "reason": "；".join(reasons[:2]),
            })

        scored.sort(key=lambda x: x["score"], reverse=True)
        elapsed_ms = max(1, int((time.perf_counter() - started) * 1000))
        telemetry_store.record_usage(
            capability="content_recommendation",
            course_id=course_id,
            latency_ms=elapsed_ms,
            prompt_tokens=max(10, len(candidates) * 15),
            completion_tokens=max(10, top_k * 20),
            provider="local-rule-similarity",
            degraded=False,
        )

        return {
            "strategy_used": self.strategy_name,
            "items": scored[:top_k],
        }
