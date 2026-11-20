"""管理端域 5：平台总览看板与 CSV/XLSX 多维报表导出。"""

from collections import defaultdict
import csv
from datetime import datetime, timedelta, timezone
import io
from typing import Any
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session
from app.clients.ai_service_client import ai_client
from app.core.enums import CourseStatus, QuizSessionStatus, TicketStatus, UserRole
from app.models.entities import (
    AuditLog,
    ClassGroup,
    Course,
    Enrollment,
    LearningEvent,
    QualitySample,
    QuestionBankItem,
    QuizSession,
    SupportTicket,
    TutorMessage,
    User,
)
from app.services.xlsx_service import build_xlsx_bytes

def _in_date_window(ts_iso: str | None, start_date: str | None, end_date: str | None) -> bool:
    if not start_date and not end_date:
        return True
    if not ts_iso:
        return False
    date_part = str(ts_iso)[:10]
    if start_date and date_part < str(start_date)[:10]:
        return False
    if end_date and date_part > str(end_date)[:10]:
        return False
    return True


async def build_admin_overview(db: Session, *, request_id: str | None = None) -> dict[str, Any]:
    """管理端域 5（平台总览看板）+ 域 3（AI 算力用量概览）聚合指标（全 SQL 侧聚合，零全表内存扫描）。"""
    total_registered_users = int(db.scalar(select(func.count()).select_from(User)) or 0)
    enabled_users_count = int(db.scalar(select(func.count()).select_from(User).where(User.is_active.is_(True))) or 0)

    engaged_ids: set[str] = set()
    for col in (
        LearningEvent.user_id,
        QuizSession.user_id,
        TutorMessage.user_id,
        Enrollment.student_id,
        Course.teacher_id,
        AuditLog.actor_id,
    ):
        engaged_ids.update(x for x in db.scalars(select(col).distinct()).all() if x)

    recent_cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    active_conds = [User.last_study_date != "", User.created_at >= recent_cutoff]
    if engaged_ids:
        active_conds.append(User.id.in_(engaged_ids))
    active_users_count = int(
        db.scalar(select(func.count()).select_from(User).where(User.is_active.is_(True), or_(*active_conds))) or 0
    )
    if active_users_count == 0:
        active_users_count = enabled_users_count

    total_courses = int(db.scalar(select(func.count()).select_from(Course)) or 0)
    pending_courses = int(
        db.scalar(select(func.count()).select_from(Course).where(Course.status == CourseStatus.PENDING_REVIEW)) or 0
    )
    watch_seconds = float(db.scalar(select(func.sum(LearningEvent.watched_seconds))) or 0)
    qa_count = int(db.scalar(select(func.count()).select_from(TutorMessage).where(TutorMessage.role == "user")) or 0)
    question_count = int(db.scalar(select(func.count()).select_from(QuestionBankItem)) or 0)

    total_quiz = int(db.scalar(select(func.count()).select_from(QuizSession)) or 0)
    submitted_quiz = int(
        db.scalar(select(func.count()).select_from(QuizSession).where(QuizSession.status == QuizSessionStatus.SUBMITTED))
        or 0
    )
    quiz_completion_pct = round((submitted_quiz / max(1, total_quiz)) * 100, 1) if total_quiz else 100.0

    courses = list(db.scalars(select(Course)).all())
    enr_stats = {
        row[0]: (int(row[1]), int(row[2] or 0))
        for row in db.execute(
            select(
                Enrollment.course_id,
                func.count(),
                func.sum(case((Enrollment.progress_pct < 20.0, 1), else_=0)),
            ).group_by(Enrollment.course_id)
        ).all()
    }
    qa_by_course = dict(
        db.execute(
            select(TutorMessage.course_id, func.count())
            .where(TutorMessage.role == "user")
            .group_by(TutorMessage.course_id)
        ).all()
    )

    course_rankings = []
    for c in courses:
        enrolled_cnt, at_risk_cnt = enr_stats.get(c.id, (0, 0))
        course_rankings.append({
            "course_id": c.id,
            "code": c.code,
            "title": c.title,
            "status": c.status,
            "enrolled_count": enrolled_cnt,
            "ai_qa_count": int(qa_by_course.get(c.id, 0)),
            "dropout_risk_count": at_risk_cnt,
            "storage_used_mb": c.storage_used_mb,
            "storage_quota_mb": c.storage_quota_mb,
        })
    course_rankings.sort(key=lambda x: (x["ai_qa_count"], x["enrolled_count"]), reverse=True)

    ai_health = await ai_client.ops_health(request_id=request_id)
    ai_usage = await ai_client.ops_usage_metrics(request_id=request_id)
    pending_samples = int(
        db.scalar(select(func.count()).select_from(QualitySample).where(QualitySample.status == "pending")) or 0
    )
    open_tickets = int(
        db.scalar(select(func.count()).select_from(SupportTicket).where(SupportTicket.status != TicketStatus.RESOLVED))
        or 0
    )
    pending_teachers = int(
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == UserRole.TEACHER, User.teacher_approved.is_(False))
        )
        or 0
    )

    return {
        "platform_metrics": {
            "active_users": active_users_count,
            "engaged_users": len(engaged_ids),
            "total_registered_users": total_registered_users,
            "pending_teacher_reviews": pending_teachers,
            "total_courses": total_courses,
            "pending_course_reviews": pending_courses,
            "watch_hours": round(watch_seconds / 3600.0, 2),
            "ai_qa_count": qa_count,
            "generated_questions": question_count,
            "quiz_completion_rate_pct": quiz_completion_pct,
            "pending_quality_samples": pending_samples,
            "open_tickets": open_tickets,
        },
        "course_rankings": course_rankings,
        "ai_service_health": ai_health,
        "ai_usage_telemetry": ai_usage,
    }


