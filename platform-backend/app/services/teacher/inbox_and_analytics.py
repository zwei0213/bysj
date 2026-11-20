"""教师未解答收件箱、助教策略配置、置顶 FAQ 与学情看板服务。"""

from collections import Counter, defaultdict
from typing import Any
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.clients.ai_service_client import ai_client
from app.core.enums import InboxStatus, QuizSessionStatus
from app.core.errors import PlatformError, utc_now_iso
from app.models.entities import CourseFaq, CourseTutorConfig, Enrollment, LearningEvent, QualitySample, QuestionBankItem, QuizSession, TutorMessage, UnansweredQuestion, User, VideoResource, WrongQuestionRecord
from app.services.audit_helper import record_audit

from app.services.teacher.courses import ensure_course_owner_or_admin

def query_unanswered_inbox(db: Session, *, teacher: User, course_id: str) -> list[dict[str, Any]]:
    ensure_course_owner_or_admin(db, course_id, teacher)
    rows = db.scalars(
        select(UnansweredQuestion)
        .where(UnansweredQuestion.course_id == course_id)
        .order_by(UnansweredQuestion.created_at.desc())
    ).all()
    return [
        {
            "id": r.id,
            "question": r.question,
            "video_context": r.video_context_json,
            "status": r.status,
            "teacher_answer": r.teacher_answer,
            "synced_to_kb": r.synced_to_kb,
            "created_at": r.created_at,
            "resolved_at": r.resolved_at,
        }
        for r in rows
    ]


async def resolve_unanswered_inbox_item(
    db: Session,
    *,
    teacher: User,
    item_id: str,
    teacher_answer: str,
    sync_to_kb: bool = True,
    pin_as_faq: bool = False,
    request_id: str | None = None,
) -> UnansweredQuestion:
    item = db.get(UnansweredQuestion, item_id)
    if not item:
        raise PlatformError("INBOX_ITEM_NOT_FOUND", "未解答问题记录不存在", status_code=404)
    ensure_course_owner_or_admin(db, item.course_id, teacher)

    item.teacher_answer = teacher_answer.strip()
    item.status = InboxStatus.RESOLVED
    item.resolved_at = utc_now_iso()

    if sync_to_kb:
        idx = await ai_client.upsert_document(
            course_id=item.course_id,
            document_id=f"teacher_qa_{item.id}",
            title=f"教师权威答疑：{item.question[:40]}",
            source_type="teacher_qa",
            content=f"学生疑问：{item.question}\n授课教师解答：{item.teacher_answer}",
            metadata=item.video_context_json or {},
            request_id=request_id,
            caller_role=teacher.role,
            caller_id=teacher.id,
        )
        item.synced_to_kb = not idx.get("degraded", False)

    if pin_as_faq:
        faq = CourseFaq(
            course_id=item.course_id,
            question=item.question[:280],
            answer=item.teacher_answer,
            is_pinned=True,
        )
        db.add(faq)

    record_audit(
        db,
        actor=teacher,
        action="RESOLVE_UNANSWERED_QUESTION",
        target_type="unanswered_question",
        target_id=item.id,
        detail=f"教师回复收件箱问题并回流知识库={item.synced_to_kb}, 沉淀FAQ={pin_as_faq}",
    )
    db.commit()
    return item


def get_tutor_config_dict(db: Session, *, teacher: User, course_id: str) -> dict[str, Any]:
    ensure_course_owner_or_admin(db, course_id, teacher)
    cfg = db.get(CourseTutorConfig, course_id)
    if not cfg:
        return {
            "strict_course_only": True,
            "allow_out_of_syllabus": False,
            "tone": "encouraging_academic",
            "forbidden_topics": [],
        }
    return {
        "strict_course_only": cfg.strict_course_only,
        "allow_out_of_syllabus": cfg.allow_out_of_syllabus,
        "tone": cfg.tone,
        "forbidden_topics": cfg.forbidden_topics_json,
    }


