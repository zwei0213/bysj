"""管理端六大治理域 HTTP 路由层（薄控制器，全部数据库与代理逻辑委托至 admin_service 与 ai_client）。"""
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session
from app.clients.ai_service_client import ai_client
from app.core.database import get_db
from app.core.enums import UserRole
from app.core.errors import get_request_id, ok_response
from app.core.rate_limit import get_role_ai_rate_limits, update_role_ai_rate_limits
from app.core.security import require_roles
from app.models.entities import User
from app.schemas.admin import (
    AdminRetryDTO,
    AdminRuntimePatchDTO,
    AnnouncementCreateDTO,
    BatchClassAssignDTO,
    BatchUserImportDTO,
    CategoryCreateDTO,
    CategoryPatchDTO,
    CourseGovernancePatchDTO,
    OrgCreateDTO,
    OrgPatchDTO,
    PermissionsMatrixPutDTO,
    SampleResolutionDTO,
    SpotCheckCreateDTO,
    SpotCheckDrawDTO,
    TakedownDTO,
    TicketPatchDTO,
    UserCreateDTO,
    UserPatchDTO,
)
from app.services.admin_service import (
    apply_course_governance,
    batch_assign_students_to_class,
    batch_import_users_from_csv,
    batch_import_users_from_tabular,
    build_admin_overview,
    build_system_status_panorama,
    cleanup_expired_courses,
    create_category,
    create_managed_user,
    create_org_node,
    create_spot_check,
    create_system_announcement,
    delete_category,
    delete_managed_user,
    delete_org_node,
    delete_system_announcement,
    draw_spot_check_candidates,
    export_platform_report_csv,
    export_platform_report_xlsx,
    get_permissions_matrix,
    list_admin_ai_jobs,
    list_merged_ai_indexes,
    list_recent_audit_logs,
    query_all_announcements,
    query_all_categories,
    query_all_courses_for_governance,
    query_course_resources_for_admin,
    query_organizations,
    query_quality_samples,
    query_support_tickets,
    query_users,
    resolve_sample_item,
    takedown_course_document,
    takedown_course_video,
    update_category,
    update_managed_user,
    update_org_node,
    update_permissions_matrix,
    update_ticket_status,
)
from app.services.audit_helper import record_audit

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["AdminGovernance"],
    dependencies=[Depends(require_roles(UserRole.ADMIN))],
)


# ============================================================
# 域 5：平台总览看板与多维报表导出 (.csv / .xlsx)
# ============================================================
@router.get("/overview")
async def get_admin_overview(request: Request, db: Session = Depends(get_db)):
    return ok_response(await build_admin_overview(db, request_id=get_request_id(request)), request)


@router.get("/reports/courses.csv")
async def download_courses_report_csv(
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    course_id: str | None = Query(default=None),
    class_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    csv_text = export_platform_report_csv(
        db,
        start_date=start_date,
        end_date=end_date,
        course_id=course_id,
        class_id=class_id,
    )
    return Response(
        content=csv_text.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="platform_courses_report.csv"'},
    )


@router.get("/reports/courses.xlsx")
async def download_courses_report_xlsx(
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    course_id: str | None = Query(default=None),
    class_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    xlsx_bytes = export_platform_report_xlsx(
        db,
        start_date=start_date,
        end_date=end_date,
        course_id=course_id,
        class_id=class_id,
    )
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="platform_courses_report.xlsx"'},
    )


# ============================================================
# 域 1：组织架构、账号管理、XLSX 批量导入、权限矩阵与批量分班
# ============================================================
@router.get("/organizations")
async def list_organizations(request: Request, db: Session = Depends(get_db)):
    return ok_response({"items": query_organizations(db)}, request)


@router.post("/organizations", status_code=201)
async def create_organization(
    body: OrgCreateDTO,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(UserRole.ADMIN)),
):
    data = create_org_node(db, admin=admin, name=body.name, org_type=body.org_type, parent_id=body.parent_id)
    return ok_response(data, request, status_code=201)


@router.patch("/organizations/{org_id}")
async def patch_organization(
    org_id: str,
    body: OrgPatchDTO,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(UserRole.ADMIN)),
):
    data = update_org_node(
        db,
        admin=admin,
        org_id=org_id,
        name=body.name,
        org_type=body.org_type,
        parent_id=body.parent_id,
    )
    return ok_response(data, request)


@router.delete("/organizations/{org_id}")
async def delete_organization(
    org_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(UserRole.ADMIN)),
):
    return ok_response(delete_org_node(db, admin=admin, org_id=org_id), request)


