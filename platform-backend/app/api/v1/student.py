"""学生学习域 HTTP 路由层（薄控制器，全部业务逻辑委托至 student_service，关键 AI 接口挂载角色配额限流）。"""
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.errors import get_request_id, ok_response
from app.core.rate_limit import limit_ai_quota
from app.core.security import get_current_user
from app.models.entities import User
from app.schemas.student import (
    LearningEventDTO,
    MessageFeedbackDTO,
    NoteCreateDTO,
    NotePatchDTO,
    QuizSessionStartDTO,
    QuizSubmissionDTO,
    TutorAskDTO,
    WrongQuestionPatchDTO,
)
from app.services.student_service import (
    ask_tutor_in_thread,
    build_student_dashboard,
    create_user_note,
    delete_user_note,
    get_student_video_playback,
    get_tutor_thread_detail,
    list_user_tutor_threads,
    patch_wrong_question_record,
    query_bookmarked_messages,
    query_user_notes,
    query_wrong_questions,
    record_message_feedback,
    record_watch_event,
    sanitize_session_for_student,
    start_student_quiz_session,
    stream_tutor_in_thread,
    submit_student_quiz_session,
    update_user_note,
)

router = APIRouter(prefix="/api/v1", tags=["StudentLearning"])


@router.get("/videos/{video_id}/playback")
async def get_video_playback(
    video_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return ok_response(get_student_video_playback(db, video_id=video_id, user=user), request)


@router.post("/users/me/learning-events", status_code=201)
async def create_learning_event(
    body: LearningEventDTO,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    event_id = record_watch_event(
        db,
        user=user,
        course_id=body.course_id,
        video_id=body.video_id,
        event_type=body.event_type,
        position_sec=body.position_sec,
        watched_seconds=body.watched_seconds,
        completed=body.completed,
    )
    return ok_response({"event_id": event_id, "streak_days": user.streak_days}, request, status_code=201)


@router.get("/users/me/notes")
async def list_my_notes(
    request: Request,
    video_id: str | None = Query(default=None),
    course_id: str | None = Query(default=None),
    bookmarks_only: bool = Query(default=False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    items = query_user_notes(
        db,
        user=user,
        video_id=video_id,
        course_id=course_id,
        bookmarks_only=bookmarks_only,
    )
    return ok_response({"items": items}, request)


@router.post("/users/me/notes", status_code=201)
async def create_my_note(
    body: NoteCreateDTO,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    data = create_user_note(
        db,
        user=user,
        course_id=body.course_id,
        video_id=body.video_id,
        timestamp_sec=body.timestamp_sec,
        content=body.content,
        is_bookmark=body.is_bookmark,
    )
    return ok_response(data, request, status_code=201)


@router.patch("/users/me/notes/{note_id}")
async def patch_my_note(
    note_id: str,
    body: NotePatchDTO,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    data = update_user_note(
        db,
        user=user,
        note_id=note_id,
        content=body.content,
        is_bookmark=body.is_bookmark,
        timestamp_sec=body.timestamp_sec,
    )
    return ok_response(data, request)


@router.delete("/users/me/notes/{note_id}")
async def delete_my_note(
    note_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    delete_user_note(db, user=user, note_id=note_id)
    return ok_response({"deleted": True}, request)


@router.get("/users/me/bookmarked-messages")
async def list_my_bookmarked_messages(
    request: Request,
    course_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    items = query_bookmarked_messages(db, user=user, course_id=course_id)
    return ok_response({"items": items}, request)


@router.get("/courses/{course_id}/tutor-threads")
async def list_course_tutor_threads(
    course_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return ok_response({"items": list_user_tutor_threads(db, user=user, course_id=course_id)}, request)


@router.post(
    "/courses/{course_id}/tutor-threads",
    status_code=201,
    dependencies=[Depends(limit_ai_quota)],
)
async def create_thread_and_ask(
    course_id: str,
    body: TutorAskDTO,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    res = await ask_tutor_in_thread(
        db,
        user=user,
        course_id=course_id,
        thread_id=None,
        question=body.question,
        quick_action=body.quick_action,
        video_context=body.video_context,
        request_id=get_request_id(request),
    )
    return ok_response(res, request, status_code=201)


@router.post(
    "/courses/{course_id}/tutor-threads/stream",
    dependencies=[Depends(limit_ai_quota)],
)
async def create_thread_and_ask_stream(
    course_id: str,
    body: TutorAskDTO,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    req_id = get_request_id(request)
    return StreamingResponse(
        stream_tutor_in_thread(
            db,
            user=user,
            course_id=course_id,
            thread_id=None,
            question=body.question,
            quick_action=body.quick_action,
            video_context=body.video_context,
            request_id=req_id,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Request-ID": req_id},
    )


@router.get("/tutor-threads/{thread_id}/messages")
async def get_thread_messages(
    thread_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return ok_response(get_tutor_thread_detail(db, user=user, thread_id=thread_id), request)


@router.post(
    "/tutor-threads/{thread_id}/messages",
    status_code=201,
    dependencies=[Depends(limit_ai_quota)],
)
async def append_thread_message(
    thread_id: str,
    body: TutorAskDTO,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    res = await ask_tutor_in_thread(
        db,
        user=user,
        course_id=None,
        thread_id=thread_id,
        question=body.question,
        quick_action=body.quick_action,
        video_context=body.video_context,
        request_id=get_request_id(request),
    )
    return ok_response(res, request, status_code=201)


@router.post(
    "/tutor-threads/{thread_id}/messages/stream",
    dependencies=[Depends(limit_ai_quota)],
)
async def append_thread_message_stream(
    thread_id: str,
    body: TutorAskDTO,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    req_id = get_request_id(request)
    return StreamingResponse(
        stream_tutor_in_thread(
            db,
            user=user,
            course_id=None,
            thread_id=thread_id,
            question=body.question,
            quick_action=body.quick_action,
            video_context=body.video_context,
            request_id=req_id,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Request-ID": req_id},
    )


@router.post("/tutor-messages/{message_id}/feedback")
async def submit_message_feedback(
    message_id: str,
    body: MessageFeedbackDTO,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    res = await record_message_feedback(
        db,
        user=user,
        message_id=message_id,
        vote=body.vote,
        bookmarked=body.bookmarked,
        reason=body.reason,
        request_id=get_request_id(request),
    )
    return ok_response(res, request)


@router.post(
    "/courses/{course_id}/quiz-sessions",
    status_code=201,
    dependencies=[Depends(limit_ai_quota)],
)
async def create_quiz_session(
    course_id: str,
    body: QuizSessionStartDTO,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = await start_student_quiz_session(
        db,
        user=user,
        course_id=course_id,
        session_type=body.session_type,
        chapter_id=body.chapter_id,
        exam_id=body.exam_id,
        wrong_record_ids=body.wrong_record_ids,
        count=body.count,
        request_id=get_request_id(request),
    )
    return ok_response(sanitize_session_for_student(session), request, status_code=201)


@router.post("/quiz-sessions/{session_id}/submissions", status_code=201)
async def submit_quiz_answers(
    session_id: str,
    body: QuizSubmissionDTO,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    graded = submit_student_quiz_session(db, user=user, session_id=session_id, answers=body.answers)
    return ok_response(
        {
            "session_id": graded.id,
            "title": graded.title,
            "score": graded.score,
            "total_score": graded.total_score,
            "submitted_at": graded.submitted_at,
            "details": graded.grading_detail_json,
        },
        request,
        status_code=201,
    )


@router.get("/users/me/wrong-questions")
async def list_my_wrong_questions(
    request: Request,
    course_id: str | None = Query(default=None),
    knowledge_point: str | None = Query(default=None),
    include_mastered: bool = Query(default=True),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    items = query_wrong_questions(
        db,
        user=user,
        course_id=course_id,
        knowledge_point=knowledge_point,
        include_mastered=include_mastered,
    )
    return ok_response({"items": items}, request)


@router.patch("/users/me/wrong-questions/{record_id}")
async def update_my_wrong_question(
    record_id: str,
    body: WrongQuestionPatchDTO,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    data = patch_wrong_question_record(
        db,
        user=user,
        record_id=record_id,
        is_mastered=body.is_mastered,
        review_note=body.review_note,
    )
    return ok_response(data, request)


@router.get("/users/me/dashboard")
async def get_my_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    data = await build_student_dashboard(db, user=user, request_id=get_request_id(request))
    return ok_response(data, request)
