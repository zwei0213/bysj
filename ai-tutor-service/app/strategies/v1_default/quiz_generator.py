import time
from typing import Any
from app.providers.llm_provider import llm_client
from app.services.telemetry_store import telemetry_store
from app.services.vector_store import _tokenize_mixed, knowledge_store


class V1KnowledgeQuizGenerator:
    """
    V1 结构化智能出题与个性化薄弱点组题策略：
    - 支持 `teacher_bank`（教师批量生成待审题）、`chapter_practice`（章节即时自测）、
      `personalized_diagnostic`（基于工程端传入的错题与提问历史定向攻坚薄弱点）；
    - 向云端 LLM 注入与目标知识点最相关的最多 6 个讲义正文切片（每片截取 300 字），杜绝无依据盲目出题；
    - 本地确定性引擎支持正确选项位置轮换（单选 A/B/C/D 轮换、判断 A/B 轮换、多选干扰项轮换）；
    - 全量结果经过 `sanitize_output_text` 输出敏感词脱敏。
    """

    strategy_name = "v1_knowledge_driven_quiz_generator"

    async def generate_questions(self, payload: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        course_id: str = payload["course_id"]
        mode: str = payload.get("mode", "chapter_practice")
        scope: dict[str, Any] = payload.get("scope") or {}
        learner_profile: dict[str, Any] = payload.get("learner_profile") or {}

        count = max(1, min(20, int(scope.get("count") or 4)))
        difficulty_pref: str = scope.get("difficulty") or "mixed"
        q_types: list[str] = scope.get("question_types") or ["single_choice", "multiple_choice", "true_false"]
        chapter_ids: list[str] = scope.get("chapter_ids") or []

        # 确定目标知识点优先级：个性化模式下优先锁定学生历史薄弱知识点
        target_topics: list[str] = []
        if mode == "personalized_diagnostic":
            target_topics.extend(learner_profile.get("weak_knowledge_points") or [])
            for wrong_stem in (learner_profile.get("recent_wrong_question_stems") or [])[:2]:
                if wrong_stem:
                    target_topics.append(wrong_stem[:18])
        target_topics.extend(scope.get("knowledge_points") or [])

        course_chunks = knowledge_store.list_course_chunks(course_id)
        if chapter_ids:
            filtered = [c for c in course_chunks if (c.get("metadata") or {}).get("chapter_id") in chapter_ids]
            if filtered:
                course_chunks = filtered

        if not target_topics:
            target_topics = [c["title"].split("-")[-1].strip() for c in course_chunks[:count]] or ["课程核心概念", "关键机制与边界条件"]

        fallback_questions = self._build_structured_questions(
            course_chunks=course_chunks,
            target_topics=target_topics,
            q_types=q_types,
            difficulty_pref=difficulty_pref,
            count=count,
            chapter_ids=chapter_ids,
            mode=mode,
        )

        relevant_chunks = _select_relevant_chunks(course_chunks, target_topics, max_chunks=6)
        reference_materials = "\n\n".join(
            f"[参考片段 {i + 1} | 《{ch['title']}》]\n{ch['snippet'][:300]}"
            for i, ch in enumerate(relevant_chunks)
        ) or "（当前课程暂无已入库讲义切片，请围绕目标知识点核心学科定义命题）"

        generated, llm_res = await llm_client.complete_json(
            system_prompt=(
                "你是高校课程命题专家。请严格依据提供的参考课程资料正文与目标知识点，输出 JSON 试题数组。\n"
                "数组每项必须严格包含以下字段与格式：\n"
                "- question_type: \"single_choice\" | \"multiple_choice\" | \"true_false\"\n"
                "- stem: 题干字符串\n"
                "- options: 选项对象数组，格式严格为 [{\"key\":\"A\",\"text\":\"...\"}, {\"key\":\"B\",\"text\":\"...\"}]（判断题固定为 A 正确、B 错误）\n"
                "- answer: 正确选项 key 字符串数组，如单选题 [\"A\"]、多选题 [\"A\",\"C\"]、判断题 [\"B\"]，注意均匀分布正确选项位置\n"
                "- explanation: 结合课程资料的详尽解析字符串\n"
                "- difficulty: \"easy\" | \"medium\" | \"hard\"\n"
                "- knowledge_point: 考查知识点字符串\n"
                "- chapter_id: 关联章节 ID 字符串或 null"
            ),
            user_prompt=(
                f"出题模式: {mode}\n"
                f"目标知识点: {target_topics}\n"
                f"允许题型: {q_types}\n"
                f"题量: {count}\n"
                f"难度偏好: {difficulty_pref}\n\n"
                f"参考课程资料正文（最多 6 段，每段最多 300 字）：\n{reference_materials}"
            ),
            fallback_obj=fallback_questions,
        )

        raw_questions = generated if isinstance(generated, list) and generated else fallback_questions
        questions = _normalize_and_sanitize_questions(raw_questions, fallback_questions, count=count)

        elapsed_ms = max(1, int((time.perf_counter() - started) * 1000))
        telemetry_store.record_usage(
            capability=f"assessment_{mode}",
            course_id=course_id,
            latency_ms=elapsed_ms,
            prompt_tokens=llm_res.prompt_tokens,
            completion_tokens=llm_res.completion_tokens,
            provider=llm_res.provider,
            degraded=llm_res.degraded,
        )

        sanitized_topics = [telemetry_store.sanitize_output_text(tp)[0] for tp in target_topics[:count]]
        return {
            "mode": mode,
            "strategy_used": self.strategy_name,
            "targeted_knowledge_points": sanitized_topics,
            "degraded": llm_res.degraded,
            "questions": questions[:count],
        }

    def _build_structured_questions(
        self,
        *,
        course_chunks: list[dict[str, Any]],
        target_topics: list[str],
        q_types: list[str],
        difficulty_pref: str,
        count: int,
        chapter_ids: list[str],
        mode: str,
    ) -> list[dict[str, Any]]:
        difficulties = ["easy", "medium", "hard"]
        choice_keys = ["A", "B", "C", "D"]
        questions: list[dict[str, Any]] = []

        for idx in range(count):
            topic = target_topics[idx % len(target_topics)]
            q_type = q_types[idx % len(q_types)]
            diff = difficulties[idx % len(difficulties)] if difficulty_pref == "mixed" else difficulty_pref

            matched_hits = _select_best_chunk_for_topic(course_chunks, topic, idx)
            snippet = matched_hits["snippet"]
            ref_title = matched_hits["title"]
            ch_id = (
                (matched_hits.get("metadata") or {}).get("chapter_id")
                or (chapter_ids[idx % len(chapter_ids)] if chapter_ids else None)
            )
            prefix_tag = "【薄弱点靶向强化】" if mode == "personalized_diagnostic" else ""

            if q_type == "true_false":
                is_true_statement = (idx % 2) == 0
                if is_true_statement:
                    stem = f"{prefix_tag}判断正误：在「{topic}」的学习中，{snippet[:75]}"
                    ans = ["A"]
                    explanation = f"依据课程资料《{ref_title}》原文阐述：{snippet[:140]}，因此该表述正确（选 A）。"
                else:
                    stem = f"{prefix_tag}判断正误：在应用「{topic}」时，完全无需考虑任何前置假设、输入边界约束或计算复杂度权衡。"
                    ans = ["B"]
                    explanation = f"该表述错误（选 B）。依据《{ref_title}》核心原理：{snippet[:140]}，实际分析必须严格结合边界约束与复杂度权衡。"
                questions.append({
                    "question_type": "true_false",
                    "stem": stem,
                    "options": [
                        {"key": "A", "text": "正确"},
                        {"key": "B", "text": "错误"},
                    ],
                    "answer": ans,
                    "explanation": explanation,
                    "difficulty": diff,
                    "knowledge_point": topic,
                    "chapter_id": ch_id,
                })
            elif q_type == "multiple_choice":
                wrong_pos = idx % 4
                true_statements = [
                    f"核心机制遵循：{snippet[:58]}",
                    f"在实际应用「{topic}」时需结合边界条件与上下文验证",
                    f"掌握「{topic}」有助于衔接后续章节的综合实践分析",
                ]
                wrong_statement = f"「{topic}」在任何极端退化场景下都无需考虑输入约束或计算开销"
                options = []
                ans_keys = []
                true_cursor = 0
                for pos, key in enumerate(choice_keys):
                    if pos == wrong_pos:
                        options.append({"key": key, "text": wrong_statement})
                    else:
                        options.append({"key": key, "text": true_statements[true_cursor]})
                        true_cursor += 1
                        ans_keys.append(key)
                questions.append({
                    "question_type": "multiple_choice",
                    "stem": f"{prefix_tag}关于知识点「{topic}」，下列哪些表述符合本课程讲义与核心原理？（多选）",
                    "options": options,
                    "answer": ans_keys,
                    "explanation": (
                        f"选项 {choice_keys[wrong_pos]} 忽略了边界约束与复杂度权衡，属于典型误区；"
                        f"其余选项 {','.join(ans_keys)} 均符合《{ref_title}》：{snippet[:130]}。"
                    ),
                    "difficulty": diff,
                    "knowledge_point": topic,
                    "chapter_id": ch_id,
                })
            else:
                correct_pos = idx % 4
                correct_key = choice_keys[correct_pos]
                correct_text = f"{snippet[:66]}"
                distractors = [
                    "仅依赖经验猜测，无需结合理论定义与约束条件",
                    "与课程上下文完全独立，不存在前置依赖关系",
                    "在所有极端退化输入下表现完全恒定不变",
                ]
                options = []
                dist_cursor = 0
                for pos, key in enumerate(choice_keys):
                    if pos == correct_pos:
                        options.append({"key": key, "text": correct_text})
                    else:
                        options.append({"key": key, "text": distractors[dist_cursor]})
                        dist_cursor += 1
                questions.append({
                    "question_type": "single_choice",
                    "stem": f"{prefix_tag}围绕「{topic}」的核心原理，下列哪一项理解最为准确？",
                    "options": options,
                    "answer": [correct_key],
                    "explanation": f"正确答案 {correct_key} 直接对应《{ref_title}》的核心结论：{snippet[:140]}。",
                    "difficulty": diff,
                    "knowledge_point": topic,
                    "chapter_id": ch_id,
                })

        return questions


def _select_relevant_chunks(
    chunks: list[dict[str, Any]],
    target_topics: list[str],
    *,
    max_chunks: int = 6,
) -> list[dict[str, Any]]:
    if not chunks:
        return []
    topic_tokens = set(_tokenize_mixed(" ".join(target_topics)))
    lowered_topics = [tp.lower() for tp in target_topics if tp]

    scored: list[tuple[float, int, dict[str, Any]]] = []
    for idx, ch in enumerate(chunks):
        blob = f"{ch.get('title', '')} {ch.get('snippet', '')}"
        lowered_blob = blob.lower()
        exact_hits = sum(2.5 for tp in lowered_topics if tp in lowered_blob)
        chunk_tokens = set(_tokenize_mixed(blob))
        overlap = len(topic_tokens & chunk_tokens) if topic_tokens else 0
        scored.append((exact_hits + overlap, -idx, ch))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in scored[:max_chunks]]


