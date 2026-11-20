from datetime import datetime, timezone
from typing import Any
import uuid
from fastapi import Request
from fastapi.responses import JSONResponse


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_request_id(request: Request) -> str:
    req_id = getattr(request.state, "request_id", None)
    if req_id:
        return req_id
    header_id = request.headers.get("X-Request-ID")
    return header_id or f"req_{uuid.uuid4().hex[:12]}"


def ok_response(
    data: Any,
    request: Request,
    *,
    status_code: int = 200,
    pagination: dict[str, int] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """构造符合工程契约规范的统一成功信封响应。"""
    req_id = get_request_id(request)
    meta: dict[str, Any] = {
        "request_id": req_id,
        "timestamp": utc_now_iso(),
    }
    if pagination is not None:
        meta["pagination"] = pagination

    resp_headers = {"X-Request-ID": req_id}
    if headers:
        resp_headers.update(headers)

    return JSONResponse(
        status_code=status_code,
        content={"data": data, "meta": meta},
        headers=resp_headers,
    )


def paginate_items(items: list[Any], page: int = 1, page_size: int = 20) -> tuple[list[Any], dict[str, int]]:
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    total = len(items)
    total_pages = max(1, (total + page_size - 1) // page_size)
    start = (page - 1) * page_size
    end = start + page_size
    return items[start:end], {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
    }
