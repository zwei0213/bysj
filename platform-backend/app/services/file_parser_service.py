"""
多格式教学资料与字幕解析服务（接入开源成熟轮子）：
- `.pdf`：优先使用 `pymupdf4llm` 保留标题层级与表格结构转为 Markdown（异常时自动回退 `pypdf`）；
- `.pptx`：使用 `python-pptx` 提取幻灯片正文、表格与演讲者备注；
- `.docx`：使用 `python-docx` 提取段落与表格；
- `.md` / `.txt` / `.csv`、`.srt` / `.vtt` 外挂字幕与视频时长探测。
"""
import io
from pathlib import Path
import re
import struct
from typing import Any
from app.core.errors import PlatformError


def decode_text_bytes(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _extract_pdf_markdown(raw_bytes: bytes) -> str:
    # 优先使用 PyMuPDF + pymupdf4llm 提取版面感知 Markdown（保留标题、列表与表格）
    try:
        import pymupdf
        import pymupdf4llm

        with pymupdf.open(stream=raw_bytes, filetype="pdf") as doc:
            md_text = (pymupdf4llm.to_markdown(doc) or "").strip()
            if md_text:
                return md_text
    except Exception:
        pass

    # 回退 pypdf 逐页提取
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw_bytes))
    pages = [
        f"【第 {idx + 1} 页】\n{(page.extract_text() or '').strip()}"
        for idx, page in enumerate(reader.pages)
        if (page.extract_text() or "").strip()
    ]
    return "\n\n".join(pages).strip()


def _extract_pptx_slides(raw_bytes: bytes) -> str:
    from pptx import Presentation

    prs = Presentation(io.BytesIO(raw_bytes))
    slides_out: list[str] = []
    for idx, slide in enumerate(prs.slides, start=1):
        parts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                txt = shape.text_frame.text.strip()
                if txt:
                    parts.append(txt)
            if shape.has_table:
                for row in shape.table.rows:
                    cells = [c.text.strip().replace("\n", " ") for c in row.cells if c.text.strip()]
                    if cells:
                        parts.append(" | ".join(cells))
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                parts.append(f"[备注] {notes}")
        if parts:
            slides_out.append(f"【课件第 {idx} 页】\n" + "\n".join(parts))
    return "\n\n".join(slides_out).strip()


def parse_document_bytes(filename: str, raw_bytes: bytes) -> tuple[str, str]:
    ext = Path(filename).suffix.lower()
    if not raw_bytes:
        raise PlatformError("EMPTY_FILE", "上传的文件内容为空", status_code=400)

    if ext == ".pdf":
        combined = _extract_pdf_markdown(raw_bytes)
        if not combined:
            raise PlatformError(
                "SCANNED_OR_EMPTY_PDF",
                "未能从该 PDF 提取到文字层（可能是纯图片扫描版 PDF），请上传带文字层的 PDF 或 Word/Markdown 讲义",
                status_code=400,
            )
        return "pdf", combined

    if ext == ".docx":
        import docx

        document = docx.Document(io.BytesIO(raw_bytes))
        blocks = [p.text.strip() for p in document.paragraphs if p.text.strip()]
        for table in document.tables:
            for row in table.rows:
                cells = [c.text.strip().replace("\n", " ") for c in row.cells if c.text.strip()]
                if cells:
                    blocks.append(" | ".join(cells))
        combined = "\n\n".join(blocks).strip()
        if not combined:
            raise PlatformError("EMPTY_DOCX", "Word 文档中未提取到有效文字内容", status_code=400)
        return "docx", combined

    if ext == ".pptx":
        slides_text = _extract_pptx_slides(raw_bytes)
        if not slides_text:
            raise PlatformError("EMPTY_PPTX", "PPTX 课件中未提取到有效文字内容", status_code=400)
        return "pptx", slides_text

    if ext in (".md", ".markdown", ".txt", ".csv"):
        text = decode_text_bytes(raw_bytes).strip()
        if not text:
            raise PlatformError("EMPTY_TEXT_FILE", "文本文件内容为空", status_code=400)
        fmt = "markdown" if ext in (".md", ".markdown") else ext.lstrip(".")
        return fmt, text

    raise PlatformError(
        "UNSUPPORTED_DOCUMENT_FORMAT",
        f"暂不支持的文件后缀 '{ext}'，请上传 .pdf, .docx, .pptx, .md 或 .txt 文件",
        status_code=400,
    )


_SRT_TIME_RE = re.compile(
    r"(?:(\d{1,2}):)?(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(?:(\d{1,2}):)?(\d{2}):(\d{2})[,.](\d{1,3})"
)


def parse_subtitle_bytes(filename: str, raw_bytes: bytes) -> list[dict[str, Any]]:
    ext = Path(filename).suffix.lower()
    if ext not in (".srt", ".vtt"):
        raise PlatformError("UNSUPPORTED_SUBTITLE_FORMAT", "字幕文件请使用 .srt 或 .vtt 格式", status_code=400)

    content = decode_text_bytes(raw_bytes)
    segments: list[dict[str, Any]] = []
    for block in re.split(r"\r?\n\r?\n", content.strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip() and ln.strip() != "WEBVTT"]
        ts_idx = next((i for i, ln in enumerate(lines) if "-->" in ln), -1)
        if ts_idx == -1:
            continue
        m = _SRT_TIME_RE.search(lines[ts_idx])
        if not m:
            continue
        h1, m1, s1, _ms1, h2, m2, s2, _ms2 = m.groups()
        start_sec = int(h1 or 0) * 3600 + int(m1) * 60 + int(s1)
        end_sec = max(start_sec + 1, int(h2 or 0) * 3600 + int(m2) * 60 + int(s2))
        text = re.sub(r"<[^>]+>", "", " ".join(lines[ts_idx + 1 :])).strip()
        if text:
            segments.append({"start_sec": start_sec, "end_sec": end_sec, "text": text})
    return segments


def probe_video_duration_sec(file_path: Path, default_sec: int = 300) -> int:
    """优先使用 PyAV 读取精确媒体时长，回退 MP4 mvhd 头解析。"""
    try:
        import av

        with av.open(str(file_path)) as container:
            if container.duration:
                return max(1, int(container.duration / av.time_base))
    except Exception:
        pass

    try:
        return _probe_mp4_mvhd_duration(file_path) or default_sec
    except Exception:
        return default_sec


def _probe_mp4_mvhd_duration(file_path: Path) -> int | None:
    data = file_path.read_bytes()[: 2 * 1024 * 1024]
    idx = data.find(b"mvhd")
    if idx == -1 or idx + 24 > len(data):
        return None
    version = data[idx + 4]
    if version == 0:
        timescale, duration = struct.unpack(">II", data[idx + 16 : idx + 24])
    elif version == 1 and idx + 32 <= len(data):
        timescale, duration = struct.unpack(">IQ", data[idx + 24 : idx + 32])
    else:
        return None
    return max(1, int(duration // max(1, timescale)))