def _select_best_chunk_for_topic(chunks: list[dict[str, Any]], topic: str, idx: int) -> dict[str, Any]:
    for ch in chunks:
        if topic.lower() in ch["title"].lower() or topic.lower() in ch["snippet"].lower():
            return ch
    if chunks:
        best = _select_relevant_chunks(chunks, [topic], max_chunks=1)
        if best:
            return best[0]
        return chunks[idx % len(chunks)]
    return {
        "title": "课程核心讲义",
        "snippet": f"{topic}需要将理论定义、推导过程与实际边界条件结合分析，确保可解释与可验证。",
        "metadata": {},
    }


def _normalize_and_sanitize_questions(
    raw_list: list[Any],
    fallback_list: list[dict[str, Any]],
    *,
    count: int,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for idx in range(count):
        candidate = raw_list[idx] if idx < len(raw_list) and isinstance(raw_list[idx], dict) else fallback_list[idx]
        fb = fallback_list[idx]

        q_type = str(candidate.get("question_type") or fb["question_type"])
        if q_type not in ("single_choice", "multiple_choice", "true_false"):
            q_type = fb["question_type"]

        raw_opts = candidate.get("options")
        opts: list[dict[str, str]] = []
        if isinstance(raw_opts, list) and len(raw_opts) >= 2:
            for opt_i, opt in enumerate(raw_opts):
                if isinstance(opt, dict) and opt.get("key") and opt.get("text"):
                    clean_opt_txt, _ = telemetry_store.sanitize_output_text(str(opt["text"]))
                    opts.append({"key": str(opt["key"]).strip().upper(), "text": clean_opt_txt})
                elif isinstance(opt, str) and opt.strip():
                    key = chr(ord("A") + opt_i)
                    clean_opt_txt, _ = telemetry_store.sanitize_output_text(opt.strip())
                    opts.append({"key": key, "text": clean_opt_txt})

        valid_keys = {o["key"] for o in opts}
        raw_ans = candidate.get("answer")
        if isinstance(raw_ans, str):
            ans_list = [raw_ans.strip().upper()]
        elif isinstance(raw_ans, list):
            ans_list = [str(a).strip().upper() for a in raw_ans if str(a).strip()]
        else:
            ans_list = []

        if len(opts) < 2 or not ans_list or not set(ans_list).issubset(valid_keys):
            opts = [
                {"key": o["key"], "text": telemetry_store.sanitize_output_text(o["text"])[0]}
                for o in fb["options"]
            ]
            ans_list = list(fb["answer"])
            q_type = fb["question_type"]

        diff = str(candidate.get("difficulty") or fb["difficulty"]).lower()
        if diff not in ("easy", "medium", "hard"):
            diff = fb["difficulty"]

        stem, _ = telemetry_store.sanitize_output_text(str(candidate.get("stem") or fb["stem"]))
        explanation, _ = telemetry_store.sanitize_output_text(str(candidate.get("explanation") or fb["explanation"]))
        kp, _ = telemetry_store.sanitize_output_text(str(candidate.get("knowledge_point") or fb["knowledge_point"]))

        normalized.append(
            {
                "question_type": q_type,
                "stem": stem,
                "options": opts,
                "answer": ans_list,
                "explanation": explanation,
                "difficulty": diff,
                "knowledge_point": kp,
                "chapter_id": candidate.get("chapter_id", fb.get("chapter_id")),
            }
        )
    return normalized