def upsert_tutor_config(
    db: Session,
    *,
    teacher: User,
    course_id: str,
    strict_course_only: bool,
    allow_out_of_syllabus: bool,
    tone: str,
    forbidden_topics: list[str],
) -> None:
    ensure_course_owner_or_admin(db, course_id, teacher)
    cfg = db.get(CourseTutorConfig, course_id)
    if not cfg:
        cfg = CourseTutorConfig(course_id=course_id)
        db.add(cfg)
    cfg.strict_course_only = strict_course_only
    cfg.allow_out_of_syllabus = allow_out_of_syllabus
    cfg.tone = tone
    cfg.forbidden_topics_json = forbidden_topics
    cfg.updated_at = utc_now_iso()
    record_audit(
        db,
        actor=teacher,
        action="UPDATE_TUTOR_CONFIG",
        target_type="course",
        target_id=course_id,
        detail=f"更新助教边界策略 (strict={strict_course_only}, tone={tone})",
    )
    db.commit()


def query_course_faqs(db: Session, *, teacher: User, course_id: str) -> list[dict[str, Any]]:
    ensure_course_owner_or_admin(db, course_id, teacher)
    rows = db.scalars(
        select(CourseFaq)
        .where(CourseFaq.course_id == course_id)
        .order_by(CourseFaq.is_pinned.desc(), CourseFaq.created_at.desc())
    ).all()
    return [
        {
            "id": f.id,
            "course_id": f.course_id,
            "question": f.question,
            "answer": f.answer,
            "is_pinned": f.is_pinned,
            "created_at": f.created_at,
        }
        for f in rows
    ]


def create_pinned_faq(
    db: Session,
    *,
    teacher: User,
    course_id: str,
    question: str,
    answer: str,
    is_pinned: bool,
) -> str:
    ensure_course_owner_or_admin(db, course_id, teacher)
    faq = CourseFaq(course_id=course_id, question=question.strip(), answer=answer.strip(), is_pinned=is_pinned)
    db.add(faq)
    db.flush()
    record_audit(
        db,
        actor=teacher,
        action="CREATE_COURSE_FAQ",
        target_type="course_faq",
        target_id=faq.id,
        detail=f"创建课程 FAQ: {faq.question[:40]}",
    )
    db.commit()
    return faq.id


def update_course_faq(
    db: Session,
    *,
    teacher: User,
    faq_id: str,
    question: str | None = None,
    answer: str | None = None,
    is_pinned: bool | None = None,
) -> dict[str, Any]:
    faq = db.get(CourseFaq, faq_id)
    if not faq:
        raise PlatformError("FAQ_NOT_FOUND", "FAQ 记录不存在", status_code=404)
    ensure_course_owner_or_admin(db, faq.course_id, teacher)
    if question is not None:
        faq.question = question.strip()
    if answer is not None:
        faq.answer = answer.strip()
    if is_pinned is not None:
        faq.is_pinned = is_pinned
    record_audit(
        db,
        actor=teacher,
        action="UPDATE_COURSE_FAQ",
        target_type="course_faq",
        target_id=faq.id,
        detail=f"更新课程 FAQ: {faq.question[:40]}",
    )
    db.commit()
    return {
        "id": faq.id,
        "course_id": faq.course_id,
        "question": faq.question,
        "answer": faq.answer,
        "is_pinned": faq.is_pinned,
        "created_at": faq.created_at,
    }


def delete_course_faq(db: Session, *, teacher: User, faq_id: str) -> dict[str, Any]:
    faq = db.get(CourseFaq, faq_id)
    if not faq:
        raise PlatformError("FAQ_NOT_FOUND", "FAQ 记录不存在", status_code=404)
    ensure_course_owner_or_admin(db, faq.course_id, teacher)
    db.delete(faq)
    record_audit(
        db,
        actor=teacher,
        action="DELETE_COURSE_FAQ",
        target_type="course_faq",
        target_id=faq_id,
        detail="删除课程 FAQ",
    )
    db.commit()
    return {"deleted": True, "id": faq_id}