@router.get("/users")
async def list_users(
    request: Request,
    role: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    return ok_response({"items": query_users(db, role=role, keyword=keyword)}, request)


@router.post("/users", status_code=201)
async def create_user_account(
    body: UserCreateDTO,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(UserRole.ADMIN)),
):
    data = create_managed_user(
        db,
        admin=admin,
        username=body.username,
        display_name=body.display_name,
        email=body.email,
        password=body.password,
        role=body.role,
        org_id=body.org_id,
    )
    return ok_response(data, request, status_code=201)


@router.post("/users/import", status_code=201)
async def batch_import_users(
    body: BatchUserImportDTO,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(UserRole.ADMIN)),
):
    data = batch_import_users_from_csv(db, admin=admin, csv_text=body.csv_text, org_id=body.org_id)
    return ok_response(data, request, status_code=201)


@router.post("/users/xlsx-import", status_code=201)
async def xlsx_import_users(
    request: Request,
    file: UploadFile = File(...),
    org_id: str | None = Form(default=None),
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(UserRole.ADMIN)),
):
    raw_bytes = await file.read()
    data = batch_import_users_from_tabular(
        db,
        admin=admin,
        filename=file.filename or "users.xlsx",
        raw_bytes=raw_bytes,
        org_id=org_id or None,
    )
    return ok_response(data, request, status_code=201)


@router.patch("/users/{user_id}")
async def update_user_account(
    user_id: str,
    body: UserPatchDTO,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(UserRole.ADMIN)),
):
    data = update_managed_user(
        db,
        admin=admin,
        user_id=user_id,
        display_name=body.display_name,
        email=body.email,
        org_id=body.org_id,
        role=body.role,
        is_active=body.is_active,
        teacher_approved=body.teacher_approved,
        teacher_status=body.teacher_status,
        reject_reason=body.reject_reason,
        reset_password=body.reset_password,
    )
    return ok_response(data, request)


@router.delete("/users/{user_id}")
async def delete_user_account(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(UserRole.ADMIN)),
):
    return ok_response(delete_managed_user(db, admin=admin, user_id=user_id), request)


@router.get("/permissions-matrix")
async def get_admin_permissions_matrix(request: Request, db: Session = Depends(get_db)):
    return ok_response(get_permissions_matrix(db), request)


@router.put("/permissions-matrix")
async def put_admin_permissions_matrix(
    body: PermissionsMatrixPutDTO,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(UserRole.ADMIN)),
):
    data = update_permissions_matrix(db, admin=admin, roles=body.roles, items=body.items)
    return ok_response(data, request)


@router.post("/classes/batch-assign")
async def admin_batch_assign_classes(
    body: BatchClassAssignDTO,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(UserRole.ADMIN)),
):
    data = batch_assign_students_to_class(
        db,
        admin=admin,
        course_id=body.course_id,
        class_id=body.class_id,
        student_ids=body.student_ids,
        source_org_id=body.source_org_id,
        group_name=body.group_name,
    )
    return ok_response(data, request)


# ============================================================
# 域 2：课程审核上下架、分类目录、资源审查与合规下架、过期清理
# ============================================================
@router.get("/categories")
async def list_admin_categories(request: Request, db: Session = Depends(get_db)):
    return ok_response({"items": query_all_categories(db)}, request)


@router.post("/categories", status_code=201)
async def create_admin_category(
    body: CategoryCreateDTO,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(UserRole.ADMIN)),
):
    data = create_category(
        db,
        admin=admin,
        name=body.name,
        slug=body.slug,
        sort_order=body.sort_order,
        is_visible=body.is_visible,
    )
    return ok_response(data, request, status_code=201)


@router.patch("/categories/{cat_id}")
async def patch_admin_category(
    cat_id: str,
    body: CategoryPatchDTO,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(UserRole.ADMIN)),
):
    data = update_category(
        db,
        admin=admin,
        cat_id=cat_id,
        name=body.name,
        slug=body.slug,
        sort_order=body.sort_order,
        is_visible=body.is_visible,
    )
    return ok_response(data, request)


@router.delete("/categories/{cat_id}")
async def delete_admin_category(
    cat_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(UserRole.ADMIN)),
):
    return ok_response(delete_category(db, admin=admin, cat_id=cat_id), request)


@router.get("/courses")
async def list_all_courses_for_governance(request: Request, db: Session = Depends(get_db)):
    return ok_response({"items": query_all_courses_for_governance(db)}, request)


