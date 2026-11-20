from collections import Counter
import math
import re
import time
from typing import Any
from app.providers.asr_provider import asr_client
from app.providers.embedding_provider import compute_local_feature_hash_vector, cosine_similarity
from app.providers.llm_provider import llm_client
from app.services.telemetry_store import telemetry_store
from app.services.vector_store import _tokenize_mixed, knowledge_store

_TRANSITION_PREFIX_RE = re.compile(
    r"^(接下来|接着|下面我们|下面来看|第二部分|第三部分|第四部分|第五部分|最后我们|最后来看|此外|另一方面|下面进入|现在进入|本节总结|总结一下|首先我们|第一部分)"
)


def _lexical_cosine(text_a: str, text_b: str) -> float:
    toks_a = Counter(_tokenize_mixed(text_a))
    toks_b = Counter(_tokenize_mixed(text_b))
    if not toks_a or not toks_b:
        return 0.0
    dot = sum(cnt * toks_b.get(tok, 0) for tok, cnt in toks_a.items())
    norm_a = math.sqrt(sum(v * v for v in toks_a.values())) or 1.0
    norm_b = math.sqrt(sum(v * v for v in toks_b.values())) or 1.0
    return max(0.0, dot / (norm_a * norm_b))


def _window_similarity(text_a: str, text_b: str) -> float:
    lex = _lexical_cosine(text_a, text_b)
    vec_a = compute_local_feature_hash_vector(text_a)
    vec_b = compute_local_feature_hash_vector(text_b)
    dense = cosine_similarity(vec_a, vec_b)
    return 0.55 * lex + 0.45 * dense


