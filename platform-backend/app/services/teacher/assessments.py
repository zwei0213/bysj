"""教师题库管理、AI 批量出题与试卷组卷服务。"""

from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.clients.ai_service_client import ai_client
from app.core.enums import QuestionReviewStatus
from app.core.errors import PlatformError
from app.models.entities import ExamPaper, QuestionBankItem, User
from app.services.audit_helper import record_audit

from app.services.teacher.courses import ensure_course_owner_or_admin

def _serialize_question_item(q: QuestionBankItem) -> dict[str, Any]:
    return {
        "id": q.id,
        "chapter_id": q.chapter_id,
        "question_type": q.question_type,
        "stem": q.stem,
        "options": q.options_json,
        "answer": q.answer_json,
        "explanation": q.explanation,
        "difficulty": q.difficulty,
        "knowledge_point": q.knowledge_point,
        "review_status": q.review_status,
        "source": q.source,
        "created_at": q.created_at,
    }


def query_course_questions(
    db: Session,
    *,
    teacher: User,
    course_id: str,
    review_status: str | None = None,
    knowledge_point: str | None = None,
) -> list[dict[str, Any]]:
    ensure_course_owner_or_admin(db, course_id, teacher)
    stmt = select(QuestionBankItem).where(QuestionBankItem.course_id == course_id)
    if review_status:
        stmt = stmt.where(QuestionBankItem.review_status == review_status)
    if knowledge_point and knowledge_point.strip():
        stmt = stmt.where(QuestionBankItem.knowledge_point.ilike(f"%{knowledge_point.strip()}%"))
    items = db.scalars(stmt.order_by(QuestionBankItem.created_at.desc())).all()
    return [_serialize_question_item(q) for q in items]


def create_manual_question(
    db: Session,
    *,
    teacher: User,
    course_id: str,
    chapter_id: str | None,
    question_type: str,
    stem: str,
    options: list[dict[str, str]],
    answer: list[str],
    explanation: str,
    difficulty: str,
    knowledge_point: str,
) -> dict[str, Any]:
    ensure_course_owner_or_admin(db, course_id, teacher)
    item = QuestionBankItem(
        course_id=course_id,
        chapter_id=chapter_id,
        question_type=question_type,
        stem=stem.strip(),
        options_json=options,
        answer_json=[str(a).strip().upper() for a in answer if str(a).strip()],
        explanation=explanation.strip(),
        difficulty=difficulty,
        knowledge_point=knowledge_point.strip() or "核心概念",
        review_status=QuestionReviewStatus.APPROVED,
        source="manual",
    )
    db.add(item)
    db.flush()
    record_audit(
        db,
        actor=teacher,
        action="CREATE_MANUAL_QUESTION",
        target_type="question_bank_item",
        target_id=item.id,
        detail=f"手动录入试题 ({item.question_type}, {item.knowledge_point})",
    )
    db.commit()
    return _serialize_question_item(item)


async def generate_questions_for_bank(
    db: Session,
    *,
    teacher: User,
    course_id: str,
    chapter_ids: list[str],
    knowledge_points: list[str],
    question_types: list[str],
    difficulty: str,
    count: int,
    request_id: str | None = None,
) -> list[QuestionBankItem]:
    ensure_course_owner_or_admin(db, course_id, teacher)
    ai_res = await ai_client.generate_assessments(
        {
            "course_id": course_id,
            "mode": "teacher_bank",
            "scope": {
                "chapter_ids": chapter_ids,
                "knowledge_points": knowledge_points,
                "question_types": question_types or ["single_choice", "multiple_choice", "true_false"],
                "difficulty": difficulty,
                "count": count,
            },
        },
        request_id=request_id,
        caller_role=teacher.role,
        caller_id=teacher.id,
    )
    created: list[QuestionBankItem] = []
    for q in ai_res.get("questions", []):
        item = QuestionBankItem(
            course_id=course_id,
            chapter_id=q.get("chapter_id"),
            question_type=q.get("question_type", "single_choice"),
            stem=q.get("stem", ""),
            options_json=q.get("options", []),
            answer_json=q.get("answer", ["A"]),
            explanation=q.get("explanation", ""),
            difficulty=q.get("difficulty", "medium"),
            knowledge_point=q.get("knowledge_point", "课程知识点"),
            review_status=QuestionReviewStatus.PENDING_REVIEW,
            source="ai",
        )
        db.add(item)
        created.append(item)

    db.flush()
    record_audit(
        db,
        actor=teacher,
        action="AI_GENERATE_QUESTIONS",
        target_type="course",
        target_id=course_id,
        detail=f"AI 批量生成 {len(created)} 道待审试题",
    )
    db.commit()
    return created


