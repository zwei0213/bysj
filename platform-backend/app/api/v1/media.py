"""
支持 HTTP 206 Partial Content 字节范围拖拽流式播放的媒体分发路由 (/media/{category}/{filename})。
安全策略：
- `covers` 课程封面公开可见；
- `videos` / `documents` / `subtitles` 必须携带有效 HMAC 能力签名 (`?sig=`)、Bearer JWT 或内部 `X-Service-Key`，防未授权枚举下载；
- 包含严格的路径穿越防御，适配原生 <video> 任意时间点跳转。
"""
from collections.abc import Iterator
import mimetypes
from pathlib import Path
import secrets
from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from app.core.config import settings
from app.core.errors import PlatformError
from app.core.security import decode_jwt_token, verify_media_signature
from app.services.storage_service import ensure_storage_dirs

router = APIRouter(tags=["MediaStreaming"])


def _iter_file_range(file_path: Path, start: int, length: int, chunk_size: int = 1024 * 256) -> Iterator[bytes]:
    with file_path.open("rb") as f:
        f.seek(start)
        remaining = length
        while remaining > 0:
            data = f.read(min(chunk_size, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data


def _authorize_media_access(
    category: str,
    filename: str,
    *,
    sig: str | None,
    authorization: str | None,
    service_key: str | None,
) -> None:
    if category == "covers":
        return
    if verify_media_signature(category, filename, sig):
        return
    if service_key and secrets.compare_digest(service_key, settings.ai_service_key):
        return
    if authorization and authorization.lower().startswith("bearer "):
        decode_jwt_token(authorization.split(" ", 1)[1].strip(), expected_use="access")
        return
    raise PlatformError("UNAUTHORIZED_MEDIA_ACCESS", "缺少有效的媒体访问凭证或签名", status_code=401)


@router.get("/media/{category}/{filename}")
async def stream_media_file(
    category: str,
    filename: str,
    request: Request,
    sig: str | None = Query(default=None),
    range_header: str | None = Header(default=None, alias="Range"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    service_key: str | None = Header(default=None, alias="X-Service-Key"),
):
    if category not in ("videos", "documents", "covers", "subtitles"):
        raise PlatformError("MEDIA_NOT_FOUND", "不支持的媒体分类目录", status_code=404)

    _authorize_media_access(
        category,
        filename,
        sig=sig,
        authorization=authorization,
        service_key=service_key,
    )

    storage_root = ensure_storage_dirs().resolve()
    target = (storage_root / category / filename).resolve()
    if not target.is_relative_to(storage_root) or not target.is_file():
        raise PlatformError("MEDIA_NOT_FOUND", "媒体资源不存在", status_code=404)

    file_size = target.stat().st_size
    content_type, _ = mimetypes.guess_type(str(target))
    media_type = content_type or "application/octet-stream"

    if not range_header or not range_header.startswith("bytes="):
        return FileResponse(
            path=str(target),
            media_type=media_type,
            headers={"Accept-Ranges": "bytes"},
        )

    try:
        range_spec = range_header.replace("bytes=", "", 1).split(",")[0].strip()
        start_str, end_str = range_spec.split("-", 1)
        start = int(start_str) if start_str else 0
        end = int(end_str) if end_str else file_size - 1
    except ValueError as exc:
        raise PlatformError("INVALID_BYTE_RANGE", "非法的 Range 请求头", status_code=416) from exc

    start = max(0, start)
    end = min(file_size - 1, end)
    if start >= file_size or start > end:
        raise PlatformError("RANGE_NOT_SATISFIABLE", "请求字节区间越界", status_code=416)

    chunk_len = end - start + 1
    return StreamingResponse(
        _iter_file_range(target, start, chunk_len),
        status_code=206,
        media_type=media_type,
        headers={
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(chunk_len),
            "Cache-Control": "private, max-age=3600",
        },
    )
