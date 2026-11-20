"""
多源级联视频语音转写适配器 (Cascading ASR Provider: Cloud API + Local Faster-Whisper)。
优先级策略（默认 `ASR_PROVIDER=auto`）：
1. 若配置了 OpenAI 兼容 API Key（且启用了 openai），调用 `/audio/transcriptions` (`verbose_json` 秒级时间戳)；
2. 若配置了 Gemini API Key（且启用了 gemini），提取轻量单声道音频调用 Gemini 多模态接口输出时间戳字幕；
3. 若未配置外部 API（或云端调用失败），自动启用本地离线 `faster-whisper` 模型（内置 PyAV 解码器，无需系统安装 ffmpeg.exe，自带 VAD 静音过滤）；
4. 若视频无音轨或解码异常，优雅降级返回空列表交由上游章节合成器兜底，绝不崩溃。
"""
import asyncio
import base64
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
from typing import Any
import uuid
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class AsrResult:
    segments: list[dict[str, Any]]
    provider_used: str
    duration_sec: int | None = None
    degraded: bool = False


class CascadingAsrClient:
    def __init__(self) -> None:
        self._whisper_model: Any = None
        self._media_transport: httpx.AsyncBaseTransport | None = None

    def set_media_transport(self, transport: httpx.AsyncBaseTransport | None) -> None:
        """供进程内集成测试注入媒体下载 Transport。"""
        self._media_transport = transport

    async def transcribe_from_url(self, media_url: str) -> AsrResult:
        if not media_url:
            return AsrResult(segments=[], provider_used="none", degraded=False)

        tmp_dir = Path(settings.data_dir) / "tmp_media"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(media_url.split("?")[0]).suffix or ".mp4"
        local_media = tmp_dir / f"asr_in_{uuid.uuid4().hex[:10]}{suffix}"
        extracted_wav = tmp_dir / f"asr_16k_{uuid.uuid4().hex[:10]}.wav"

        try:
            await self._download_media(media_url, local_media)
        except Exception as exc:
            raise RuntimeError(f"无法从对象存储拉取视频媒体流 ({media_url}): {exc}") from exc

        try:
            has_audio, probed_duration = await asyncio.to_thread(
                self._extract_16k_mono_wav, local_media, extracted_wav
            )
            if not has_audio:
                logger.info("Media %s has no audio track; skipping ASR.", media_url)
                return AsrResult(
                    segments=[],
                    provider_used="silent_video",
                    duration_sec=probed_duration,
                    degraded=False,
                )

            mode = settings.asr_provider.lower().strip()

            # 方案 B-1：OpenAI 兼容云端 Whisper API
            use_openai = (
                mode == "openai"
                or (mode == "auto" and settings.ai_provider.lower() == "openai")
            ) and bool(settings.openai_api_key)
            if use_openai:
                try:
                    segs = await self._transcribe_via_openai(extracted_wav)
                    if segs:
                        return AsrResult(segments=segs, provider_used="openai_whisper_api", duration_sec=probed_duration)
                except Exception as exc:
                    logger.warning("OpenAI ASR failed, falling back to local faster-whisper: %s", exc)

            # 方案 B-2：Google Gemini 多模态音频时间戳转写
            use_gemini = (
                mode == "gemini"
                or (mode == "auto" and settings.ai_provider.lower() == "gemini")
            ) and bool(settings.gemini_api_key)
            if use_gemini:
                try:
                    segs = await self._transcribe_via_gemini(extracted_wav, probed_duration or 300)
                    if segs:
                        return AsrResult(segments=segs, provider_used="gemini_multimodal_asr", duration_sec=probed_duration)
                except Exception as exc:
                    logger.warning("Gemini ASR failed, falling back to local faster-whisper: %s", exc)

            # 方案 A：本地离线 Faster-Whisper 语音识别（默认无 API Key 时自动启用）
            segs = await asyncio.to_thread(self._transcribe_via_local_whisper, extracted_wav)
            return AsrResult(
                segments=segs,
                provider_used=f"local_faster_whisper({settings.whisper_model_size})",
                duration_sec=probed_duration,
                degraded=False,
            )
        except Exception as exc:
            logger.warning("ASR pipeline degraded for %s: %s", media_url, exc)
            return AsrResult(segments=[], provider_used="asr_fallback", degraded=True)
        finally:
            for f in (local_media, extracted_wav):
                try:
                    if f.exists():
                        f.unlink()
                except OSError:
                    pass

    async def _download_media(self, media_url: str, dest: Path) -> None:
        async with httpx.AsyncClient(timeout=120.0, transport=self._media_transport, follow_redirects=True) as client:
            async with client.stream("GET", media_url) as resp:
                resp.raise_for_status()
                with dest.open("wb") as out:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        out.write(chunk)

    @staticmethod
    def _extract_16k_mono_wav(input_path: Path, output_wav: Path) -> tuple[bool, int | None]:
        """
        使用 PyAV（随 faster-whisper 内置编译好的 FFmpeg 库，无需系统安装 ffmpeg.exe）
        探测视频真实时长并将音轨重采样为 16kHz 单声道 WAV，兼顾云端上传体积与本地 Whisper 推理速度。
        """
        import av

        with av.open(str(input_path)) as in_container:
            duration_sec = int(in_container.duration / av.time_base) if in_container.duration else None
            audio_streams = [s for s in in_container.streams if s.type == "audio"]
            if not audio_streams:
                return False, duration_sec

            in_stream = audio_streams[0]
            with av.open(str(output_wav), mode="w", format="wav") as out_container:
                out_stream = out_container.add_stream("pcm_s16le", rate=16000, layout="mono")
                resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
                for frame in in_container.decode(in_stream):
                    frame.pts = None
                    for out_frame in resampler.resample(frame):
                        for packet in out_stream.encode(out_frame):
                            out_container.mux(packet)
                for out_frame in resampler.resample(None):
                    for packet in out_stream.encode(out_frame):
                        out_container.mux(packet)
                for packet in out_stream.encode(None):
                    out_container.mux(packet)

            return True, duration_sec

    async def _transcribe_via_openai(self, wav_path: Path) -> list[dict[str, Any]]:
        url = f"{settings.openai_base_url.rstrip('/')}/audio/transcriptions"
        async with httpx.AsyncClient(timeout=180.0) as client:
            with wav_path.open("rb") as audio_file:
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                    data={"model": settings.openai_asr_model, "response_format": "verbose_json"},
                    files={"file": ("audio.wav", audio_file, "audio/wav")},
                )
                resp.raise_for_status()
                payload = resp.json()

        segments = []
        for seg in payload.get("segments") or []:
            text = str(seg.get("text", "")).strip()
            if text:
                segments.append({
                    "start_sec": max(0, int(float(seg.get("start", 0)))),
                    "end_sec": max(1, int(float(seg.get("end", 1)))),
                    "text": text,
                })
        if not segments and payload.get("text"):
            segments.append({"start_sec": 0, "end_sec": 60, "text": str(payload["text"]).strip()})
        return segments

    async def _transcribe_via_gemini(self, wav_path: Path, duration_sec: int) -> list[dict[str, Any]]:
        raw_bytes = wav_path.read_bytes()
        # Gemini inlineData 适用于中短音频（约 20MB 内）
        if len(raw_bytes) > 18 * 1024 * 1024:
            raise ValueError("Audio exceeds inline multimodal size limit; delegating to local faster-whisper")
        b64_audio = base64.b64encode(raw_bytes).decode("ascii")
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{settings.gemini_model}:generateContent?key={settings.gemini_api_key}"
        )
        prompt = (
            "请准确转写这段课程音频，并严格只输出 JSON 数组，格式为："
            '[{"start_sec": 0, "end_sec": 25, "text": "转写句子"}]，不要输出 Markdown 代码块以外的多余说明。'
        )
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                url,
                json={
                    "contents": [{
                        "role": "user",
                        "parts": [
                            {"text": prompt},
                            {"inlineData": {"mimeType": "audio/wav", "data": b64_audio}},
                        ],
                    }]
                },
            )
            resp.raise_for_status()
            text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

        cleaned = text.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return [
                {
                    "start_sec": int(s.get("start_sec", 0)),
                    "end_sec": int(s.get("end_sec", duration_sec)),
                    "text": str(s.get("text", "")).strip(),
                }
                for s in parsed
                if str(s.get("text", "")).strip()
            ]
        return []

    def _transcribe_via_local_whisper(self, wav_path: Path) -> list[dict[str, Any]]:
        if settings.hf_endpoint:
            os.environ.setdefault("HF_ENDPOINT", settings.hf_endpoint)
        from faster_whisper import WhisperModel

        if self._whisper_model is None:
            model_cache = Path(settings.data_dir) / "whisper_models"
            model_cache.mkdir(parents=True, exist_ok=True)
            logger.info(
                "Loading local Faster-Whisper model '%s' on %s (%s)...",
                settings.whisper_model_size,
                settings.whisper_device,
                settings.whisper_compute_type,
            )
            self._whisper_model = WhisperModel(
                settings.whisper_model_size,
                device=settings.whisper_device,
                compute_type=settings.whisper_compute_type,
                download_root=str(model_cache),
            )

        raw_segments, _info = self._whisper_model.transcribe(
            str(wav_path),
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        segments: list[dict[str, Any]] = []
        for seg in raw_segments:
            txt = seg.text.strip()
            if txt:
                segments.append({
                    "start_sec": max(0, int(seg.start)),
                    "end_sec": max(int(seg.start) + 1, int(round(seg.end))),
                    "text": txt,
                })
        return segments


asr_client = CascadingAsrClient()
