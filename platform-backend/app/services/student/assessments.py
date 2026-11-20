"""学生测验评估域：随堂自测/正式考试/错题重练、判分、错题本与仪表盘推荐。"""

from datetime import datetime
from typing import Any
import uuid
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.clients.ai_service_client import ai_client
from app.core.enums import OutlineStatus, QuestionReviewStatus, QuizSessionStatus, QuizSessionType
from app.core.errors import PlatformError, utc_now_iso
from app.models.entities import Course, Enrollment, ExamPaper, LearningEvent, OutlineVersion, QuestionBankItem, QuizSession, TutorMessage, User, VideoResource, WrongQuestionRecord

from app.services.student.study import parse_flexible_datetime, touch_user_study_streak

def _check_exam_window(start_time: str | None, end_time: str | None) -> None:
    start_dt = parse_flexible_datetime(start_time)
    if start_dt is not None:
        now_cmp = datetime.now(start_dt.tzinfo) if start_dt.tzinfo else datetime.now()
        if now_cmp < start_dt:
            raise PlatformError("EXAM_NOT_OPEN", f"该试卷尚未开放作答（开放时间：{start_time}）", status_code=400)

    end_dt = parse_flexible_datetime(end_time)
    if end_dt is not None:
        now_cmp = datetime.now(end_dt.tzinfo) if end_dt.tzinfo else datetime.now()
        if now_cmp > end_dt:
            raise PlatformError("EXAM_NOT_OPEN", f"该试卷作答时间已截止（截止时间：{end_time}）", status_code=400)


