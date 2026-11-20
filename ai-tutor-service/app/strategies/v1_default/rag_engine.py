import asyncio
from collections.abc import AsyncIterator
import time
from typing import Any
from app.providers.llm_provider import llm_client
from app.services.telemetry_store import telemetry_store
from app.services.vector_store import knowledge_store

_COREFERENCE_MARKERS = (
    "它",
    "这个",
    "那个",
    "这",
    "那",
    "上述",
    "上面",
    "刚才",
    "前文",
    "继续",
    "再讲",
    "展开",
    "举例",
    "例子",
    "区别",
    "为什么呢",
    "怎么推",
    "it",
    "this",
    "that",
    "they",
)


def _format_mmss(seconds: float | int | None) -> str:
    if seconds is None:
        return ""
    sec = max(0, int(seconds))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def _extract_recent_history(raw_history: Any, max_turns: int = 6) -> list[dict[str, str]]:
    if not isinstance(raw_history, list):
        return []
    turns: list[dict[str, str]] = []
    for item in raw_history[-max_turns:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "user").strip().lower()
        if role not in ("user", "assistant"):
            role = "user"
        content = str(item.get("content") or "").strip()
        if content:
            turns.append({"role": role, "content": content})
    return turns


def _needs_history_disambiguation(question: str) -> bool:
    if len(question) <= 18:
        return True
    lowered = question.lower()
    return any(marker in lowered for marker in _COREFERENCE_MARKERS)