@router.patch("/courses/{course_id}/governance")
async def review_or_govern_course(
    course_id: str,
    body: CourseGovernancePatchDTO,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(UserRole.ADMIN)),
):
    data = apply_course_governance(
        db,
        admin=admin,
        course_id=course_id,
        status=body.status,
        reject_reason=body.reject_reason,
        is_featured=body.is_featured,
        storage_quota_mb=body.storage_quota_mb,
        expires_at=body.expires_at,
    )
    return ok_response(data, request)


@router.get("/courses/{course_id}/resources")
async def inspect_course_resources(course_id: str, request: Request, db: Session = Depends(get_db)):
    return ok_response(query_course_resources_for_admin(db, course_id=course_id), request)


@router.post("/courses/cleanup-expired")
async def admin_cleanup_expired_courses(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(UserRole.ADMIN)),
):
    return ok_response(cleanup_expired_courses(db, admin=admin), request)


@router.post("/documents/{document_id}/takedown")
async def admin_takedown_document(
    document_id: str,
    request: Request,
    body: TakedownDTO | None = None,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(UserRole.ADMIN)),
):
    reason = body.reason if body else "敏感或版权合规处置下架"
    doc = await takedown_course_document(
        db,
        admin=admin,
        document_id=document_id,
        reason=reason,
        request_id=get_request_id(request),
    )
    return ok_response({"id": doc.id, "index_status": doc.index_status}, request)


@router.post("/videos/{video_id}/takedown")
async def admin_takedown_video(
    video_id: str,
    request: Request,
    body: TakedownDTO | None = None,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(UserRole.ADMIN)),
):
    reason = body.reason if body else "敏感或版权合规处置下架"
    data = await takedown_course_video(
        db,
        admin=admin,
        video_id=video_id,
        reason=reason,
        request_id=get_request_id(request),
    )
    return ok_response(data, request)


# ============================================================
# 域 3：AI 生产任务监控与全课程索引状态
# ============================================================
@router.get("/ai-jobs")
async def list_ai_jobs(
    request: Request,
    status: str | None = Query(default=None),
    course_id: str | None = Query(default=None),
    start_time: str | None = Query(default=None),
    end_time: str | None = Query(default=None),
):
    data = await list_admin_ai_jobs(
        status=status,
        course_id=course_id,
        start_time=start_time,
        end_time=end_time,
        request_id=get_request_id(request),
    )
    return ok_response(data, request)


@router.post("/ai-jobs/{job_id}/retries", status_code=202)
async def retry_ai_job(
    job_id: str,
    request: Request,
    body: AdminRetryDTO | None = None,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(UserRole.ADMIN)),
):
    job = await ai_client.ops_retry_job(
        job_id,
        run_inline=False if body is None else body.run_inline,
        request_id=get_request_id(request),
    )
    record_audit(db, actor=admin, action="RETRY_AI_JOB", target_type="ai_job", target_id=job_id, detail=f"重试后状态: {job.get('status')}")
    db.commit()
    return ok_response(job, request, status_code=202, headers={"Location": f"/api/v1/admin/ai-jobs"})


@router.get("/ai-indexes")
async def list_ai_indexes(request: Request, db: Session = Depends(get_db)):
    return ok_response(await list_merged_ai_indexes(db, request_id=get_request_id(request)), request)


@router.post("/ai-indexes/{course_id}/rebuilds", status_code=202)
async def rebuild_ai_index(
    course_id: str,
    request: Request,
    body: AdminRetryDTO | None = None,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(UserRole.ADMIN)),
):
    job = await ai_client.ops_rebuild_index(
        course_id,
        run_inline=False if body is None else body.run_inline,
        request_id=get_request_id(request),
    )
    record_audit(db, actor=admin, action="REBUILD_COURSE_INDEX", target_type="course", target_id=course_id, detail=f"触发重建任务 {job.get('job_id')}")
    db.commit()
    return ok_response(job, request, status_code=202, headers={"Location": f"/api/v1/admin/ai-indexes"})


# ============================================================
# 域 4：AI 质量随机抽检、问题样本队列处置与运行时配置热更新
# ============================================================
@router.get("/feedback-samples")
async def list_quality_samples(request: Request, db: Session = Depends(get_db)):
    return ok_response({"items": query_quality_samples(db)}, request)