async def submit_teacher_quality_feedback(
    db: Session,
    *,
    teacher: User,
    course_id: str,
    sample_type: str,
    input_text: str,
    model_output: str,
    reason: str = "",
    request_id: str | None = None,
) -> dict[str, Any]:
    ensure_course_owner_or_admin(db, course_id, teacher)
    sample = QualitySample(
        course_id=course_id,
        sample_type=sample_type,
        source_reason="teacher_feedback",
        input_text=input_text.strip(),
        model_output=model_output.strip(),
        admin_note=reason.strip(),
        status="pending",
    )
    db.add(sample)
    db.flush()
    sync_res = await ai_client.ops_submit_feedback_sample(
        {
            "sample_id": sample.id,
            "course_id": course_id,
            "sample_type": sample_type,
            "source_reason": "teacher_feedback",
            "input_text": sample.input_text,
            "model_output": sample.model_output,
            "tags": [reason.strip()] if reason.strip() else ["teacher_feedback"],
        },
        request_id=request_id,
    )
    sample.synced_to_ai_eval = not sync_res.get("degraded", False)
    record_audit(
        db,
        actor=teacher,
        action="REPORT_AI_QUALITY_ISSUE",
        target_type="quality_sample",
        target_id=sample.id,
        detail=f"教师上报 AI 质量样本 ({sample_type})",
    )
    db.commit()
    return {
        "id": sample.id,
        "status": sample.status,
        "synced_to_ai_eval": sample.synced_to_ai_eval,
    }


