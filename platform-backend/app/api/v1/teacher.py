"""教师教学与 AI 人工校准工作台 HTTP 路由层（薄控制器，全部业务逻辑委托至 teacher_service）。"""
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.enums import UserRole
from app.core.errors import PlatformError, get_request_id, ok_response
from app.core.rate_limit import limit_ai_quota
from app.core.security import require_roles
from app.models.entities import User
from app.schemas.teacher import (
    CalibratedOutlineDTO,
    ChapterRegenerateDTO,
    ChunkUploadCompleteDTO,
    ChunkUploadInitDTO,
    ClassCreateDTO,
    CourseCreateDTO,
    CoursePatchDTO,
    DocumentUploadDTO,
    EnrollmentPatchDTO,
    ExamCreateDTO,
    FaqCreateDTO,
    FaqPatchDTO,
    InboxResolveDTO,
    QuestionBatchGenDTO,
    QuestionManualCreateDTO,
    QuestionReviewPatchDTO,
    StudentRosterImportDTO,
    TeacherQualityFeedbackDTO,
    TutorConfigDTO,
    VideoUploadDTO,
)
from app.services.teacher_service import (
    build_teacher_course_analytics,
    complete_chunk_video_upload,
    create_class_for_course,
    create_course_exam,
    create_manual_question,
    create_new_course,
    create_pinned_faq,
    delete_course_document,
    delete_course_exam,
    delete_course_faq,
    delete_course_question,
    generate_questions_for_bank,
    get_tutor_config_dict,
    import_course_students_tabular,
    import_course_students_text,
    init_chunk_video_upload,
    publish_outline_version,
    query_course_exams,
    query_course_faqs,
    query_course_questions,
    query_course_students,
    query_teacher_courses,
    query_unanswered_inbox,
    query_video_outline_versions,
    regenerate_single_chapter,
    remove_student_enrollment,
    resolve_unanswered_inbox_item,
    save_calibrated_outline,
    save_chunk_part,
    submit_teacher_quality_feedback,
    sync_video_ai_job,
    update_course_faq,
    update_question_review,
    update_student_enrollment,
    update_teacher_course,
    upload_course_cover,
    upload_course_document,
    upload_real_document_file,
    upload_real_video_file_and_trigger_ai,
    upload_video_and_trigger_ai,
    upsert_tutor_config,
)

router = APIRouter(
    prefix="/api/v1/teacher",
    tags=["TeacherStudio"],
    dependencies=[Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN))],
)


