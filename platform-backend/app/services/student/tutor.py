"""学生 AI 助教域：多轮会话、流式与同步问答及质量反馈。"""
from collections.abc import AsyncIterator
import json
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.clients.ai_service_client import ai_client
from app.core.enums import InboxStatus
from app.core.errors import PlatformError, utc_now_iso
from app.models.entities import Course, CourseTutorConfig, QualitySample, TutorMessage, TutorThread, UnansweredQuestion, User
from app.services.student.study import touch_user_study_streak

BLOCKED_MESSAGE_PLACEHOLDER = "【系统提示】该条 AI 助教回复因内容质量或合规原因已被管理员屏蔽。"

def list_user_tutor_threads(db: Session, *, user: User, course_id: str) -> list[dict[str, Any]]:
    threads = db.scalars(
        select(TutorThread)
        .where(TutorThread.course_id == course_id, TutorThread.user_id == user.id)
        .order_by(TutorThread.updated_at.desc())
    ).all()
    return [
        {"id": t.id, "title": t.title, "created_at": t.created_at, "updated_at": t.updated_at}
        for t in threads
    ]


def get_tutor_thread_detail(db: Session, *, user: User, thread_id: str) -> dict[str, Any]:
    thread = db.get(TutorThread, thread_id)
    if not thread or thread.user_id != user.id:
        raise PlatformError("THREAD_NOT_FOUND", "对话记录不存在", status_code=404)
    msgs = db.scalars(
        select(TutorMessage).where(TutorMessage.thread_id == thread_id).order_by(TutorMessage.created_at.asc())
    ).all()
    return {
        "thread_id": thread.id,
        "title": thread.title,
        "items": [
            {
                "id": m.id,
                "role": m.role,
                "content": BLOCKED_MESSAGE_PLACEHOLDER if m.is_blocked else m.content,
                "citations": [] if m.is_blocked else m.citations_json,
                "suggested_followups": [] if m.is_blocked else m.followups_json,
                "video_context": m.video_context_json,
                "confidence": m.confidence,
                "needs_teacher_review": m.needs_teacher_review,
                "is_bookmarked": m.is_bookmarked,
                "is_blocked": bool(m.is_blocked),
                "feedback_vote": m.feedback_vote,
                "created_at": m.created_at,
            }
            for m in msgs
        ],
    }