async def start_student_quiz_session(
    db: Session,
    *,
    user: User,
    course_id: str,
    session_type: str,
    chapter_id: str | None = None,
    exam_id: str | None = None,
    wrong_record_ids: list[str] | None = None,
    count: int = 4,
    request_id: str | None = None,
) -> QuizSession:
    questions_snapshot: list[dict[str, Any]] = []
    title = "课程章节自测"
    resolved_exam_id: str | None = None
    time_limit_min: int | None = None
    max_attempts: int | None = None

    if session_type == QuizSessionType.EXAM:
        if not exam_id:
            raise PlatformError("EXAM_NOT_FOUND", "请指定要参加的试卷 exam_id", status_code=404)
        exam = db.get(ExamPaper, exam_id)
        if not exam or not exam.is_published or exam.course_id != course_id:
            raise PlatformError("EXAM_NOT_FOUND", "测验试卷不存在或未发布", status_code=404)

        _check_exam_window(exam.start_time, exam.end_time)

        if exam.target_class_id:
            enr = db.scalar(
                select(Enrollment).where(Enrollment.course_id == course_id, Enrollment.student_id == user.id)
            )
            if not enr or enr.class_id != exam.target_class_id:
                raise PlatformError("EXAM_CLASS_MISMATCH", "您不在该试卷指定的目标教学班级中", status_code=403)

        used_attempts = (
            db.scalar(
                select(func.count())
                .select_from(QuizSession)
                .where(QuizSession.user_id == user.id, QuizSession.exam_id == exam.id)
            )
            or 0
        )
        if used_attempts >= (exam.max_attempts or 1):
            raise PlatformError(
                "EXAM_MAX_ATTEMPTS_REACHED",
                f"已达到该试卷允许的最大作答次数 ({exam.max_attempts} 次)",
                status_code=400,
            )

        resolved_exam_id = exam.id
        time_limit_min = exam.time_limit_min
        max_attempts = exam.max_attempts
        title = exam.title
        if exam.question_ids_json:
            q_map = {
                q.id: q
                for q in db.scalars(
                    select(QuestionBankItem).where(QuestionBankItem.id.in_(exam.question_ids_json))
                ).all()
            }
            questions_snapshot = [
                _serialize_bank_question(q_map[qid]) for qid in exam.question_ids_json if qid in q_map
            ]

    elif session_type == QuizSessionType.WRONG_RETRY:
        title = "错题本专项重练"
        wr_stmt = select(WrongQuestionRecord).where(
            WrongQuestionRecord.user_id == user.id,
            WrongQuestionRecord.course_id == course_id,
        )
        if wrong_record_ids:
            wr_stmt = wr_stmt.where(WrongQuestionRecord.id.in_(wrong_record_ids))
        else:
            wr_stmt = wr_stmt.where(WrongQuestionRecord.is_mastered.is_(False)).order_by(
                WrongQuestionRecord.wrong_count.desc()
            )
        wr_rows = list(db.scalars(wr_stmt).all())
        if not wrong_record_ids:
            wr_rows = wr_rows[:count]
        raw_snaps = [dict(r.question_snapshot_json or {}) for r in wr_rows if r.question_snapshot_json]
        questions_snapshot = _assign_question_ids(raw_snaps)

    elif session_type == QuizSessionType.PERSONALIZED:
        title = "个性化薄弱点强化测验"
        wrongs = db.scalars(
            select(WrongQuestionRecord)
            .where(
                WrongQuestionRecord.user_id == user.id,
                WrongQuestionRecord.course_id == course_id,
                WrongQuestionRecord.is_mastered.is_(False),
            )
            .order_by(WrongQuestionRecord.wrong_count.desc())
        ).all()
        recent_asks = db.scalars(
            select(TutorMessage)
            .where(TutorMessage.user_id == user.id, TutorMessage.course_id == course_id, TutorMessage.role == "user")
            .order_by(TutorMessage.created_at.desc())
        ).all()
        learner_profile = {
            "weak_knowledge_points": [w.knowledge_point for w in wrongs[:5]],
            "recent_wrong_question_stems": [w.question_snapshot_json.get("stem", "") for w in wrongs[:3]],
            "recent_asked_questions": [m.content for m in recent_asks[:4]],
        }
        ai_res = await ai_client.generate_assessments(
            {
                "course_id": course_id,
                "mode": "personalized_diagnostic",
                "scope": {"count": count, "difficulty": "mixed"},
                "learner_profile": learner_profile,
            },
            request_id=request_id,
            caller_role=user.role,
            caller_id=user.id,
        )
        questions_snapshot = _assign_question_ids(ai_res.get("questions", []))
    else:
        if chapter_id:
            title = f"章节即时自测 ({chapter_id})"
        bank_q = db.scalars(
            select(QuestionBankItem).where(
                QuestionBankItem.course_id == course_id,
                QuestionBankItem.review_status == QuestionReviewStatus.APPROVED,
            )
        ).all()
        if chapter_id:
            ch_filtered = [q for q in bank_q if q.chapter_id == chapter_id]
            if ch_filtered:
                bank_q = ch_filtered
        for q_item in bank_q[:count]:
            questions_snapshot.append(_serialize_bank_question(q_item))

        if len(questions_snapshot) < count:
            ai_res = await ai_client.generate_assessments(
                {
                    "course_id": course_id,
                    "mode": "chapter_practice",
                    "scope": {
                        "chapter_ids": [chapter_id] if chapter_id else [],
                        "count": count - len(questions_snapshot),
                    },
                },
                request_id=request_id,
                caller_role=user.role,
                caller_id=user.id,
            )
            questions_snapshot.extend(_assign_question_ids(ai_res.get("questions", [])))

    if not questions_snapshot:
        raise PlatformError("NO_QUESTIONS_AVAILABLE", "当前暂无可用试题，请稍后再试", status_code=400)

    session = QuizSession(
        course_id=course_id,
        user_id=user.id,
        exam_id=resolved_exam_id,
        session_type=session_type,
        title=title,
        questions_snapshot_json=questions_snapshot,
    )
    session._time_limit_min = time_limit_min  # type: ignore[attr-defined]
    session._max_attempts = max_attempts  # type: ignore[attr-defined]
    db.add(session)
    db.commit()
    return session


def submit_student_quiz_session(
    db: Session,
    *,
    user: User,
    session_id: str,
    answers: dict[str, list[str]],
) -> QuizSession:
    session = db.get(QuizSession, session_id)
    if not session or session.user_id != user.id:
        raise PlatformError("QUIZ_SESSION_NOT_FOUND", "测验场次不存在", status_code=404)
    if session.status == QuizSessionStatus.SUBMITTED:
        return session

    questions = session.questions_snapshot_json or []
    per_score = 100.0 / max(1, len(questions))
    earned = 0.0
    details: list[dict[str, Any]] = []

    for q in questions:
        qid = q["id"]
        std_ans = sorted(str(x).upper() for x in (q.get("answer") or []))
        user_ans = sorted(str(x).upper() for x in (answers.get(qid) or []))
        is_correct = std_ans == user_ans
        if is_correct:
            earned += per_score
        else:
            existing_wrong = db.scalars(
                select(WrongQuestionRecord).where(
                    WrongQuestionRecord.user_id == user.id,
                    WrongQuestionRecord.course_id == session.course_id,
                    WrongQuestionRecord.knowledge_point == q.get("knowledge_point", "核心概念"),
                )
            ).first()
            if existing_wrong and existing_wrong.question_snapshot_json.get("stem") == q.get("stem"):
                existing_wrong.wrong_count += 1
                existing_wrong.is_mastered = False
                existing_wrong.last_wrong_answer_json = user_ans
                existing_wrong.updated_at = utc_now_iso()
            else:
                db.add(
                    WrongQuestionRecord(
                        user_id=user.id,
                        course_id=session.course_id,
                        knowledge_point=q.get("knowledge_point", "核心概念"),
                        question_snapshot_json=q,
                        last_wrong_answer_json=user_ans,
                        wrong_count=1,
                        is_mastered=False,
                    )
                )

        details.append({
            "question_id": qid,
            "stem": q.get("stem"),
            "user_answer": user_ans,
            "standard_answer": std_ans,
            "is_correct": is_correct,
            "explanation": q.get("explanation", ""),
            "knowledge_point": q.get("knowledge_point", ""),
            "chapter_id": q.get("chapter_id"),
        })

    session.answers_json = answers
    session.grading_detail_json = details
    session.score = round(earned, 1)
    session.status = QuizSessionStatus.SUBMITTED
    session.submitted_at = utc_now_iso()
    touch_user_study_streak(db, user)
    db.commit()
    return session


