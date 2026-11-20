"""公共认证、自助注册、令牌刷新、密码安全、通知中心、课程分类与目录、选课及工单 HTTP 路由层。"""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.errors import ok_response
from app.core.rate_limit import limit_requests
from app.core.security import get_current_user
from app.models.entities import User
from app.schemas.auth import (
    LoginRequestDTO,
    NotificationPatchDTO,
    PasswordChangeDTO,
    RefreshTokenRequestDTO,
    RegisterRequestDTO,
    SupportTicketCreateDTO,
)
from app.services.admin_service import list_active_announcements
from app.services.catalog_service import (
    authenticate_and_issue_token,
    build_course_detail,
    change_user_password,
    enroll_student_in_course,
    list_visible_categories,
    mark_user_notification_read,
    query_my_support_tickets,
    query_published_courses,
    query_user_notifications,
    refresh_auth_tokens,
    register_user_account,
    submit_user_support_ticket,
)

router = APIRouter(prefix="/api/v1")


@router.post("/auth/tokens", tags=["Auth"], dependencies=[Depends(limit_requests("auth_login", max_per_minute=25))])
async def issue_auth_token(body: LoginRequestDTO, request: Request, db: Session = Depends(get_db)):
    data = authenticate_and_issue_token(db, username=body.username, password=body.password)
    return ok_response(data, request)


@router.post(
    "/auth/registrations",
    tags=["Auth"],
    status_code=201,
    dependencies=[Depends(limit_requests("auth_register", max_per_minute=12))],
)
async def register_account(body: RegisterRequestDTO, request: Request, db: Session = Depends(get_db)):
    data = register_user_account(
        db,
        username=body.username,
        display_name=body.display_name,
        email=body.email,
        password=body.password,
        role=body.role,
    )
    return ok_response(data, request, status_code=201)


@router.post("/auth/refresh", tags=["Auth"], dependencies=[Depends(limit_requests("auth_refresh", max_per_minute=30))])
async def refresh_token_endpoint(body: RefreshTokenRequestDTO, request: Request, db: Session = Depends(get_db)):
    data = refresh_auth_tokens(db, refresh_token=body.refresh_token)
    return ok_response(data, request)


@router.get("/users/me", tags=["Auth"])
async def get_me(request: Request, user: User = Depends(get_current_user)):
    return ok_response(
        {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "email": user.email,
            "role": user.role,
            "org_id": user.org_id,
            "streak_days": user.streak_days,
            "last_study_date": user.last_study_date,
            "teacher_approved": user.teacher_approved,
            "teacher_status": user.teacher_status,
            "reject_reason": user.reject_reason,
        },
        request,
    )


@router.put("/users/me/password", tags=["Auth"])
async def update_my_password(
    body: PasswordChangeDTO,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    change_user_password(db, user=user, current_password=body.current_password, new_password=body.new_password)
    return ok_response({"updated": True}, request)


@router.get("/users/me/notifications", tags=["Auth"])
async def list_my_notifications(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return ok_response(query_user_notifications(db, user=user), request)


@router.patch("/users/me/notifications/{notif_id}", tags=["Auth"])
async def patch_my_notification(
    notif_id: str,
    request: Request,
    body: NotificationPatchDTO | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    is_read = True if body is None else body.is_read
    data = mark_user_notification_read(db, user=user, notif_id=notif_id, is_read=is_read)
    return ok_response(data, request)


@router.get("/users/me/support-tickets", tags=["Courses"])
async def list_my_support_tickets(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return ok_response({"items": query_my_support_tickets(db, user=user)}, request)


@router.get("/categories", tags=["Courses"])
async def get_public_categories(
    request: Request,
    db: Session = Depends(get_db),
):
    return ok_response({"items": list_visible_categories(db)}, request)


@router.get("/announcements", tags=["Courses"])
async def get_public_announcements(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    items = [
        {"id": a.id, "title": a.title, "content": a.content, "target_role": a.target_role, "created_at": a.created_at}
        for a in list_active_announcements(db, role=user.role)
    ]
    return ok_response({"items": items}, request)


@router.get("/courses", tags=["Courses"])
async def list_published_courses(
    request: Request,
    category: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    featured_only: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    sliced, pagination = query_published_courses(
        db,
        user=user,
        category=category,
        keyword=keyword,
        featured_only=featured_only,
        page=page,
        page_size=page_size,
    )
    return ok_response({"items": sliced}, request, pagination=pagination)


@router.get("/courses/{course_id}", tags=["Courses"])
async def get_course_detail(
    course_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    data = build_course_detail(db, course_id=course_id, user=user)
    return ok_response(data, request)


@router.post("/courses/{course_id}/enrollments", tags=["Courses"], status_code=201)
async def enroll_in_course(
    course_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    data, status_code = enroll_student_in_course(db, course_id=course_id, user=user)
    return ok_response(data, request, status_code=status_code)


@router.post("/support-tickets", tags=["Courses"], status_code=201)
async def create_support_ticket(
    body: SupportTicketCreateDTO,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    data = submit_user_support_ticket(
        db,
        user=user,
        category=body.category,
        subject=body.subject,
        description=body.description,
    )
    return ok_response(data, request, status_code=201)