async def build_teacher_course_analytics(
    db: Session,
    *,
    teacher: User,
    course_id: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    ensure_course_owner_or_admin(db, course_id, teacher)
    enroll_count = db.scalar(select(func.count()).select_from(Enrollment).where(Enrollment.course_id == course_id)) or 0
    videos = db.scalars(
        select(VideoResource).where(VideoResource.course_id == course_id).order_by(VideoResource.order_index)
    ).all()
    events = list(db.scalars(select(LearningEvent).where(LearningEvent.course_id == course_id)).all())

    events_by_video: dict[str, list[LearningEvent]] = defaultdict(list)
    for ev in events:
        if ev.video_id:
            events_by_video[ev.video_id].append(ev)

    video_stats = []
    for vid in videos:
        v_events = events_by_video[vid.id]
        completed_users = len({e.user_id for e in v_events if e.completed})
        completion_rate = round((completed_users / max(1, enroll_count)) * 100, 1)
        skip_count = sum(1 for ev in v_events if ev.event_type == "skip_segment")
        replay_count = sum(1 for ev in v_events if ev.event_type == "replay_segment")
        bucket_counts: Counter[int] = Counter()
        for ev in v_events:
            if ev.event_type == "skip_segment":
                continue
            bucket = (ev.position_sec // 60) * 60
            weight = 2 if ev.event_type == "replay_segment" else 1
            bucket_counts[bucket] += weight
        heatmap = [
            {"minute_start_sec": sec, "label": f"{sec // 60:02d}:00", "intensity": bucket_counts.get(sec, 0)}
            for sec in range(0, max(60, vid.duration_sec), 60)
        ]
        video_stats.append({
            "video_id": vid.id,
            "title": vid.title,
            "outline_status": vid.outline_status,
            "completion_rate_pct": completion_rate,
            "watch_event_count": len(v_events),
            "replay_segments_count": replay_count,
            "skip_segments_count": skip_count,
            "heatmap": heatmap,
        })

    # 按会话内时间顺序精确配对每条学生提问与其紧随的助教回答
    all_msgs = db.scalars(
        select(TutorMessage)
        .where(TutorMessage.course_id == course_id)
        .order_by(TutorMessage.created_at.asc())
    ).all()
    msgs_by_thread: dict[str, list[TutorMessage]] = defaultdict(list)
    for m in all_msgs:
        msgs_by_thread[m.thread_id].append(m)

    q_payload: list[dict[str, Any]] = []
    for thread_msgs in msgs_by_thread.values():
        for idx, m in enumerate(thread_msgs):
            if m.role != "user":
                continue
            paired_reply = (
                thread_msgs[idx + 1]
                if idx + 1 < len(thread_msgs) and thread_msgs[idx + 1].role == "assistant"
                else None
            )
            q_payload.append({
                "question": m.content,
                "confidence": paired_reply.confidence if paired_reply else 0.7,
                "needs_teacher_review": paired_reply.needs_teacher_review if paired_reply else False,
            })

    known_kps = list(
        set(
            db.scalars(
                select(QuestionBankItem.knowledge_point).where(QuestionBankItem.course_id == course_id)
            ).all()
        )
    )
    cluster_res = await ai_client.cluster_questions(
        {"course_id": course_id, "questions": q_payload, "known_knowledge_points": known_kps},
        request_id=request_id,
        caller_role=teacher.role,
        caller_id=teacher.id,
    )

    sessions = list(
        db.scalars(
            select(QuizSession).where(
                QuizSession.course_id == course_id,
                QuizSession.status == QuizSessionStatus.SUBMITTED,
            )
        ).all()
    )
    scores = [float(s.score or 0.0) for s in sessions]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0

    dist_buckets = {"0-59": 0, "60-69": 0, "70-79": 0, "80-89": 0, "90-100": 0}
    q_stats: dict[str, dict[str, Any]] = {}
    for sess in sessions:
        sc = float(sess.score or 0.0)
        if sc < 60:
            dist_buckets["0-59"] += 1
        elif sc < 70:
            dist_buckets["60-69"] += 1
        elif sc < 80:
            dist_buckets["70-79"] += 1
        elif sc < 90:
            dist_buckets["80-89"] += 1
        else:
            dist_buckets["90-100"] += 1

        for d in sess.grading_detail_json or []:
            qid = str(d.get("question_id") or d.get("stem", "")[:30])
            if not qid:
                continue
            entry = q_stats.setdefault(
                qid,
                {
                    "question_id": qid,
                    "stem": str(d.get("stem") or ""),
                    "knowledge_point": str(d.get("knowledge_point") or ""),
                    "attempts": 0,
                    "correct_count": 0,
                },
            )
            entry["attempts"] += 1
            if d.get("is_correct"):
                entry["correct_count"] += 1

    score_distribution = [
        {"range": rng, "label": rng, "count": cnt}
        for rng, cnt in dist_buckets.items()
    ]
    question_accuracy = sorted(
        [
            {
                **st,
                "accuracy_pct": round((st["correct_count"] / max(1, st["attempts"])) * 100, 1),
            }
            for st in q_stats.values()
        ],
        key=lambda x: (x["accuracy_pct"], -x["attempts"]),
    )[:20]

    wrong_records = db.scalars(select(WrongQuestionRecord).where(WrongQuestionRecord.course_id == course_id)).all()
    weak_counter: Counter[str] = Counter()
    for wr in wrong_records:
        weak_counter[wr.knowledge_point] += wr.wrong_count
    weak_ranking = [
        {"knowledge_point": kp, "wrong_appearances": cnt}
        for kp, cnt in weak_counter.most_common(6)
    ]

    return {
        "enrolled_students": enroll_count,
        "video_analytics": video_stats,
        "question_hotspots": cluster_res.get("clusters", []),
        "quiz_summary": {
            "submitted_count": len(sessions),
            "average_score": avg_score,
            "score_distribution": score_distribution,
            "question_accuracy": question_accuracy,
            "weak_knowledge_points": weak_ranking,
        },
    }