async def ask_tutor_in_thread(
    db: Session,
    *,
    user: User,
    course_id: str | None,
    thread_id: str | None,
    question: str,
    quick_action: str | None = None,
    video_context: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    if thread_id:
        thread = db.get(TutorThread, thread_id)
        if not thread or thread.user_id != user.id:
            raise PlatformError("THREAD_NOT_FOUND", "对话记录不存在", status_code=404)
        resolved_course_id = thread.course_id
    else:
        if not course_id or not db.get(Course, course_id):
            raise PlatformError("COURSE_NOT_FOUND", "课程不存在", status_code=404)
        resolved_course_id = course_id
        thread = TutorThread(
            course_id=resolved_course_id,
            user_id=user.id,
            title=question.strip()[:40],
        )
        db.add(thread)
        db.flush()

    history_rows = db.scalars(
        select(TutorMessage)
        .where(TutorMessage.thread_id == thread.id, TutorMessage.is_blocked.is_(False))
        .order_by(TutorMessage.created_at.asc())
    ).all()
    history_payload = [{"role": m.role, "content": m.content} for m in history_rows[-6:]]

    cfg = db.get(CourseTutorConfig, resolved_course_id)
    policy = {
        "strict_course_only": cfg.strict_course_only if cfg else True,
        "allow_out_of_syllabus": cfg.allow_out_of_syllabus if cfg else False,
        "tone": cfg.tone if cfg else "encouraging_academic",
        "forbidden_topics": cfg.forbidden_topics_json if cfg else [],
    }

    user_msg = TutorMessage(
        thread_id=thread.id,
        course_id=resolved_course_id,
        user_id=user.id,
        role="user",
        content=question.strip(),
        video_context_json=video_context,
    )
    db.add(user_msg)
    db.flush()

    ai_reply = await ai_client.generate_tutor_reply(
        {
            "course_id": resolved_course_id,
            "question": question.strip(),
            "quick_action": quick_action,
            "video_context": video_context,
            "conversation_history": history_payload,
            "tutor_policy": policy,
        },
        request_id=request_id,
        caller_role=user.role,
        caller_id=user.id,
    )

    assistant_msg = TutorMessage(
        thread_id=thread.id,
        course_id=resolved_course_id,
        user_id=user.id,
        role="assistant",
        content=ai_reply.get("answer", ""),
        citations_json=ai_reply.get("citations", []),
        followups_json=ai_reply.get("suggested_followups", []),
        video_context_json=video_context,
        confidence=float(ai_reply.get("confidence", 0.0)),
        needs_teacher_review=bool(ai_reply.get("needs_teacher_review", False)),
    )
    db.add(assistant_msg)
    thread.updated_at = utc_now_iso()
    touch_user_study_streak(db, user)
    db.flush()

    inbox_item_id = None
    if assistant_msg.needs_teacher_review:
        inbox = UnansweredQuestion(
            course_id=resolved_course_id,
            student_id=user.id,
            message_id=assistant_msg.id,
            question=question.strip(),
            video_context_json=video_context,
            status=InboxStatus.PENDING,
        )
        db.add(inbox)
        db.flush()
        inbox_item_id = inbox.id

    db.commit()
    return {
        "thread_id": thread.id,
        "thread_title": thread.title,
        "user_message_id": user_msg.id,
        "assistant_message": {
            "id": assistant_msg.id,
            "content": assistant_msg.content,
            "confidence": assistant_msg.confidence,
            "needs_teacher_review": assistant_msg.needs_teacher_review,
            "routed_inbox_id": inbox_item_id,
            "degraded": bool(ai_reply.get("degraded", False)),
            "citations": assistant_msg.citations_json,
            "suggested_followups": assistant_msg.followups_json,
            "created_at": assistant_msg.created_at,
        },
    }


async def stream_tutor_in_thread(
    db: Session,
    *,
    user: User,
    course_id: str | None,
    thread_id: str | None,
    question: str,
    quick_action: str | None = None,
    video_context: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> AsyncIterator[str]:
    """SSE 流式问答：实时转发 AI 服务 delta 增量，结束后持久化完整回复与引用元数据。"""
    if thread_id:
        thread = db.get(TutorThread, thread_id)
        if not thread or thread.user_id != user.id:
            raise PlatformError("THREAD_NOT_FOUND", "对话记录不存在", status_code=404)
        resolved_course_id = thread.course_id
    else:
        if not course_id or not db.get(Course, course_id):
            raise PlatformError("COURSE_NOT_FOUND", "课程不存在", status_code=404)
        resolved_course_id = course_id
        thread = TutorThread(course_id=resolved_course_id, user_id=user.id, title=question.strip()[:40])
        db.add(thread)
        db.flush()

    history_rows = db.scalars(
        select(TutorMessage)
        .where(TutorMessage.thread_id == thread.id, TutorMessage.is_blocked.is_(False))
        .order_by(TutorMessage.created_at.asc())
    ).all()
    history_payload = [{"role": m.role, "content": m.content} for m in history_rows[-6:]]

    cfg = db.get(CourseTutorConfig, resolved_course_id)
    policy = {
        "strict_course_only": cfg.strict_course_only if cfg else True,
        "allow_out_of_syllabus": cfg.allow_out_of_syllabus if cfg else False,
        "tone": cfg.tone if cfg else "encouraging_academic",
        "forbidden_topics": cfg.forbidden_topics_json if cfg else [],
    }

    user_msg = TutorMessage(
        thread_id=thread.id,
        course_id=resolved_course_id,
        user_id=user.id,
        role="user",
        content=question.strip(),
        video_context_json=video_context,
    )
    db.add(user_msg)
    db.commit()

    yield f"event: meta\ndata: {json.dumps({'thread_id': thread.id, 'thread_title': thread.title, 'user_message_id': user_msg.id}, ensure_ascii=False)}\n\n"

    final_reply: dict[str, Any] | None = None
    acc_text: list[str] = []
    async for ev in ai_client.stream_tutor_reply(
        {
            "course_id": resolved_course_id,
            "question": question.strip(),
            "quick_action": quick_action,
            "video_context": video_context,
            "conversation_history": history_payload,
            "tutor_policy": policy,
        },
        request_id=request_id,
        caller_role=user.role,
        caller_id=user.id,
    ):
        if ev.get("event") == "delta":
            delta = str(ev.get("delta") or "")
            if delta:
                acc_text.append(delta)
                yield f"event: delta\ndata: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
        elif ev.get("event") == "done":
            final_reply = ev.get("data") or {}

    ai_reply = final_reply or {
        "answer": "".join(acc_text),
        "confidence": 0.0,
        "needs_teacher_review": False,
        "citations": [],
        "suggested_followups": [],
        "degraded": True,
    }

    assistant_msg = TutorMessage(
        thread_id=thread.id,
        course_id=resolved_course_id,
        user_id=user.id,
        role="assistant",
        content=ai_reply.get("answer") or "".join(acc_text),
        citations_json=ai_reply.get("citations", []),
        followups_json=ai_reply.get("suggested_followups", []),
        video_context_json=video_context,
        confidence=float(ai_reply.get("confidence", 0.0)),
        needs_teacher_review=bool(ai_reply.get("needs_teacher_review", False)),
    )
    db.add(assistant_msg)
    thread.updated_at = utc_now_iso()
    touch_user_study_streak(db, user)
    db.flush()

    inbox_item_id = None
    if assistant_msg.needs_teacher_review:
        inbox = UnansweredQuestion(
            course_id=resolved_course_id,
            student_id=user.id,
            message_id=assistant_msg.id,
            question=question.strip(),
            video_context_json=video_context,
            status=InboxStatus.PENDING,
        )
        db.add(inbox)
        db.flush()
        inbox_item_id = inbox.id

    db.commit()
    done_payload = {
        "thread_id": thread.id,
        "thread_title": thread.title,
        "user_message_id": user_msg.id,
        "assistant_message": {
            "id": assistant_msg.id,
            "role": "assistant",
            "content": assistant_msg.content,
            "confidence": assistant_msg.confidence,
            "needs_teacher_review": assistant_msg.needs_teacher_review,
            "routed_inbox_id": inbox_item_id,
            "degraded": bool(ai_reply.get("degraded", False)),
            "citations": assistant_msg.citations_json,
            "suggested_followups": assistant_msg.followups_json,
            "is_bookmarked": False,
            "is_blocked": False,
            "feedback_vote": None,
            "created_at": assistant_msg.created_at,
        },
    }
    yield f"event: done\ndata: {json.dumps(done_payload, ensure_ascii=False)}\n\n"


async def record_message_feedback(
    db: Session,
    *,
    user: User,
    message_id: str,
    vote: str | None,
    bookmarked: bool | None,
    reason: str = "",
    request_id: str | None = None,
) -> dict[str, Any]:
    msg = db.get(TutorMessage, message_id)
    if not msg or msg.user_id != user.id:
        raise PlatformError("MESSAGE_NOT_FOUND", "未找到对应的助教回复记录", status_code=404)

    if bookmarked is not None:
        msg.is_bookmarked = bookmarked
    if vote:
        msg.feedback_vote = vote
        if vote in ("down", "reported"):
            prev_user_msg = db.scalars(
                select(TutorMessage)
                .where(
                    TutorMessage.thread_id == msg.thread_id,
                    TutorMessage.role == "user",
                    TutorMessage.created_at <= msg.created_at,
                )
                .order_by(TutorMessage.created_at.desc())
            ).first()
            q_text = prev_user_msg.content if prev_user_msg else ""
            sample = QualitySample(
                course_id=msg.course_id,
                message_id=msg.id,
                sample_type="tutor_reply",
                source_reason="reported" if vote == "reported" else "student_downvote",
                input_text=q_text,
                model_output=msg.content,
                admin_note=reason,
                status="pending",
            )
            db.add(sample)
            db.flush()
            sync_res = await ai_client.ops_submit_feedback_sample(
                {
                    "sample_id": sample.id,
                    "course_id": msg.course_id,
                    "sample_type": "tutor_reply",
                    "source_reason": sample.source_reason,
                    "input_text": q_text,
                    "model_output": msg.content,
                    "tags": [reason] if reason else [],
                },
                request_id=request_id,
            )
            sample.synced_to_ai_eval = not sync_res.get("degraded", False)

    db.commit()
    return {
        "message_id": msg.id,
        "feedback_vote": msg.feedback_vote,
        "is_bookmarked": msg.is_bookmarked,
    }