def _collect_course_report_table(
    db: Session,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    course_id: str | None = None,
    class_id: str | None = None,
) -> tuple[list[str], list[list[Any]]]:
    course_stmt = select(Course).order_by(Course.created_at.desc())
    if course_id:
        course_stmt = course_stmt.where(Course.id == course_id)
    if class_id:
        cls = db.get(ClassGroup, class_id)
        if cls:
            course_stmt = course_stmt.where(Course.id == cls.course_id)
        else:
            return (
                ["课程ID", "课程编码", "课程名称", "状态", "分类", "选课人数", "学习时长(分钟)", "AI提问次数", "测验提交数", "测验均分", "题库题量", "存储占用(MB)"],
                [],
            )

    courses = list(db.scalars(course_stmt).all())
    if not courses:
        return (
            ["课程ID", "课程编码", "课程名称", "状态", "分类", "选课人数", "学习时长(分钟)", "AI提问次数", "测验提交数", "测验均分", "题库题量", "存储占用(MB)"],
            [],
        )

    c_ids = [c.id for c in courses]
    enr_stmt = select(Enrollment).where(Enrollment.course_id.in_(c_ids))
    if class_id:
        enr_stmt = enr_stmt.where(Enrollment.class_id == class_id)
    all_enrs = [
        e for e in db.scalars(enr_stmt).all() if _in_date_window(e.created_at, start_date, end_date)
    ]
    class_student_filter: set[str] | None = {e.student_id for e in all_enrs} if class_id else None

    enr_counts: dict[str, int] = defaultdict(int)
    for e in all_enrs:
        enr_counts[e.course_id] += 1

    watch_secs: dict[str, int] = defaultdict(int)
    for ev in db.scalars(select(LearningEvent).where(LearningEvent.course_id.in_(c_ids))).all():
        if class_student_filter is not None and ev.user_id not in class_student_filter:
            continue
        if _in_date_window(ev.created_at, start_date, end_date):
            watch_secs[ev.course_id] += int(ev.watched_seconds or 0)

    qa_counts: dict[str, int] = defaultdict(int)
    for m in db.scalars(
        select(TutorMessage).where(TutorMessage.course_id.in_(c_ids), TutorMessage.role == "user")
    ).all():
        if class_student_filter is not None and m.user_id not in class_student_filter:
            continue
        if _in_date_window(m.created_at, start_date, end_date):
            qa_counts[m.course_id] += 1

    quiz_scores: dict[str, list[float]] = defaultdict(list)
    for qs in db.scalars(
        select(QuizSession).where(
            QuizSession.course_id.in_(c_ids),
            QuizSession.status == QuizSessionStatus.SUBMITTED,
        )
    ).all():
        if class_student_filter is not None and qs.user_id not in class_student_filter:
            continue
        if _in_date_window(qs.submitted_at or qs.created_at, start_date, end_date):
            quiz_scores[qs.course_id].append(float(qs.score or 0.0))

    q_counts: dict[str, int] = defaultdict(int)
    for qb in db.scalars(select(QuestionBankItem).where(QuestionBankItem.course_id.in_(c_ids))).all():
        if _in_date_window(qb.created_at, start_date, end_date):
            q_counts[qb.course_id] += 1

    status_cn_map = {
        "published": "已发布上架",
        "pending_review": "待审核",
        "draft": "草稿",
        "rejected": "审核驳回",
        "closed": "已结课",
        "archived": "已归档",
        "takedown": "已下架",
    }
    headers = [
        "课程编号",
        "课程代码",
        "课程名称",
        "课程状态",
        "所属分类",
        "选课人数",
        "学习时长(分钟)",
        "智能提问次数",
        "测验提交数",
        "测验均分",
        "题库题量",
        "存储占用(兆字节)",
    ]
    rows: list[list[Any]] = []
    for c in courses:
        sc_list = quiz_scores[c.id]
        avg_sc = round(sum(sc_list) / len(sc_list), 1) if sc_list else 0.0
        rows.append([
            c.id,
            c.code or "-",
            c.title,
            status_cn_map.get(c.status, c.status),
            c.category,
            int(enr_counts.get(c.id, 0)),
            round(watch_secs.get(c.id, 0) / 60.0, 1),
            int(qa_counts.get(c.id, 0)),
            len(sc_list),
            avg_sc,
            int(q_counts.get(c.id, 0)),
            float(c.storage_used_mb or 0.0),
        ])
    return headers, rows


def export_platform_report_csv(
    db: Session,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    course_id: str | None = None,
    class_id: str | None = None,
) -> str:
    """导出 Excel 可直接打开的 UTF-8 BOM 运营报表（支持起止日期、课程与班级多维过滤）。"""
    headers, rows = _collect_course_report_table(
        db,
        start_date=start_date,
        end_date=end_date,
        course_id=course_id,
        class_id=class_id,
    )
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf)
    writer.writerow(headers)
    for r in rows:
        writer.writerow(r)
    return buf.getvalue()


def export_platform_report_xlsx(
    db: Session,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    course_id: str | None = None,
    class_id: str | None = None,
) -> bytes:
    """导出零外部依赖原生 OpenXML `.xlsx` 运营报表字节流。"""
    headers, rows = _collect_course_report_table(
        db,
        start_date=start_date,
        end_date=end_date,
        course_id=course_id,
        class_id=class_id,
    )
    return build_xlsx_bytes("平台课程运营报表", headers, rows)
