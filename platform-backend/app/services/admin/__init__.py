"""admin 领域服务子模块统一导出。"""
from app.services.admin.overview_and_reports import (
    build_admin_overview,
    export_platform_report_csv,
    export_platform_report_xlsx,
)
from app.services.admin.org_and_users import (
    query_organizations,
    create_org_node,
    update_org_node,
    delete_org_node,
    query_users,
    create_managed_user,
    batch_import_users_from_csv,
    batch_import_users_from_tabular,
    update_managed_user,
    delete_managed_user,
    get_permissions_matrix,
    update_permissions_matrix,
    batch_assign_students_to_class,
)
from app.services.admin.course_governance import (
    query_all_courses_for_governance,
    apply_course_governance,
    query_all_categories,
    create_category,
    update_category,
    delete_category,
    query_course_resources_for_admin,
    takedown_course_document,
    takedown_course_video,
    cleanup_expired_courses,
)
from app.services.admin.ai_ops_and_quality import (
    list_admin_ai_jobs,
    list_merged_ai_indexes,
    query_quality_samples,
    draw_spot_check_candidates,
    create_spot_check,
    resolve_sample_item,
)
from app.services.admin.system_ops import (
    query_all_announcements,
    create_system_announcement,
    delete_system_announcement,
    query_support_tickets,
    update_ticket_status,
    list_recent_audit_logs,
    list_active_announcements,
    build_system_status_panorama,
)