def query_wrong_questions(
    db: Session,
    *,
    user: User,
    course_id: str | None = None,
    knowledge_point: str | None = None,
    include_mastered: bool = True,
) -> list[dict[str, Any]]:
    stmt = select(WrongQuestionRecord).where(WrongQuestionRecord.user_id == user.id)
    if course_id:
        stmt = stmt.where(WrongQuestionRecord.course_id == course_id)
    if knowledge_point and knowledge_point.strip():
        stmt = stmt.where(WrongQuestionRecord.knowledge_point.ilike(f"%{knowledge_point.strip()}%"))
    if not include_mastered:
        stmt = stmt.where(WrongQuestionRecord.is_mastered.is_(False))

    rows = db.scalars(stmt.order_by(WrongQuestionRecord.updated_at.desc())).all()
    return [
        {
            "id": r.id,
            "course_id": r.course_id,
            "knowledge_point": r.knowledge_point,
            "question": r.question_snapshot_json,
            "last_wrong_answer": r.last_wrong_answer_json,
            "wrong_count": r.wrong_count,
            "is_mastered": r.is_mastered,
            "review_note": r.review_note,
            "updated_at": r.updated_at,
        }
        for r in rows
    ]


def patch_wrong_question_record(
    db: Session,
    *,
    user: User,
    record_id: str,
    is_mastered: bool | None,
    review_note: str | None,
) -> dict[str, Any]:
    rec = db.get(WrongQuestionRecord, record_id)
    if not rec or rec.user_id != user.id:
        raise PlatformError("WRONG_RECORD_NOT_FOUND", "错题记录不存在", status_code=404)
    if is_mastered is not None:
        rec.is_mastered = is_mastered
    if review_note is not None:
        rec.review_note = review_note
    rec.updated_at = utc_now_iso()
    db.commit()
    return {"id": rec.id, "is_mastered": rec.is_mastered, "review_note": rec.review_note}