class V1SemanticChapterizer:
    """
    V1 视频章节化流水线（支持真实视频文件 ASR + 字幕文件 + 纯文本讲稿三级输入）：
    1. 若未直接传入字幕且存在真实 `media_url`，自动调用 `asr_client`（云端 API -> 本地 Faster-Whisper）；
    2. 基于相邻时间窗词法+稠密特征余弦相似度波谷检测（TextTiling 语义边界切分）生成 2~8 个语义章节；
    3. 长视频超过 60 条字幕时覆盖全视频时间轴均匀抽样（最多 150 条）传入 LLM，确保覆盖至 `duration_sec`；
    4. 严格遵守 Human-in-the-Loop：默认不在 `chapterize()` 阶段提前将未审核字幕写入知识库（仅 `index_immediately=True` 时入库）；
    5. 支持按指定时间区间与指令的单章节重新生成 `regenerate_single_chapter`。
    """

    strategy_name = "v1_semantic_window_chapterizer"

    async def chapterize(self, *, course_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        video_id = payload.get("video_id", "vid_unknown")
        video_title = payload.get("video_title", "未命名授课视频")
        media_url = (payload.get("media_url") or "").strip()
        duration_sec = max(1, int(payload.get("duration_sec") or 600))
        raw_text = (payload.get("raw_transcript_text") or "").strip()
        provided_segments: list[dict[str, Any]] = payload.get("transcript_segments") or []
        topic_hints: list[str] = payload.get("knowledge_points") or []
        index_immediately: bool = payload.get("index_immediately") is True

        asr_provider_used = "provided_subtitle" if provided_segments else ("text_alignment" if raw_text else "placeholder_synthesis")
        asr_degraded = False

        # 若未附带现成字幕且上传了真实视频媒体地址，执行真实语音识别 (Cloud API -> Local Faster-Whisper)
        if not provided_segments and not raw_text and media_url:
            asr_res = await asr_client.transcribe_from_url(media_url)
            asr_provider_used = asr_res.provider_used
            asr_degraded = asr_res.degraded
            if asr_res.duration_sec and asr_res.duration_sec > 0:
                duration_sec = asr_res.duration_sec
            if asr_res.segments:
                provided_segments = asr_res.segments

        segments, synthesized_placeholder = self._normalize_or_synthesize_segments(
            video_title=video_title,
            duration_sec=duration_sec,
            raw_text=raw_text,
            provided_segments=provided_segments,
            topic_hints=topic_hints,
        )
        if segments:
            duration_sec = max(duration_sec, int(segments[-1]["end_sec"]))

        # 基于相邻滑动时间窗相似度波谷切分 2~8 个语义章节
        semantic_groups = self._segment_into_semantic_groups(segments)
        fallback_chapters: list[dict[str, Any]] = []

        for g_idx, group in enumerate(semantic_groups):
            order_idx = g_idx + 1
            start_sec = 0 if g_idx == 0 else int(group[0]["start_sec"])
            end_sec = duration_sec if g_idx == len(semantic_groups) - 1 else int(group[-1]["end_sec"])
            end_sec = max(start_sec + 1, end_sec)
            combined_text = " ".join(seg["text"] for seg in group)
            kp = self._extract_key_phrase(combined_text, video_title, order_idx, topic_hints)
            fallback_chapters.append({
                "order_index": order_idx,
                "title": f"第 {order_idx} 节：{kp}",
                "summary": combined_text[:120] + ("…" if len(combined_text) > 120 else ""),
                "start_sec": start_sec,
                "end_sec": end_sec,
                "knowledge_points": [kp],
            })

        sampled_segments = self._sample_segments_across_timeline(segments, max_items=150)
        chapters_data, llm_res = await llm_client.complete_json(
            system_prompt=(
                "你是高校智能教学视频分析助手。请根据给定的覆盖全时间轴的时间戳字幕片段，识别话题转换边界，输出 JSON 数组（2~8 个章节），"
                "每项严格包含 order_index, title, summary, start_sec, end_sec, knowledge_points，"
                "且章节时间范围需单调递增并完整覆盖至视频总时长末尾 duration_sec。"
            ),
            user_prompt=(
                f"视频标题：{video_title}\n"
                f"总时长秒：{duration_sec}\n"
                f"原始字幕总条数：{len(segments)}（已覆盖全轴采样 {len(sampled_segments)} 条代表性字幕）：\n"
                f"{sampled_segments}"
            ),
            fallback_obj=fallback_chapters,
        )

        raw_chapters = chapters_data if isinstance(chapters_data, list) and chapters_data else fallback_chapters
        chapters = self._normalize_and_sanitize_chapters(raw_chapters, fallback_chapters, duration_sec=duration_sec)
        sanitized_segments = [
            {
                "start_sec": int(s["start_sec"]),
                "end_sec": int(s["end_sec"]),
                "text": telemetry_store.sanitize_output_text(str(s["text"]))[0],
            }
            for s in segments
        ]

        # Human-in-the-Loop 守则：默认不在草稿生成阶段提前入库，仅当显式传入 index_immediately=True 时入库
        if index_immediately:
            for ch in chapters:
                ch_start = int(ch.get("start_sec", 0))
                ch_end = int(ch.get("end_sec", duration_sec))
                matched_text = "\n".join(
                    s["text"]
                    for s in sanitized_segments
                    if int(s["start_sec"]) >= ch_start and int(s["start_sec"]) < max(ch_end, ch_start + 1)
                ) or ch.get("summary", "")
                doc_id = f"transcript_{video_id}_{ch.get('order_index', 1)}"
                knowledge_store.upsert_document(
                    course_id,
                    doc_id,
                    title=f"{video_title} - {ch.get('title', '')}",
                    source_type="video_transcript",
                    content=f"{ch.get('title', '')}\n{ch.get('summary', '')}\n{matched_text}",
                    metadata={
                        "video_id": video_id,
                        "start_sec": ch_start,
                        "end_sec": ch_end,
                    },
                )

        elapsed_ms = max(1, int((time.perf_counter() - started) * 1000))
        is_degraded = bool(llm_res.degraded or asr_degraded or synthesized_placeholder)
        telemetry_store.record_usage(
            capability="video_chapterization",
            course_id=course_id,
            latency_ms=elapsed_ms,
            prompt_tokens=llm_res.prompt_tokens,
            completion_tokens=llm_res.completion_tokens,
            provider=f"{asr_provider_used}+{llm_res.provider}",
            degraded=is_degraded,
        )

        return {
            "video_id": video_id,
            "duration_sec": duration_sec,
            "asr_provider_used": asr_provider_used,
            "strategy_used": self.strategy_name,
            "degraded": is_degraded,
            "chapters": chapters,
            "transcript_segments": sanitized_segments,
        }

    async def regenerate_single_chapter(self, *, course_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """按指定时间区间与教师指令重新生成单个章节的标题、摘要与知识点。"""
        started = time.perf_counter()
        video_title = str(payload.get("video_title") or "授课视频").strip()
        start_sec = max(0, int(payload.get("start_sec") or 0))
        end_sec = max(start_sec + 1, int(payload.get("end_sec") or max(60, start_sec + 60)))
        raw_segments: list[dict[str, Any]] = payload.get("transcript_segments") or []
        current_title = str(payload.get("current_title") or "").strip()
        instruction = str(payload.get("instruction") or "").strip()

        matched_segs = [
            s
            for s in raw_segments
            if isinstance(s, dict)
            and str(s.get("text") or "").strip()
            and int(s.get("end_sec", end_sec)) > start_sec
            and int(s.get("start_sec", 0)) < end_sec
        ]
        if not matched_segs:
            matched_segs = [s for s in raw_segments if isinstance(s, dict) and str(s.get("text") or "").strip()]

        combined_text = " ".join(str(s.get("text", "")).strip() for s in matched_segs).strip()
        if not combined_text:
            combined_text = str(payload.get("transcript_text") or "").strip()
        empty_source = not combined_text and not current_title

        kp_primary = self._extract_key_phrase(
            combined_text or current_title,
            video_title,
            1,
            [instruction[:16]] if instruction and len(instruction) <= 16 else [],
        )
        kps = [kp_primary]
        extra_quoted = [q for q in re.findall(r"[「“\"]([^」”\"]{2,16})[」”\"]", combined_text) if q != kp_primary]
        if extra_quoted:
            kps.append(extra_quoted[0])

        if instruction:
            fallback_title = f"{kp_primary}（{instruction[:14]}）" if kp_primary not in instruction else f"{video_title}：{instruction[:18]}"
        elif current_title:
            fallback_title = f"{current_title}（精修版）" if kp_primary in current_title else f"{kp_primary}：{current_title[:16]}"
        else:
            fallback_title = f"核心解析：{kp_primary}"

        if combined_text:
            prefix = f"【按「{instruction[:20]}」重整】" if instruction else ""
            fallback_summary = f"{prefix}{combined_text[:130]}" + ("…" if len(combined_text) > 130 else "")
        else:
            fallback_summary = f"围绕「{current_title or video_title}」（{start_sec}s-{end_sec}s）提炼的核心概念与边界要点。"

        fallback_obj = {
            "title": fallback_title,
            "summary": fallback_summary,
            "knowledge_points": kps[:3],
        }

        regen_data, llm_res = await llm_client.complete_json(
            system_prompt=(
                "你是高校课程视频提纲精修助手。请依据给定的时间区间字幕文本和教师修改指令，"
                "重新生成该单章节的 JSON 对象，严格包含 title, summary, knowledge_points 三个字段。"
            ),
            user_prompt=(
                f"视频标题：{video_title}\n"
                f"章节起止秒数：{start_sec}s - {end_sec}s\n"
                f"原章节标题：{current_title or '（无）'}\n"
                f"教师重生成指令：{instruction or '提炼更准确的学术标题与核心摘要'}\n"
                f"区间转写字幕：{combined_text[:1800] or '（暂无字幕正文）'}"
            ),
            fallback_obj=fallback_obj,
        )

        obj = regen_data if isinstance(regen_data, dict) else fallback_obj
        clean_title, _ = telemetry_store.sanitize_output_text(str(obj.get("title") or fallback_title).strip())
        clean_summary, _ = telemetry_store.sanitize_output_text(str(obj.get("summary") or fallback_summary).strip())
        raw_kps = obj.get("knowledge_points")
        if isinstance(raw_kps, list) and raw_kps:
            clean_kps = [
                telemetry_store.sanitize_output_text(str(k).strip())[0]
                for k in raw_kps
                if str(k).strip()
            ]
        else:
            clean_kps = [telemetry_store.sanitize_output_text(k)[0] for k in kps]

        is_degraded = bool(llm_res.degraded or empty_source)
        elapsed_ms = max(1, int((time.perf_counter() - started) * 1000))
        telemetry_store.record_usage(
            capability="chapter_regeneration",
            course_id=course_id,
            latency_ms=elapsed_ms,
            prompt_tokens=llm_res.prompt_tokens,
            completion_tokens=llm_res.completion_tokens,
            provider=llm_res.provider,
            degraded=is_degraded,
        )

        return {
            "title": clean_title,
            "summary": clean_summary,
            "start_sec": start_sec,
            "end_sec": end_sec,
            "knowledge_points": clean_kps[:4],
            "strategy_used": self.strategy_name,
            "degraded": is_degraded,
        }

    @staticmethod
    def _segment_into_semantic_groups(segments: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        """
        基于相邻时间窗词法+稠密特征余弦相似度波谷检测（TextTiling 思想）切分 2~8 个语义章节。
        """
        n = len(segments)
        if n <= 1:
            return [segments] if segments else []
        if n == 2:
            return [[segments[0]], [segments[1]]]

        min_chapters = 2
        max_chapters = min(8, n)
        window_size = max(1, min(3, n // 5 or 1))
        min_gap = max(1, n // 10)

        # 计算每个潜在切分点 i (位于 segments[i-1] 与 segments[i] 之间) 的话题新颖度分数（相似度波谷深度 + 转折词信号）
        boundary_scores: list[tuple[float, int]] = []
        sims: list[float] = []
        for i in range(1, n):
            left_text = " ".join(s["text"] for s in segments[max(0, i - window_size) : i])
            right_text = " ".join(s["text"] for s in segments[i : min(n, i + window_size)])
            sim = _window_similarity(left_text, right_text)
            sims.append(sim)

        for idx_pos, sim in enumerate(sims):
            split_i = idx_pos + 1
            left_sim = sims[idx_pos - 1] if idx_pos > 0 else sim
            right_sim = sims[idx_pos + 1] if idx_pos + 1 < len(sims) else sim
            # 局部波谷突出度 (Depth Score)
            depth = max(0.0, left_sim - sim) + max(0.0, right_sim - sim)
            novelty = (1.0 - sim) + 0.6 * depth
            if _TRANSITION_PREFIX_RE.search(segments[split_i]["text"].strip()):
                novelty += 0.22
            boundary_scores.append((novelty, split_i))

        # 自适应确定目标章节数（2 ~ 8 章）：综合字幕规模与显著波谷数量
        mean_novelty = sum(sc for sc, _ in boundary_scores) / len(boundary_scores)
        prominent_valleys = [
            split_i
            for pos, (sc, split_i) in enumerate(boundary_scores)
            if sc >= mean_novelty + 0.06
            and (pos == 0 or sc >= boundary_scores[pos - 1][0])
            and (pos + 1 == len(boundary_scores) or sc >= boundary_scores[pos + 1][0])
        ]

        heuristic_target = min(max_chapters, max(min_chapters, round(math.sqrt(n) * 1.15)))
        if prominent_valleys:
            target_chapters = min(max_chapters, max(min_chapters, len(prominent_valleys) + 1, heuristic_target - 1))
        else:
            target_chapters = heuristic_target

        needed_splits = target_chapters - 1
        ranked_candidates = sorted(boundary_scores, key=lambda x: x[0], reverse=True)

        chosen_splits: list[int] = []
        for _score, split_i in ranked_candidates:
            if len(chosen_splits) >= needed_splits:
                break
            if all(abs(split_i - existing) >= min_gap for existing in chosen_splits):
                chosen_splits.append(split_i)

        if len(chosen_splits) < min_chapters - 1:
            chosen_splits = [ranked_candidates[0][1]]

        chosen_splits.sort()
        groups: list[list[dict[str, Any]]] = []
        prev = 0
        for sp in chosen_splits:
            if sp > prev:
                groups.append(segments[prev:sp])
                prev = sp
        if prev < n:
            groups.append(segments[prev:n])
        return groups

    @staticmethod
    def _sample_segments_across_timeline(
        segments: list[dict[str, Any]],
        *,
        max_items: int = 150,
    ) -> list[dict[str, Any]]:
        """
        当长视频字幕超过 60 条时，覆盖全视频时间轴均匀抽样（最多 150 条代表性带时间戳字幕，含首尾锚点），避免仅截断前 60 条导致后半程丢失。
        """
        total = len(segments)
        if total <= 60:
            return segments
        if total <= max_items:
            return [
                {
                    "start_sec": s["start_sec"],
                    "end_sec": s["end_sec"],
                    "text": s["text"][:120],
                }
                for s in segments
            ]

        indices = sorted({round(i * (total - 1) / (max_items - 1)) for i in range(max_items)})
        sampled: list[dict[str, Any]] = []
        for pos, idx in enumerate(indices):
            nxt_idx = indices[pos + 1] if pos + 1 < len(indices) else total
            window = segments[idx:nxt_idx]
            merged_txt = " ".join(w["text"] for w in window[:2]).strip()[:140]
            sampled.append(
                {
                    "start_sec": int(segments[idx]["start_sec"]),
                    "end_sec": int(window[-1]["end_sec"]) if window else int(segments[idx]["end_sec"]),
                    "text": merged_txt,
                }
            )
        return sampled

    @staticmethod
    def _normalize_and_sanitize_chapters(
        raw_chapters: list[Any],
        fallback_chapters: list[dict[str, Any]],
        *,
        duration_sec: int,
    ) -> list[dict[str, Any]]:
        valid_items = [c for c in raw_chapters if isinstance(c, dict) and c.get("title")]
        if not valid_items:
            valid_items = fallback_chapters

        valid_items = valid_items[:8]
        normalized: list[dict[str, Any]] = []
        cursor = 0
        for idx, ch in enumerate(valid_items):
            raw_start = int(ch.get("start_sec", cursor))
            start_sec = 0 if idx == 0 else max(cursor, min(raw_start, max(cursor, duration_sec - 1)))
            default_end = duration_sec if idx == len(valid_items) - 1 else max(start_sec + 1, int(ch.get("end_sec", start_sec + 60)))
            end_sec = duration_sec if idx == len(valid_items) - 1 else min(duration_sec, max(start_sec + 1, default_end))
            cursor = end_sec

            clean_title, _ = telemetry_store.sanitize_output_text(str(ch.get("title") or f"第 {idx + 1} 节").strip())
            clean_summary, _ = telemetry_store.sanitize_output_text(str(ch.get("summary") or "").strip())
            raw_kps = ch.get("knowledge_points") or []
            clean_kps = [
                telemetry_store.sanitize_output_text(str(k).strip())[0]
                for k in (raw_kps if isinstance(raw_kps, list) else [str(raw_kps)])
                if str(k).strip()
            ] or [clean_title[:16]]

            normalized.append(
                {
                    "order_index": idx + 1,
                    "title": clean_title,
                    "summary": clean_summary,
                    "start_sec": start_sec,
                    "end_sec": end_sec,
                    "knowledge_points": clean_kps,
                }
            )

        if normalized:
            normalized[-1]["end_sec"] = max(int(normalized[-1]["end_sec"]), duration_sec)
        return normalized

    def _normalize_or_synthesize_segments(
        self,
        *,
        video_title: str,
        duration_sec: int,
        raw_text: str,
        provided_segments: list[dict[str, Any]],
        topic_hints: list[str],
    ) -> tuple[list[dict[str, Any]], bool]:
        if provided_segments:
            cleaned = [
                {
                    "start_sec": int(seg.get("start_sec", idx * 60)),
                    "end_sec": max(int(seg.get("start_sec", idx * 60)) + 1, int(seg.get("end_sec", (idx + 1) * 60))),
                    "text": str(seg.get("text", "")).strip(),
                }
                for idx, seg in enumerate(provided_segments)
                if isinstance(seg, dict) and str(seg.get("text", "")).strip()
            ]
            if cleaned:
                return cleaned, False

        if raw_text:
            sentences = [
                s.strip()
                for s in re.split(r"(?<=[。！？.!?\n])\s*", raw_text)
                if s.strip()
            ]
            if sentences:
                step = max(1, duration_sec // len(sentences))
                result = []
                for idx, sent in enumerate(sentences):
                    start = idx * step
                    end = duration_sec if idx == len(sentences) - 1 else max(start + 1, (idx + 1) * step)
                    result.append({"start_sec": start, "end_sec": end, "text": sent})
                return result, False

        # 既无音轨转写结果、又无 raw_transcript_text 和有效 provided_segments：合成占位提纲并标记 placeholder=True -> degraded=True
        topics = topic_hints or [
            f"{video_title}背景与问题定义",
            f"{video_title}核心数学原理与结构",
            f"{video_title}工程实现与典型误区分析",
            f"{video_title}本讲小结与课后思考",
        ]
        step = max(15, duration_sec // (len(topics) * 2))
        synthesized: list[dict[str, Any]] = []
        cursor = 0
        for idx, tp in enumerate(topics):
            mid = min(duration_sec, max(cursor + 1, cursor + step))
            nxt = duration_sec if idx == len(topics) - 1 else min(duration_sec, max(mid + 1, mid + step))
            synthesized.append({
                "start_sec": cursor,
                "end_sec": mid,
                "text": f"这一部分重点探讨「{tp}」的核心定义、产生背景以及它在课程体系中的位置。",
            })
            synthesized.append({
                "start_sec": mid,
                "end_sec": nxt,
                "text": f"在掌握「{tp}」时，请注意边界条件推导与工程落地中的复杂度权衡。",
            })
            cursor = nxt
        return synthesized, True

    @staticmethod
    def _extract_key_phrase(text: str, video_title: str, order_idx: int, hints: list[str]) -> str:
        if order_idx - 1 < len(hints) and hints[order_idx - 1]:
            return hints[order_idx - 1]
        quoted = re.findall(r"[「“\"]([^」”\"]{2,18})[」”\"]", text)
        if quoted:
            return quoted[0]
        clean = re.sub(r"^(各位同学好，|接下来我们|接下来|接着|这一部分|我们看|同学们好，|首先我们|最后)", "", text).strip()
        first_clause = re.split(r"[，。；,.;?!？！]", clean)[0].strip()
        if 3 <= len(first_clause) <= 22:
            return first_clause
        return f"{video_title}核心要点 ({order_idx})"