class V1CourseRagEngine:
    """
    V1 经典课程级隔离 RAG 助教策略：
    - 双向内容安全过滤与输出敏感词强制脱敏（全局敏感词 + 教师课程禁答话题）；
    - 多轮对话历史（最近 6 轮）指代消解与上下文注入；
    - 结合“边看边问”视频秒级上下文加权检索；
    - 严格引用溯源（精确到哪份讲义或哪个视频第几分几秒）；
    - 低置信度问题：严格模式下自动置 `needs_teacher_review=True` 驱动教师收件箱；开放超纲模式下生成带明确标注的通识拓展解答。
    """

    strategy_name = "v1_course_isolated_rag"

    async def generate_reply(self, payload: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        course_id: str = payload["course_id"]
        question: str = payload["question"].strip()
        quick_action: str | None = payload.get("quick_action")
        video_ctx: dict[str, Any] | None = payload.get("video_context")
        policy: dict[str, Any] = payload.get("tutor_policy") or {}
        history_turns = _extract_recent_history(payload.get("conversation_history"))

        # 1. 全局安全输入过滤
        blocked_kw = telemetry_store.check_safety_violation(question, direction="input")
        if blocked_kw:
            return {
                "answer": "抱歉，您的提问触发了平台学术诚信与内容安全规范，助教无法提供相关内容。请围绕课程知识本身提问。",
                "confidence": 1.0,
                "needs_teacher_review": False,
                "policy_blocked": True,
                "degraded": False,
                "strategy_used": self.strategy_name,
                "citations": [],
                "suggested_followups": ["本章节的核心知识点有哪些？", "请帮我梳理本节课的重点概念"],
            }

        # 2. 教师配置的课程禁答话题检查
        forbidden_topics: list[str] = policy.get("forbidden_topics") or []
        for topic in forbidden_topics:
            if topic and topic.lower() in question.lower():
                return {
                    "answer": f"根据授课教师设定的助教行为规范，话题「{topic}」属于课堂独立思考或考核保密范围，助教暂不直接给出解答，建议先结合讲义自行推导。",
                    "confidence": 1.0,
                    "needs_teacher_review": False,
                    "policy_blocked": True,
                    "degraded": False,
                    "strategy_used": self.strategy_name,
                    "citations": [],
                    "suggested_followups": ["能提示一下相关的前置概念吗？", "这一章的基础定义是什么？"],
                }

        # 3. 结合多轮对话上下文与边看边问时间戳构造检索 Query（防指代词稀释余弦分值）
        search_query = question
        anchor_context_q: str | None = None
        if history_turns and _needs_history_disambiguation(question):
            prev_user_qs = [
                m["content"]
                for m in reversed(history_turns)
                if m["role"] == "user" and m["content"] != question
            ]
            prev_asst_msgs = [
                m["content"]
                for m in reversed(history_turns)
                if m["role"] == "assistant" and "暂未检索到" not in m["content"]
            ]
            if prev_user_qs:
                anchor_context_q = prev_user_qs[0]
                if prev_asst_msgs:
                    anchor_context_q = f"{anchor_context_q} {prev_asst_msgs[0][:140]}"
                search_query = f"{prev_user_qs[0]} {search_query}"

        if video_ctx and video_ctx.get("chapter_title"):
            search_query = f"{search_query} {video_ctx['chapter_title']}"

        hits = await knowledge_store.search_async(
            course_id,
            search_query,
            top_k=3,
            chapter_id=video_ctx.get("chapter_id") if video_ctx else None,
            video_id=video_ctx.get("video_id") if video_ctx else None,
        )
        if anchor_context_q:
            anchor_hits = await knowledge_store.search_async(
                course_id,
                anchor_context_q,
                top_k=3,
                chapter_id=video_ctx.get("chapter_id") if video_ctx else None,
                video_id=video_ctx.get("video_id") if video_ctx else None,
            )
            merged_by_id: dict[str, dict[str, Any]] = {h["chunk_id"]: dict(h) for h in hits}
            for ah in anchor_hits:
                cid = ah["chunk_id"]
                boosted = round(ah["score"] * 0.98, 4)
                if cid in merged_by_id:
                    if boosted > merged_by_id[cid]["score"]:
                        merged_by_id[cid]["score"] = boosted
                else:
                    copied = dict(ah)
                    copied["score"] = boosted
                    merged_by_id[cid] = copied
            hits = sorted(merged_by_id.values(), key=lambda x: x["score"], reverse=True)[:3]

        runtime_cfg = telemetry_store.get_runtime_config()
        threshold = float(runtime_cfg.get("confidence_threshold", 0.28))
        strict_only = bool(policy.get("strict_course_only", True)) and not bool(policy.get("allow_out_of_syllabus", False))
        tone = str(policy.get("tone") or "encouraging_academic")

        history_block = ""
        if history_turns:
            formatted_turns = "\n".join(
                f"{'学生' if m['role'] == 'user' else '助教'}：{m['content'][:300]}"
                for m in history_turns
            )
            history_block = f"最近多轮对话历史：\n{formatted_turns}\n\n"

        action_hint = {
            "explain_simply": "请用通俗易懂的生活类比重新解释，避免堆砌晦涩术语。",
            "give_example": "请给出一个具体的数值计算或工程代码场景示例帮助理解。",
            "summarize_key_points": "请用 3 条清晰的要点列表进行考前精炼总结。",
        }.get(quick_action or "", "请条理清晰地解答，并明确指出参考了哪份课程资料或视频时间点。")

        top_score = hits[0]["score"] if hits else 0.0

        # 4. 低置信度或无匹配资料分支
        if not hits or top_score < threshold:
            if strict_only:
                answer = (
                    "在当前课程已发布的讲义与视频转写稿中，暂未检索到足够确切的依据来回答该问题。"
                    "系统已自动将您的问题流转至授课教师的「未解答问题收件箱」，老师解答并回流知识库后我将第一时间掌握！"
                )
                answer, _ = telemetry_store.sanitize_output_text(answer)
                elapsed_ms = max(1, int((time.perf_counter() - started) * 1000))
                telemetry_store.record_usage(
                    capability="tutor_rag_reply",
                    course_id=course_id,
                    latency_ms=elapsed_ms,
                    prompt_tokens=len(question),
                    completion_tokens=len(answer),
                    provider="local-guardrail",
                    degraded=False,
                )
                return {
                    "answer": answer,
                    "confidence": round(top_score, 3),
                    "needs_teacher_review": True,
                    "policy_blocked": False,
                    "degraded": False,
                    "strategy_used": self.strategy_name,
                    "citations": [],
                    "suggested_followups": [
                        "本门课程目前已收录哪些核心章节？",
                        "请总结当前视频章节的主要内容",
                    ],
                }

            # 开放超纲解答模式（allow_out_of_syllabus=True 或 strict_course_only=False）
            fallback_oos = self._compose_out_of_syllabus_answer(
                question=question,
                quick_action=quick_action,
                video_ctx=video_ctx,
                tone=tone,
            )
            fallback_oos, _ = telemetry_store.sanitize_output_text(fallback_oos)

            llm_res = await llm_client.complete_text(
                system_prompt=(
                    f"你是本课程 AI 助教（语气风格：{tone}）。当前课程讲义未直接覆盖该问题，"
                    "允许结合学科通用原理严谨作答，但必须在回答中明确注明“该部分属于课程讲义外的拓展补充”。"
                    f"{action_hint}"
                ),
                user_prompt=f"{history_block}学生提问：{question}",
                fallback_text=fallback_oos,
            )
            final_oos, _ = telemetry_store.sanitize_output_text(llm_res.content)

            elapsed_ms = max(1, int((time.perf_counter() - started) * 1000))
            telemetry_store.record_usage(
                capability="tutor_rag_reply",
                course_id=course_id,
                latency_ms=elapsed_ms,
                prompt_tokens=llm_res.prompt_tokens,
                completion_tokens=llm_res.completion_tokens,
                provider=llm_res.provider,
                degraded=llm_res.degraded,
            )

            short_q, _ = telemetry_store.sanitize_output_text(question[:18])
            oos_followups = [
                f"「{short_q}」与本课程已学章节有哪些内在关联？",
                f"能否围绕「{short_q}」再举一个具体应用案例？",
                "回到本课核心讲义梳理重点概念",
            ]
            return {
                "answer": final_oos,
                "confidence": round(max(0.52, min(0.78, top_score + 0.45)), 3),
                "needs_teacher_review": False,
                "policy_blocked": False,
                "degraded": llm_res.degraded,
                "strategy_used": self.strategy_name,
                "citations": [],
                "suggested_followups": oos_followups,
            }

        # 5. 组装引用溯源与风格化/快捷指令回答（含敏感词脱敏）
        citations = []
        for h in hits:
            clean_title, _ = telemetry_store.sanitize_output_text(h["title"])
            clean_snippet, _ = telemetry_store.sanitize_output_text(h["snippet"][:180])
            citations.append(
                {
                    "source_type": h["source_type"],
                    "source_id": h["source_id"],
                    "title": clean_title,
                    "snippet": clean_snippet,
                    "video_id": h.get("video_id"),
                    "chapter_id": h.get("chapter_id"),
                    "timestamp_sec": h.get("timestamp_sec"),
                    "score": h["score"],
                }
            )

        fallback_answer = self._compose_grounded_answer(
            question=question,
            hits=hits,
            quick_action=quick_action,
            video_ctx=video_ctx,
            tone=tone,
        )
        fallback_answer, _ = telemetry_store.sanitize_output_text(fallback_answer)

        context_block = "\n\n".join(
            f"[资料{i + 1}: {h['title']}"
            + (f" @ {_format_mmss(h.get('timestamp_sec'))}" if h.get("timestamp_sec") is not None else "")
            + f"]\n{h['snippet']}"
            for i, h in enumerate(hits)
        )

        llm_res = await llm_client.complete_text(
            system_prompt=(
                f"你是本课程专属 AI 助教（语气风格：{tone}）。"
                f"必须严格依据给定的课程资料作答。{action_hint}"
            ),
            user_prompt=f"{history_block}课程参考片段：\n{context_block}\n\n学生提问：{question}",
            fallback_text=fallback_answer,
        )

        final_answer, _ = telemetry_store.sanitize_output_text(llm_res.content)

        elapsed_ms = max(1, int((time.perf_counter() - started) * 1000))
        telemetry_store.record_usage(
            capability="tutor_rag_reply",
            course_id=course_id,
            latency_ms=elapsed_ms,
            prompt_tokens=llm_res.prompt_tokens,
            completion_tokens=llm_res.completion_tokens,
            provider=llm_res.provider,
            degraded=llm_res.degraded,
        )

        clean_primary_title, _ = telemetry_store.sanitize_output_text(hits[0]["title"])
        followups = self._build_followups(clean_primary_title, quick_action)
        return {
            "answer": final_answer,
            "confidence": round(min(0.98, max(0.55, top_score + 0.25)), 3),
            "needs_teacher_review": False,
            "policy_blocked": False,
            "degraded": llm_res.degraded,
            "strategy_used": self.strategy_name,
            "citations": citations,
            "suggested_followups": followups,
        }

    async def stream_reply(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """
        SSE 流式助教问答：
        - 云端 LLM 模式下透传实时 token 增量 (`event="delta"`)；
        - 本地离线模式或政策拦截分支按语义片段流式推送，最后输出完整结构化 `event="done"` 供业务后端落库。
        """
        started = time.perf_counter()
        course_id: str = payload["course_id"]
        question: str = payload["question"].strip()
        quick_action: str | None = payload.get("quick_action")
        video_ctx: dict[str, Any] | None = payload.get("video_context")
        policy: dict[str, Any] = payload.get("tutor_policy") or {}
        history_turns = _extract_recent_history(payload.get("conversation_history"))

        # 快速前置拦截 / 低置信度严格拦截直接复用 generate_reply 并平滑输出
        if telemetry_store.check_safety_violation(question, direction="input") or any(
            t and t.lower() in question.lower() for t in (policy.get("forbidden_topics") or [])
        ):
            result = await self.generate_reply(payload)
            yield {"event": "delta", "delta": result["answer"]}
            yield {"event": "done", "data": result}
            return

        # 若为 local 模式，直接走确定性结果并分片打字机流式推送
        from app.core.config import settings

        if settings.ai_provider.lower().strip() == "local":
            result = await self.generate_reply(payload)
            text = result["answer"]
            step = 14
            for i in range(0, len(text), step):
                yield {"event": "delta", "delta": text[i : i + step]}
                await asyncio.sleep(0.015)
            yield {"event": "done", "data": result}
            return

        # 云端 LLM 真实流式路径：先检索课程知识库 -> 实时流式推送 LLM token -> 脱敏并记录遥测
        search_query = question
        if history_turns and _needs_history_disambiguation(question):
            prev_qs = [m["content"] for m in reversed(history_turns) if m["role"] == "user" and m["content"] != question]
            if prev_qs:
                search_query = f"{prev_qs[0]} {search_query}"
        if video_ctx and video_ctx.get("chapter_title"):
            search_query = f"{search_query} {video_ctx['chapter_title']}"

        hits = await knowledge_store.search_async(
            course_id,
            search_query,
            top_k=3,
            chapter_id=video_ctx.get("chapter_id") if video_ctx else None,
            video_id=video_ctx.get("video_id") if video_ctx else None,
        )
        threshold = float(telemetry_store.get_runtime_config().get("confidence_threshold", 0.28))
        strict_only = bool(policy.get("strict_course_only", True)) and not bool(policy.get("allow_out_of_syllabus", False))
        top_score = hits[0]["score"] if hits else 0.0

        if (not hits or top_score < threshold) and strict_only:
            result = await self.generate_reply(payload)
            yield {"event": "delta", "delta": result["answer"]}
            yield {"event": "done", "data": result}
            return

        tone = str(policy.get("tone") or "encouraging_academic")
        history_block = ""
        if history_turns:
            formatted = "\n".join(f"{'学生' if m['role'] == 'user' else '助教'}：{m['content'][:300]}" for m in history_turns)
            history_block = f"最近多轮对话历史：\n{formatted}\n\n"
        action_hint = {
            "explain_simply": "请用通俗易懂的生活类比重新解释，避免堆砌晦涩术语。",
            "give_example": "请给出一个具体的数值计算或工程代码场景示例帮助理解。",
            "summarize_key_points": "请用 3 条清晰的要点列表进行考前精炼总结。",
        }.get(quick_action or "", "请条理清晰地解答，并明确指出参考了哪份课程资料或视频时间点。")

        citations: list[dict[str, Any]] = []
        if hits and top_score >= threshold:
            for h in hits:
                citations.append(
                    {
                        "source_type": h["source_type"],
                        "source_id": h["source_id"],
                        "title": telemetry_store.sanitize_output_text(h["title"])[0],
                        "snippet": telemetry_store.sanitize_output_text(h["snippet"][:180])[0],
                        "video_id": h.get("video_id"),
                        "chapter_id": h.get("chapter_id"),
                        "timestamp_sec": h.get("timestamp_sec"),
                        "score": h["score"],
                    }
                )
            fallback_txt = telemetry_store.sanitize_output_text(
                self._compose_grounded_answer(
                    question=question, hits=hits, quick_action=quick_action, video_ctx=video_ctx, tone=tone
                )
            )[0]
            context_block = "\n\n".join(
                f"[资料{i + 1}: {h['title']}"
                + (f" @ {_format_mmss(h.get('timestamp_sec'))}" if h.get("timestamp_sec") is not None else "")
                + f"]\n{h['snippet']}"
                for i, h in enumerate(hits)
            )
            sys_prompt = f"你是本课程专属 AI 助教（语气风格：{tone}）。必须严格依据给定的课程资料作答。{action_hint}"
            usr_prompt = f"{history_block}课程参考片段：\n{context_block}\n\n学生提问：{question}"
            followups = self._build_followups(citations[0]["title"], quick_action)
            confidence = round(min(0.98, max(0.55, top_score + 0.25)), 3)
        else:
            fallback_txt = telemetry_store.sanitize_output_text(
                self._compose_out_of_syllabus_answer(
                    question=question, quick_action=quick_action, video_ctx=video_ctx, tone=tone
                )
            )[0]
            sys_prompt = (
                f"你是本课程 AI 助教（语气风格：{tone}）。当前课程讲义未直接覆盖该问题，"
                f"允许结合学科通用原理严谨作答，但必须在回答中明确注明“该部分属于课程讲义外的拓展补充”。{action_hint}"
            )
            usr_prompt = f"{history_block}学生提问：{question}"
            short_q, _ = telemetry_store.sanitize_output_text(question[:18])
            followups = [
                f"「{short_q}」与本课程已学章节有哪些内在关联？",
                f"能否围绕「{short_q}」再举一个具体应用案例？",
                "回到本课核心讲义梳理重点概念",
            ]
            confidence = round(max(0.52, min(0.78, top_score + 0.45)), 3)

        chunks_acc: list[str] = []
        async for delta in llm_client.stream_text(sys_prompt, usr_prompt, fallback_text=fallback_txt):
            clean_delta, _ = telemetry_store.sanitize_output_text(delta)
            chunks_acc.append(clean_delta)
            yield {"event": "delta", "delta": clean_delta}

        final_answer, _ = telemetry_store.sanitize_output_text("".join(chunks_acc))
        elapsed_ms = max(1, int((time.perf_counter() - started) * 1000))
        telemetry_store.record_usage(
            capability="tutor_rag_reply",
            course_id=course_id,
            latency_ms=elapsed_ms,
            prompt_tokens=max(12, (len(sys_prompt) + len(usr_prompt)) // 2),
            completion_tokens=max(10, len(final_answer) // 2),
            provider=f"{settings.ai_provider}-stream",
            degraded=False,
        )
        yield {
            "event": "done",
            "data": {
                "answer": final_answer,
                "confidence": confidence,
                "needs_teacher_review": False,
                "policy_blocked": False,
                "degraded": False,
                "strategy_used": self.strategy_name,
                "citations": citations,
                "suggested_followups": followups,
            },
        }

    @staticmethod
    def _compose_out_of_syllabus_answer(
        *,
        question: str,
        quick_action: str | None,
        video_ctx: dict[str, Any] | None,
        tone: str,
    ) -> str:
        greeting = "同学你好！" if tone != "strict" else ""
        ctx_prefix = ""
        if video_ctx and video_ctx.get("timestamp_sec") is not None:
            ctx_prefix = f"结合您当前在 `{_format_mmss(video_ctx['timestamp_sec'])}` 处的延伸思考，"

        topic_focus = question[:28]
        notice = "（注：**该部分属于课程讲义外的拓展补充**，仅供课外延伸理解参考，不纳入本课必考讲义范围。）"

        if quick_action == "explain_simply":
            return (
                f"{greeting}{ctx_prefix}关于「{topic_focus}」的直观理解如下：\n\n"
                f"从学科通用原理来看，我们可以把它类比为“明确基本假设 -> 建立结构化映射 -> 校验适用边界”的三步机制。"
                f"抓住其解决的核心矛盾与输入输出约束，就能建立清晰直觉。\n\n{notice}"
            )
        if quick_action == "give_example":
            return (
                f"{greeting}{ctx_prefix}围绕「{topic_focus}」给出一个通用学科场景示例：\n\n"
                f"1. **原理抽象**：先界定系统的状态变量与核心约束条件；\n"
                f"2. **实例推演**：在小规模标准输入下验证性质是否成立，再观察大规模或边界退化情况下的复杂度与稳定性表现。\n\n{notice}"
            )
        if quick_action == "summarize_key_points":
            return (
                f"{greeting}{ctx_prefix}为您梳理「{topic_focus}」的通用核心要点：\n\n"
                f"- **要点 1（核心定义）**：明确概念解决的根本问题与数学/工程前提；\n"
                f"- **要点 2（关键机制）**：理清核心变换步骤及与其他方法的主要区别；\n"
                f"- **要点 3（适用边界）**：关注计算开销、近似假设及极端输入下的局限性。\n\n{notice}"
            )

        return (
            f"{greeting}{ctx_prefix}针对您提出的「{topic_focus}」，从学科通用原理来看：\n\n"
            f"该问题通常需要从**基本定义与前置假设**、**核心推导或演化机制**以及**实际应用中的复杂度/边界权衡**三个维度综合分析。"
            f"建议在理解通用框架后，再与本门课程已学的核心模型方法进行对比印证。\n\n{notice}"
        )

    @staticmethod
    def _compose_grounded_answer(
        *,
        question: str,
        hits: list[dict[str, Any]],
        quick_action: str | None,
        video_ctx: dict[str, Any] | None,
        tone: str,
    ) -> str:
        primary = hits[0]
        time_ref = (
            f"（对应视频时间点 `{_format_mmss(primary['timestamp_sec'])}`）"
            if primary.get("timestamp_sec") is not None
            else ""
        )
        ctx_prefix = ""
        if video_ctx and video_ctx.get("timestamp_sec") is not None:
            ctx_prefix = f"结合您正在观看的「{video_ctx.get('chapter_title') or '当前片段'}」（`{_format_mmss(video_ctx['timestamp_sec'])}`），"

        if quick_action == "explain_simply":
            return (
                f"{ctx_prefix}我们用更直观的方式来理解：\n\n"
                f"可以把该知识点想象成“先筛选关键线索、再按权重综合决策”的过程。"
                f"根据课程资料《{primary['title']}》{time_ref}的核心阐述：\n"
                f"> {primary['snippet']}\n\n"
                f"简单来说，抓住输入条件、核心变换与适用边界这三步，就能轻松掌握「{question[:24]}」。"
            )
        if quick_action == "give_example":
            return (
                f"{ctx_prefix}下面结合《{primary['title']}》{time_ref}给出一个具体实例：\n\n"
                f"1. **课程原理解析**：{primary['snippet']}\n"
                f"2. **典型应用场景**：在实际做题或工程实现中，先检查输入维度/边界约束，再代入上述核心公式或流程逐步验证，即可避免常见失分点。"
            )
        if quick_action == "summarize_key_points":
            bullets = "\n".join(f"- **要点 {i + 1}**（源自《{h['title']}》）：{h['snippet'][:90]}" for i, h in enumerate(hits))
            return f"{ctx_prefix}为您提炼本问题关联的核心复习要点：\n\n{bullets}"

        supporting = ""
        if len(hits) > 1:
            supporting = f"\n\n此外，参考《{hits[1]['title']}》的补充说明：{hits[1]['snippet'][:120]}"

        greeting = "同学你好！" if tone != "strict" else ""
        return (
            f"{greeting}{ctx_prefix}根据课程资料《{primary['title']}》{time_ref}：\n\n"
            f"{primary['snippet']}"
            f"{supporting}\n\n"
            f"您可以点击下方引用来源一键跳转至对应视频位置或讲义段落进行对照验证。"
        )

    @staticmethod
    def _build_followups(source_title: str, quick_action: str | None) -> list[str]:
        options = [
            "能用更通俗的方式再讲一遍吗？",
            "可以举一个具体的应用例子吗？",
            f"围绕《{source_title}》常考的易错点有哪些？",
        ]
        if quick_action == "explain_simply":
            options[0] = "请帮我提炼成 3 条考前复习要点"
        return options