@router.post("/spot-checks/draw")
async def draw_spot_checks(
    body: SpotCheckDrawDTO,
    request: Request,
    db: Session = Depends(get_db),
):
    data = draw_spot_check_candidates(
        db,
        sample_type=body.sample_type,
        sample_ratio_pct=body.sample_ratio_pct,
        max_count=body.max_count,
        course_id=body.course_id,
    )
    return ok_response(data, request)


@router.post("/feedback-samples", status_code=201)
async def create_spot_check_sample(
    body: SpotCheckCreateDTO,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(UserRole.ADMIN)),
):
    data = await create_spot_check(
        db,
        admin=admin,
        course_id=body.course_id,
        sample_type=body.sample_type,
        input_text=body.input_text,
        model_output=body.model_output,
        admin_note=body.admin_note,
        message_id=body.message_id,
        request_id=get_request_id(request),
    )
    return ok_response(data, request, status_code=201)


@router.patch("/feedback-samples/{sample_id}")
async def resolve_quality_sample(
    sample_id: str,
    body: SampleResolutionDTO,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(UserRole.ADMIN)),
):
    data = await resolve_sample_item(
        db,
        admin=admin,
        sample_id=sample_id,
        status=body.status,
        admin_note=body.admin_note,
        corrected_output=body.corrected_output,
        request_id=get_request_id(request),
    )
    return ok_response(data, request)


@router.get("/ai-runtime-config")
async def get_ai_runtime_config(request: Request):
    cfg = await ai_client.ops_get_runtime_config(request_id=get_request_id(request))
    if isinstance(cfg.get("rate_limit_per_minute"), dict):
        update_role_ai_rate_limits(cfg["rate_limit_per_minute"])
    else:
        cfg["rate_limit_per_minute"] = get_role_ai_rate_limits()
    return ok_response(cfg, request)


@router.patch("/ai-runtime-config")
async def patch_ai_runtime_config(
    body: AdminRuntimePatchDTO,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(UserRole.ADMIN)),
):
    patch_dict = body.model_dump(exclude_none=True)
    if body.rate_limit_per_minute:
        update_role_ai_rate_limits(body.rate_limit_per_minute)
    updated = await ai_client.ops_patch_runtime_config(
        patch_dict,
        request_id=get_request_id(request),
    )
    updated.setdefault("rate_limit_per_minute", get_role_ai_rate_limits())
    record_audit(
        db,
        actor=admin,
        action="PATCH_AI_RUNTIME_CONFIG",
        target_type="ai_service",
        target_id="global",
        detail=f"更新敏感词过滤/配额限流/模型策略 ({', '.join(patch_dict.keys())})",
    )
    db.commit()
    return ok_response(updated, request)


# ============================================================
# 域 6：系统公告、工单处理、多维审计日志与系统状态监控
# ============================================================
@router.get("/announcements")
async def list_admin_announcements(request: Request, db: Session = Depends(get_db)):
    return ok_response({"items": query_all_announcements(db)}, request)


@router.post("/announcements", status_code=201)
async def publish_announcement(
    body: AnnouncementCreateDTO,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(UserRole.ADMIN)),
):
    data = create_system_announcement(
        db,
        admin=admin,
        title=body.title,
        content=body.content,
        target_role=body.target_role,
    )
    return ok_response(data, request, status_code=201)


@router.delete("/announcements/{ann_id}")
async def delete_admin_announcement(
    ann_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(UserRole.ADMIN)),
):
    return ok_response(delete_system_announcement(db, admin=admin, ann_id=ann_id), request)


@router.get("/tickets")
async def list_support_tickets(request: Request, db: Session = Depends(get_db)):
    return ok_response({"items": query_support_tickets(db)}, request)


@router.patch("/tickets/{ticket_id}")
async def update_support_ticket(
    ticket_id: str,
    body: TicketPatchDTO,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles(UserRole.ADMIN)),
):
    data = update_ticket_status(
        db,
        admin=admin,
        ticket_id=ticket_id,
        status=body.status,
        assignee_id=body.assignee_id,
        assignee_name=body.assignee_name,
        assignee_note=body.assignee_note,
    )
    return ok_response(data, request)


@router.get("/audit-logs")
async def get_audit_logs(
    request: Request,
    actor_role: str | None = Query(default=None),
    action: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    items = list_recent_audit_logs(
        db,
        actor_role=actor_role,
        action=action,
        keyword=keyword,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )
    return ok_response({"items": items}, request)


@router.get("/system-status")
async def get_admin_system_status(request: Request, db: Session = Depends(get_db)):
    data = await build_system_status_panorama(db, request_id=get_request_id(request))
    return ok_response(data, request)