@router.get("/courses")
async def list_teacher_courses(
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    return ok_response({"items": query_teacher_courses(db, teacher=teacher)}, request)


@router.post("/courses", status_code=201)
async def create_teacher_course(
    body: CourseCreateDTO,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    data = create_new_course(
        db,
        teacher=teacher,
        code=body.code,
        title=body.title,
        subtitle=body.subtitle,
        category=body.category,
        cover_url=body.cover_url,
        description=body.description,
        start_date=body.start_date,
        end_date=body.end_date,
        submit_for_review=body.submit_for_review,
    )
    return ok_response(data, request, status_code=201)


@router.patch("/courses/{course_id}")
async def patch_teacher_course(
    course_id: str,
    body: CoursePatchDTO,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    data = update_teacher_course(
        db,
        teacher=teacher,
        course_id=course_id,
        code=body.code,
        title=body.title,
        subtitle=body.subtitle,
        category=body.category,
        cover_url=body.cover_url,
        cover_color=body.cover_color,
        description=body.description,
        start_date=body.start_date,
        end_date=body.end_date,
        status=body.status,
    )
    return ok_response(data, request)


@router.post("/courses/{course_id}/cover-uploads", status_code=201)
async def upload_teacher_course_cover(
    course_id: str,
    request: Request,
    cover_file: UploadFile | None = File(default=None),
    file: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    target_upload = cover_file or file
    if not target_upload:
        raise PlatformError("MISSING_COVER_FILE", "请上传封面图片文件", status_code=400)
    data = await upload_course_cover(db, teacher=teacher, course_id=course_id, cover_file=target_upload)
    return ok_response(data, request, status_code=201)


@router.post("/courses/{course_id}/classes", status_code=201)
async def create_course_class(
    course_id: str,
    body: ClassCreateDTO,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    data = create_class_for_course(db, teacher=teacher, course_id=course_id, name=body.name, term=body.term)
    return ok_response(data, request, status_code=201)


@router.get("/courses/{course_id}/students")
async def list_course_students(
    course_id: str,
    request: Request,
    class_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    items = query_course_students(db, teacher=teacher, course_id=course_id, class_id=class_id)
    return ok_response({"items": items}, request)


@router.post("/courses/{course_id}/students/import", status_code=201)
async def import_course_students_endpoint(
    course_id: str,
    body: StudentRosterImportDTO,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    data = import_course_students_text(
        db,
        teacher=teacher,
        course_id=course_id,
        class_id=body.class_id,
        group_name=body.group_name,
        usernames_or_csv=body.usernames_or_csv,
    )
    return ok_response(data, request, status_code=201)


@router.post("/courses/{course_id}/students/xlsx-import", status_code=201)
async def xlsx_import_course_students_endpoint(
    course_id: str,
    request: Request,
    file: UploadFile | None = File(default=None),
    roster_file: UploadFile | None = File(default=None),
    class_id: str | None = Form(default=None),
    group_name: str = Form(default=""),
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    upload = file or roster_file
    if not upload:
        raise PlatformError("MISSING_ROSTER_FILE", "请上传 .xlsx 或 .csv 学生名单文件", status_code=400)
    raw_bytes = await upload.read()
    data = import_course_students_tabular(
        db,
        teacher=teacher,
        course_id=course_id,
        filename=upload.filename or "students.xlsx",
        raw_bytes=raw_bytes,
        class_id=class_id or None,
        group_name=group_name,
    )
    return ok_response(data, request, status_code=201)


@router.patch("/enrollments/{enrollment_id}")
async def patch_enrollment(
    enrollment_id: str,
    body: EnrollmentPatchDTO,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    data = update_student_enrollment(
        db,
        teacher=teacher,
        enrollment_id=enrollment_id,
        class_id=body.class_id,
        group_name=body.group_name,
    )
    return ok_response(data, request)


@router.delete("/enrollments/{enrollment_id}")
async def delete_enrollment(
    enrollment_id: str,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    data = remove_student_enrollment(db, teacher=teacher, enrollment_id=enrollment_id)
    return ok_response(data, request)


@router.post("/courses/{course_id}/videos", status_code=202)
async def upload_video(
    course_id: str,
    body: VideoUploadDTO,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    data = await upload_video_and_trigger_ai(
        db,
        teacher=teacher,
        course_id=course_id,
        title=body.title,
        media_url=body.media_url,
        duration_sec=body.duration_sec,
        raw_transcript_text=body.raw_transcript_text,
        knowledge_points=body.knowledge_points,
        run_inline=body.run_inline,
        request_id=get_request_id(request),
    )
    loc = f"/api/v1/teacher/videos/{data['video_id']}/job-sync"
    return ok_response(data, request, status_code=202, headers={"Location": loc})


@router.post("/courses/{course_id}/video-uploads", status_code=202)
async def upload_real_video(
    course_id: str,
    request: Request,
    video_file: UploadFile = File(...),
    subtitle_file: UploadFile | None = File(default=None),
    title: str = Form(default=""),
    knowledge_points: str = Form(default=""),
    run_inline: bool = Form(default=False),
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    kps = [s.strip() for s in knowledge_points.replace("，", ",").split(",") if s.strip()]
    data = await upload_real_video_file_and_trigger_ai(
        db,
        teacher=teacher,
        course_id=course_id,
        video_file=video_file,
        subtitle_file=subtitle_file,
        title=title,
        knowledge_points=kps,
        run_inline=run_inline,
        request_id=get_request_id(request),
    )
    loc = f"/api/v1/teacher/videos/{data['video_id']}/job-sync"
    return ok_response(data, request, status_code=202, headers={"Location": loc})


@router.post("/courses/{course_id}/video-uploads/init")
async def init_chunked_video_upload(
    course_id: str,
    body: ChunkUploadInitDTO,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    data, status_code = init_chunk_video_upload(
        db,
        teacher=teacher,
        course_id=course_id,
        filename=body.filename,
        title=body.title,
        total_size=body.total_size,
        total_chunks=body.total_chunks,
    )
    return ok_response(data, request, status_code=status_code)


@router.put("/video-uploads/{upload_id}/chunks/{chunk_index}")
async def put_video_upload_chunk(
    upload_id: str,
    chunk_index: int,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    content_type = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in content_type:
        form = await request.form()
        target_part = form.get("chunk_file") or form.get("file")
        raw_bytes = await target_part.read() if hasattr(target_part, "read") else b""
    else:
        raw_bytes = await request.body()
    data = save_chunk_part(
        db,
        teacher=teacher,
        upload_id=upload_id,
        chunk_index=chunk_index,
        chunk_bytes=raw_bytes,
    )
    return ok_response(data, request)


@router.post("/video-uploads/{upload_id}/complete", status_code=202)
async def finish_chunked_video_upload(
    upload_id: str,
    request: Request,
    body: ChunkUploadCompleteDTO | None = None,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    dto = body or ChunkUploadCompleteDTO()
    data = await complete_chunk_video_upload(
        db,
        teacher=teacher,
        upload_id=upload_id,
        title=dto.title,
        knowledge_points=dto.knowledge_points,
        run_inline=dto.run_inline,
        request_id=get_request_id(request),
    )
    loc = f"/api/v1/teacher/videos/{data['video_id']}/job-sync"
    return ok_response(data, request, status_code=202, headers={"Location": loc})


@router.post("/videos/{video_id}/job-sync")
async def sync_video_job(
    video_id: str,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    data = await sync_video_ai_job(db, teacher=teacher, video_id=video_id, request_id=get_request_id(request))
    return ok_response(data, request)


@router.get("/videos/{video_id}/outline-versions")
async def list_outline_versions(
    video_id: str,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    return ok_response(query_video_outline_versions(db, teacher=teacher, video_id=video_id), request)


@router.post("/videos/{video_id}/outline-versions", status_code=201)
async def create_calibrated_outline_version(
    video_id: str,
    body: CalibratedOutlineDTO,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    ver = save_calibrated_outline(
        db,
        teacher=teacher,
        video_id=video_id,
        chapters=body.chapters,
        change_note=body.change_note,
    )
    return ok_response(
        {
            "id": ver.id,
            "version_no": ver.version_no,
            "source": ver.source,
            "chapters": ver.chapters_json,
            "is_published": ver.is_published,
        },
        request,
        status_code=201,
    )


@router.post("/videos/{video_id}/outline-versions/{version_id}/publications")
async def publish_video_outline(
    video_id: str,
    version_id: str,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    ver = await publish_outline_version(
        db,
        teacher=teacher,
        video_id=video_id,
        version_id=version_id,
        request_id=get_request_id(request),
    )
    return ok_response(
        {
            "video_id": video_id,
            "published_version_id": ver.id,
            "version_no": ver.version_no,
            "outline_status": "published",
        },
        request,
    )


@router.post(
    "/videos/{video_id}/chapter-regenerations",
    dependencies=[Depends(limit_ai_quota)],
)
async def regenerate_video_chapter_endpoint(
    video_id: str,
    body: ChapterRegenerateDTO,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    data = await regenerate_single_chapter(
        db,
        teacher=teacher,
        video_id=video_id,
        start_sec=body.start_sec,
        end_sec=body.end_sec,
        current_title=body.current_title,
        instruction=body.instruction,
        request_id=get_request_id(request),
    )
    return ok_response(data, request)


@router.post("/courses/{course_id}/documents", status_code=201)
async def create_course_document(
    course_id: str,
    body: DocumentUploadDTO,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    doc = await upload_course_document(
        db,
        teacher=teacher,
        course_id=course_id,
        title=body.title,
        file_format=body.file_format,
        content_text=body.content_text,
        request_id=get_request_id(request),
    )
    return ok_response({"id": doc.id, "title": doc.title, "index_status": doc.index_status}, request, status_code=201)


@router.post("/courses/{course_id}/document-uploads", status_code=201)
async def upload_real_document(
    course_id: str,
    request: Request,
    document_file: UploadFile = File(...),
    title: str = Form(default=""),
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    data = await upload_real_document_file(
        db,
        teacher=teacher,
        course_id=course_id,
        document_file=document_file,
        title=title,
        request_id=get_request_id(request),
    )
    return ok_response(data, request, status_code=201)


@router.delete("/documents/{document_id}")
async def delete_teacher_document(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    data = await delete_course_document(
        db,
        teacher=teacher,
        document_id=document_id,
        request_id=get_request_id(request),
    )
    return ok_response(data, request)


@router.get("/courses/{course_id}/questions")
async def list_course_questions(
    course_id: str,
    request: Request,
    review_status: str | None = Query(default=None),
    knowledge_point: str | None = Query(default=None),
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    items = query_course_questions(
        db,
        teacher=teacher,
        course_id=course_id,
        review_status=review_status,
        knowledge_point=knowledge_point,
    )
    return ok_response({"items": items}, request)


@router.post("/courses/{course_id}/questions", status_code=201)
async def create_course_manual_question(
    course_id: str,
    body: QuestionManualCreateDTO,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    data = create_manual_question(
        db,
        teacher=teacher,
        course_id=course_id,
        chapter_id=body.chapter_id,
        question_type=body.question_type,
        stem=body.stem,
        options=body.options,
        answer=body.answer,
        explanation=body.explanation,
        difficulty=body.difficulty,
        knowledge_point=body.knowledge_point,
    )
    return ok_response(data, request, status_code=201)


@router.post(
    "/courses/{course_id}/question-generations",
    status_code=201,
    dependencies=[Depends(limit_ai_quota)],
)
async def trigger_question_generation(
    course_id: str,
    body: QuestionBatchGenDTO,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    created = await generate_questions_for_bank(
        db,
        teacher=teacher,
        course_id=course_id,
        chapter_ids=body.chapter_ids,
        knowledge_points=body.knowledge_points,
        question_types=body.question_types,
        difficulty=body.difficulty,
        count=body.count,
        request_id=get_request_id(request),
    )
    return ok_response(
        {"generated_count": len(created), "question_ids": [q.id for q in created]},
        request,
        status_code=201,
    )


@router.patch("/questions/{question_id}")
async def review_or_edit_question(
    question_id: str,
    body: QuestionReviewPatchDTO,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    data = update_question_review(
        db,
        teacher=teacher,
        question_id=question_id,
        stem=body.stem,
        options=body.options,
        answer=body.answer,
        explanation=body.explanation,
        difficulty=body.difficulty,
        knowledge_point=body.knowledge_point,
        question_type=body.question_type,
        chapter_id=body.chapter_id,
        review_status=body.review_status,
    )
    return ok_response(data, request)


@router.delete("/questions/{question_id}")
async def delete_question_endpoint(
    question_id: str,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    return ok_response(delete_course_question(db, teacher=teacher, question_id=question_id), request)


@router.get("/courses/{course_id}/exams")
async def list_course_exams_endpoint(
    course_id: str,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    return ok_response({"items": query_course_exams(db, teacher=teacher, course_id=course_id)}, request)


@router.post("/courses/{course_id}/exams", status_code=201)
async def publish_exam_paper(
    course_id: str,
    body: ExamCreateDTO,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    data = create_course_exam(
        db,
        teacher=teacher,
        course_id=course_id,
        title=body.title,
        question_ids=body.question_ids,
        time_limit_min=body.time_limit_min,
        max_attempts=body.max_attempts,
        target_class_id=body.target_class_id,
        start_time=body.start_time,
        end_time=body.end_time,
    )
    return ok_response(data, request, status_code=201)


@router.delete("/exams/{exam_id}")
async def delete_exam_endpoint(
    exam_id: str,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    return ok_response(delete_course_exam(db, teacher=teacher, exam_id=exam_id), request)


@router.get("/courses/{course_id}/unanswered-questions")
async def list_unanswered_inbox(
    course_id: str,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    return ok_response({"items": query_unanswered_inbox(db, teacher=teacher, course_id=course_id)}, request)


@router.post("/unanswered-questions/{item_id}/resolutions")
async def resolve_inbox_question(
    item_id: str,
    body: InboxResolveDTO,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    item = await resolve_unanswered_inbox_item(
        db,
        teacher=teacher,
        item_id=item_id,
        teacher_answer=body.teacher_answer,
        sync_to_kb=body.sync_to_kb,
        pin_as_faq=body.pin_as_faq,
        request_id=get_request_id(request),
    )
    return ok_response({"id": item.id, "status": item.status, "synced_to_kb": item.synced_to_kb}, request)


@router.get("/courses/{course_id}/tutor-config")
async def get_course_tutor_config(
    course_id: str,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    return ok_response(get_tutor_config_dict(db, teacher=teacher, course_id=course_id), request)


@router.put("/courses/{course_id}/tutor-config")
async def update_course_tutor_config(
    course_id: str,
    body: TutorConfigDTO,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    upsert_tutor_config(
        db,
        teacher=teacher,
        course_id=course_id,
        strict_course_only=body.strict_course_only,
        allow_out_of_syllabus=body.allow_out_of_syllabus,
        tone=body.tone,
        forbidden_topics=body.forbidden_topics,
    )
    return ok_response({"updated": True}, request)


@router.get("/courses/{course_id}/faqs")
async def list_course_faqs_endpoint(
    course_id: str,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    return ok_response({"items": query_course_faqs(db, teacher=teacher, course_id=course_id)}, request)


@router.post("/courses/{course_id}/faqs", status_code=201)
async def create_course_faq(
    course_id: str,
    body: FaqCreateDTO,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    faq_id = create_pinned_faq(
        db,
        teacher=teacher,
        course_id=course_id,
        question=body.question,
        answer=body.answer,
        is_pinned=body.is_pinned,
    )
    return ok_response({"id": faq_id}, request, status_code=201)


@router.patch("/faqs/{faq_id}")
async def patch_course_faq_endpoint(
    faq_id: str,
    body: FaqPatchDTO,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    data = update_course_faq(
        db,
        teacher=teacher,
        faq_id=faq_id,
        question=body.question,
        answer=body.answer,
        is_pinned=body.is_pinned,
    )
    return ok_response(data, request)


@router.delete("/faqs/{faq_id}")
async def delete_course_faq_endpoint(
    faq_id: str,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    return ok_response(delete_course_faq(db, teacher=teacher, faq_id=faq_id), request)


@router.post("/courses/{course_id}/quality-feedback", status_code=201)
async def report_course_ai_quality_issue(
    course_id: str,
    body: TeacherQualityFeedbackDTO,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    data = await submit_teacher_quality_feedback(
        db,
        teacher=teacher,
        course_id=course_id,
        sample_type=body.sample_type,
        input_text=body.input_text,
        model_output=body.model_output,
        reason=body.reason,
        request_id=get_request_id(request),
    )
    return ok_response(data, request, status_code=201)


@router.get("/courses/{course_id}/analytics")
async def get_teacher_analytics(
    course_id: str,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(require_roles(UserRole.TEACHER, UserRole.ADMIN)),
):
    data = await build_teacher_course_analytics(
        db,
        teacher=teacher,
        course_id=course_id,
        request_id=get_request_id(request),
    )
    return ok_response(data, request)