def update_question_review(
    db: Session,
    *,
    teacher: User,
    question_id: str,
    stem: str | None = None,
    options: list[dict[str, str]] | None = None,
    answer: list[str] | None = None,
    explanation: str | None = None,
    difficulty: str | None = None,
    knowledge_point: str | None = None,
    question_type: str | None = None,
    chapter_id: str | None = None,
    review_status: str | None = None,
) -> dict[str, Any]:
    q = db.get(QuestionBankItem, question_id)
    if not q:
        raise PlatformError("QUESTION_NOT_FOUND", "题目不存在", status_code=404)
    ensure_course_owner_or_admin(db, q.course_id, teacher)

    if stem is not None:
        q.stem = stem
    if options is not None:
        q.options_json = options
    if answer is not None:
        q.answer_json = [str(a).strip().upper() for a in answer if str(a).strip()]
    if explanation is not None:
        q.explanation = explanation
    if difficulty is not None:
        q.difficulty = difficulty
    if knowledge_point is not None:
        q.knowledge_point = knowledge_point
    if question_type is not None:
        q.question_type = question_type
    if chapter_id is not None:
        q.chapter_id = chapter_id or None
    if review_status is not None:
        q.review_status = review_status

    record_audit(
        db,
        actor=teacher,
        action="REVIEW_QUESTION",
        target_type="question_bank_item",
        target_id=q.id,
        detail=f"编辑/审题状态更新为 {q.review_status}",
    )
    db.commit()
    return _serialize_question_item(q)


def delete_course_question(db: Session, *, teacher: User, question_id: str) -> dict[str, Any]:
    q = db.get(QuestionBankItem, question_id)
    if not q:
        raise PlatformError("QUESTION_NOT_FOUND", "题目不存在", status_code=404)
    ensure_course_owner_or_admin(db, q.course_id, teacher)
    db.delete(q)
    record_audit(
        db,
        actor=teacher,
        action="DELETE_QUESTION",
        target_type="question_bank_item",
        target_id=question_id,
        detail="从题库删除试题",
    )
    db.commit()
    return {"deleted": True, "id": question_id}


def query_course_exams(db: Session, *, teacher: User, course_id: str) -> list[dict[str, Any]]:
    ensure_course_owner_or_admin(db, course_id, teacher)
    rows = db.scalars(
        select(ExamPaper).where(ExamPaper.course_id == course_id).order_by(ExamPaper.created_at.desc())
    ).all()
    return [
        {
            "id": ex.id,
            "title": ex.title,
            "question_ids": ex.question_ids_json,
            "question_count": len(ex.question_ids_json),
            "time_limit_min": ex.time_limit_min,
            "max_attempts": ex.max_attempts,
            "target_class_id": ex.target_class_id,
            "start_time": ex.start_time,
            "end_time": ex.end_time,
            "is_published": ex.is_published,
            "created_at": ex.created_at,
        }
        for ex in rows
    ]


def create_course_exam(
    db: Session,
    *,
    teacher: User,
    course_id: str,
    title: str,
    question_ids: list[str],
    time_limit_min: int,
    max_attempts: int,
    target_class_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict[str, Any]:
    ensure_course_owner_or_admin(db, course_id, teacher)
    exam = ExamPaper(
        course_id=course_id,
        title=title.strip(),
        question_ids_json=question_ids,
        time_limit_min=time_limit_min,
        max_attempts=max_attempts,
        target_class_id=target_class_id or None,
        start_time=start_time or None,
        end_time=end_time or None,
        is_published=True,
    )
    db.add(exam)
    db.flush()
    record_audit(
        db,
        actor=teacher,
        action="CREATE_EXAM_PAPER",
        target_type="exam_paper",
        target_id=exam.id,
        detail=f"发布试卷《{exam.title}》(题数={len(question_ids)}, 限时={time_limit_min}min, 限次={max_attempts})",
    )
    db.commit()
    return {
        "id": exam.id,
        "title": exam.title,
        "question_count": len(question_ids),
        "time_limit_min": exam.time_limit_min,
        "max_attempts": exam.max_attempts,
        "target_class_id": exam.target_class_id,
        "start_time": exam.start_time,
        "end_time": exam.end_time,
    }


def delete_course_exam(db: Session, *, teacher: User, exam_id: str) -> dict[str, Any]:
    exam = db.get(ExamPaper, exam_id)
    if not exam:
        raise PlatformError("EXAM_NOT_FOUND", "试卷不存在", status_code=404)
    ensure_course_owner_or_admin(db, exam.course_id, teacher)
    title_bak = exam.title
    db.delete(exam)
    record_audit(
        db,
        actor=teacher,
        action="DELETE_EXAM_PAPER",
        target_type="exam_paper",
        target_id=exam_id,
        detail=f"删除试卷《{title_bak}》",
    )
    db.commit()
    return {"deleted": True, "id": exam_id}