async def build_student_dashboard(
    db: Session,
    *,
    user: User,
    request_id: str | None = None,
) -> dict[str, Any]:
    enrollments = list(db.scalars(select(Enrollment).where(Enrollment.student_id == user.id)).all())
    events = list(db.scalars(select(LearningEvent).where(LearningEvent.user_id == user.id)).all())
    total_study_minutes = round(sum(e.watched_seconds for e in events) / 60.0, 1)
    completed_video_ids = {e.video_id for e in events if e.completed and e.video_id}

    sessions = db.scalars(
        select(QuizSession)
        .where(QuizSession.user_id == user.id, QuizSession.status == QuizSessionStatus.SUBMITTED)
        .order_by(QuizSession.submitted_at.asc())
    ).all()
    score_curve = [
        {"session_id": s.id, "title": s.title, "score": s.score, "submitted_at": s.submitted_at}
        for s in sessions[-10:]
    ]

    wrongs = db.scalars(
        select(WrongQuestionRecord).where(
            WrongQuestionRecord.user_id == user.id,
            WrongQuestionRecord.is_mastered.is_(False),
        )
    ).all()
    weak_points = list({w.knowledge_point for w in wrongs})

    enrolled_course_ids = [e.course_id for e in enrollments]
    if not enrolled_course_ids:
        pub_courses = db.scalars(
            select(Course.id).where(Course.status == "published").limit(3)
        ).all()
        enrolled_course_ids = list(pub_courses)

    published_videos = (
        list(
            db.scalars(
                select(VideoResource).where(
                    VideoResource.course_id.in_(enrolled_course_ids),
                    VideoResource.outline_status == OutlineStatus.PUBLISHED,
                    VideoResource.is_hidden.is_(False),
                )
            ).all()
        )
        if enrolled_course_ids
        else []
    )
    ver_ids = [v.published_version_id for v in published_videos if v.published_version_id]
    ver_map = (
        {v.id: v for v in db.scalars(select(OutlineVersion).where(OutlineVersion.id.in_(ver_ids))).all()}
        if ver_ids
        else {}
    )

    candidates: list[dict[str, Any]] = []
    completed_item_ids: list[str] = []
    for vid in published_videos:
        ver = ver_map.get(vid.published_version_id or "")
        if not ver:
            continue
        for ch in ver.chapters_json:
            ch_id = ch.get("chapter_id")
            if vid.id in completed_video_ids and ch_id:
                completed_item_ids.append(ch_id)
            candidates.append({
                "item_id": ch_id,
                "item_type": "chapter",
                "course_id": vid.course_id,
                "video_id": vid.id,
                "chapter_id": ch_id,
                "start_sec": ch.get("start_sec", 0),
                "title": f"{vid.title} · {ch.get('title')}",
                "knowledge_points": ch.get("knowledge_points") or [],
            })

    rec_items: list[dict[str, Any]] = []
    if candidates and enrolled_course_ids:
        recent_asks = db.scalars(
            select(TutorMessage)
            .where(TutorMessage.user_id == user.id, TutorMessage.role == "user")
            .order_by(TutorMessage.created_at.desc())
            .limit(4)
        ).all()
        rec_res = await ai_client.generate_recommendations(
            {
                "course_id": enrolled_course_ids[0],
                "candidate_items": candidates,
                "learner_history": {
                    "completed_item_ids": completed_item_ids,
                    "weak_knowledge_points": weak_points,
                    "recent_asked_topics": [m.content[:20] for m in recent_asks],
                },
                "top_k": 4,
            },
            request_id=request_id,
            caller_role=user.role,
            caller_id=user.id,
        )
        rec_items = rec_res.get("items", [])

    course_map = (
        {
            c.id: c
            for c in db.scalars(select(Course).where(Course.id.in_([e.course_id for e in enrollments]))).all()
        }
        if enrollments
        else {}
    )
    course_cards = [
        {
            "course_id": enr.course_id,
            "title": course_map[enr.course_id].title,
            "category": course_map[enr.course_id].category,
            "cover_url": course_map[enr.course_id].cover_url,
            "progress_pct": enr.progress_pct,
        }
        for enr in enrollments
        if enr.course_id in course_map
    ]

    return {
        "study_minutes": total_study_minutes,
        "streak_days": user.streak_days,
        "last_study_date": user.last_study_date,
        "enrolled_courses": course_cards,
        "quiz_score_curve": score_curve,
        "unmastered_wrong_count": len(wrongs),
        "weak_knowledge_points": weak_points,
        "recommendations": rec_items,
    }


def sanitize_session_for_student(session: QuizSession) -> dict[str, Any]:
    """下发前端作答时剥离标准答案字段防止泄题，并附带考试限时与次数上限元数据。"""
    sanitized_questions = [
        {
            "id": q["id"],
            "question_type": q["question_type"],
            "stem": q["stem"],
            "options": q["options"],
            "difficulty": q.get("difficulty", "medium"),
            "knowledge_point": q.get("knowledge_point", ""),
            "chapter_id": q.get("chapter_id"),
        }
        for q in session.questions_snapshot_json
    ]
    return {
        "session_id": session.id,
        "exam_id": session.exam_id,
        "title": session.title,
        "session_type": session.session_type,
        "time_limit_min": getattr(session, "_time_limit_min", None),
        "max_attempts": getattr(session, "_max_attempts", None),
        "questions": sanitized_questions,
    }


def _serialize_bank_question(q: QuestionBankItem) -> dict[str, Any]:
    return {
        "id": q.id,
        "question_type": q.question_type,
        "stem": q.stem,
        "options": q.options_json,
        "answer": q.answer_json,
        "explanation": q.explanation,
        "difficulty": q.difficulty,
        "knowledge_point": q.knowledge_point,
        "chapter_id": q.chapter_id,
    }


def _assign_question_ids(raw_questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": q.get("id") or f"aiq_{uuid.uuid4().hex[:8]}",
            "question_type": q.get("question_type", "single_choice"),
            "stem": q.get("stem", ""),
            "options": q.get("options", []),
            "answer": q.get("answer", ["A"]),
            "explanation": q.get("explanation", ""),
            "difficulty": q.get("difficulty", "medium"),
            "knowledge_point": q.get("knowledge_point", "核心概念"),
            "chapter_id": q.get("chapter_id"),
        }
        for q in raw_questions
    ]
