"""
对象存储服务（支持严格单文件大小配额保护、封面上传、分片缓存目录与流式落盘）。
"""
from pathlib import Path
import re
import shutil
import uuid
from fastapi import UploadFile
from app.core.config import settings
from app.core.errors import PlatformError
from app.core.security import sign_media_path

ALLOWED_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".m4v", ".mp3", ".wav", ".avi"}
ALLOWED_COVER_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}


def ensure_storage_dirs() -> Path:
    root = Path(settings.storage_dir)
    for sub in ("videos", "documents", "covers", "subtitles", "chunks"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def _safe_stem(filename: str) -> str:
    stem = Path(filename).stem
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", stem).strip("_")
    return cleaned[:40] or "resource"


def unlink_media_rel_url(rel_url: str | None) -> None:
    if not rel_url or not rel_url.startswith("/media/"):
        return
    clean_path = rel_url.split("?", 1)[0]
    parts = clean_path.lstrip("/").split("/", 2)
    if len(parts) != 3:
        return
    _, category, filename = parts
    root = ensure_storage_dirs().resolve()
    target = (root / category / filename).resolve()
    if target.is_relative_to(root) and target.is_file():
        target.unlink(missing_ok=True)


def build_signed_media_url(category: str, filename: str) -> str:
    sig = sign_media_path(category, filename)
    return f"/media/{category}/{filename}?sig={sig}"


async def save_video_upload(upload: UploadFile) -> tuple[Path, str, str, float]:
    orig_name = upload.filename or "lecture.mp4"
    ext = Path(orig_name).suffix.lower()
    if ext not in ALLOWED_VIDEO_EXTS:
        raise PlatformError(
            "UNSUPPORTED_VIDEO_FORMAT",
            f"不支持的视频/音频格式 '{ext}'，请上传 {', '.join(sorted(ALLOWED_VIDEO_EXTS))}",
            status_code=400,
        )

    max_bytes = settings.max_video_upload_mb * 1024 * 1024
    root = ensure_storage_dirs()
    stored_name = f"vid_{uuid.uuid4().hex[:10]}_{_safe_stem(orig_name)}{ext}"
    dest = root / "videos" / stored_name

    total_bytes = 0
    with dest.open("wb") as f:
        while True:
            chunk = await upload.read(1024 * 256)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > max_bytes:
                f.close()
                dest.unlink(missing_ok=True)
                raise PlatformError(
                    "VIDEO_TOO_LARGE",
                    f"视频文件超出最大允许体积 ({settings.max_video_upload_mb} MB)",
                    status_code=413,
                )
            f.write(chunk)

    if total_bytes == 0:
        dest.unlink(missing_ok=True)
        raise PlatformError("EMPTY_VIDEO_FILE", "上传的视频文件为空", status_code=400)

    rel_url = build_signed_media_url("videos", stored_name)
    ai_fetch_url = f"{settings.public_backend_origin.rstrip('/')}{rel_url}"
    size_mb = round(max(0.1, total_bytes / (1024 * 1024)), 2)
    return dest, rel_url, ai_fetch_url, size_mb


async def save_document_upload(upload: UploadFile) -> tuple[bytes, str, float]:
    orig_name = upload.filename or "document.pdf"
    ext = Path(orig_name).suffix.lower()
    max_bytes = settings.max_document_upload_mb * 1024 * 1024

    chunks: list[bytes] = []
    total_bytes = 0
    while True:
        piece = await upload.read(1024 * 256)
        if not piece:
            break
        total_bytes += len(piece)
        if total_bytes > max_bytes:
            raise PlatformError(
                "DOCUMENT_TOO_LARGE",
                f"文档文件超出最大允许体积 ({settings.max_document_upload_mb} MB)",
                status_code=413,
            )
        chunks.append(piece)

    raw = b"".join(chunks)
    if not raw:
        raise PlatformError("EMPTY_DOCUMENT_FILE", "上传的资料文件为空", status_code=400)

    root = ensure_storage_dirs()
    stored_name = f"doc_{uuid.uuid4().hex[:10]}_{_safe_stem(orig_name)}{ext}"
    dest = root / "documents" / stored_name
    dest.write_bytes(raw)

    rel_url = build_signed_media_url("documents", stored_name)
    size_mb = round(max(0.05, len(raw) / (1024 * 1024)), 2)
    return raw, rel_url, size_mb


async def save_cover_upload(upload: UploadFile) -> tuple[str, float]:
    orig_name = upload.filename or "cover.png"
    ext = Path(orig_name).suffix.lower() or ".png"
    if ext not in ALLOWED_COVER_EXTS:
        raise PlatformError(
            "UNSUPPORTED_COVER_FORMAT",
            f"不支持的封面图片格式 '{ext}'，请上传 {', '.join(sorted(ALLOWED_COVER_EXTS))}",
            status_code=400,
        )

    max_bytes = 10 * 1024 * 1024
    chunks: list[bytes] = []
    total_bytes = 0
    while True:
        piece = await upload.read(1024 * 128)
        if not piece:
            break
        total_bytes += len(piece)
        if total_bytes > max_bytes:
            raise PlatformError("COVER_TOO_LARGE", "课程封面图片不能超过 10 MB", status_code=413)
        chunks.append(piece)

    raw = b"".join(chunks)
    if not raw:
        raise PlatformError("EMPTY_COVER_FILE", "上传的封面文件为空", status_code=400)

    root = ensure_storage_dirs()
    stored_name = f"cov_{uuid.uuid4().hex[:10]}_{_safe_stem(orig_name)}{ext}"
    dest = root / "covers" / stored_name
    dest.write_bytes(raw)

    rel_url = f"/media/covers/{stored_name}"
    size_mb = round(max(0.02, len(raw) / (1024 * 1024)), 2)
    return rel_url, size_mb


def prepare_chunk_temp_dir(upload_id: str) -> Path:
    root = ensure_storage_dirs()
    temp_dir = root / "chunks" / upload_id
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


def merge_chunks_to_video(filename: str, temp_dir: Path, total_chunks: int) -> tuple[Path, str, str, float]:
    ext = Path(filename or "lecture.mp4").suffix.lower() or ".mp4"
    if ext not in ALLOWED_VIDEO_EXTS:
        raise PlatformError(
            "UNSUPPORTED_VIDEO_FORMAT",
            f"不支持的视频/音频格式 '{ext}'",
            status_code=400,
        )

    root = ensure_storage_dirs()
    stored_name = f"vid_{uuid.uuid4().hex[:10]}_{_safe_stem(filename)}{ext}"
    dest = root / "videos" / stored_name

    max_bytes = settings.max_video_upload_mb * 1024 * 1024
    total_bytes = 0
    with dest.open("wb") as out_f:
        for idx in range(total_chunks):
            part_path = temp_dir / f"chunk_{idx:06d}.part"
            if not part_path.is_file():
                out_f.close()
                dest.unlink(missing_ok=True)
                raise PlatformError("CHUNKS_INCOMPLETE", f"缺少第 {idx} 号视频分片，无法合并", status_code=400)
            with part_path.open("rb") as in_f:
                while True:
                    buf = in_f.read(1024 * 256)
                    if not buf:
                        break
                    total_bytes += len(buf)
                    if total_bytes > max_bytes:
                        out_f.close()
                        dest.unlink(missing_ok=True)
                        raise PlatformError("VIDEO_TOO_LARGE", "合并后视频超出最大允许体积", status_code=413)
                    out_f.write(buf)

    if total_bytes == 0:
        dest.unlink(missing_ok=True)
        raise PlatformError("EMPTY_VIDEO_FILE", "合并后的视频文件为空", status_code=400)

    shutil.rmtree(temp_dir, ignore_errors=True)
    rel_url = build_signed_media_url("videos", stored_name)
    ai_fetch_url = f"{settings.public_backend_origin.rstrip('/')}{rel_url}"
    size_mb = round(max(0.1, total_bytes / (1024 * 1024)), 2)
    return dest, rel_url, ai_fetch_url, size_mb
