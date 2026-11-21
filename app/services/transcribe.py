import mimetypes
import re
from pathlib import Path

import httpx

from ..config import ASR_API_KEY, ASR_BASE_URL, ASR_MODEL
from .llm import LLMError


def transcribe(path: Path, offset_ms: int = 0, expected_seconds: float | None = None) -> list[dict]:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    data = {
        "model": ASR_MODEL,
        "response_format": "verbose_json",
    }
    try:
        with path.open("rb") as handle:
            files = {"file": (path.name, handle, mime)}
            with httpx.Client(timeout=600) as client:
                resp = client.post(
                    f"{ASR_BASE_URL}/audio/transcriptions",
                    data=data,
                    files=files,
                    headers={"Authorization": f"Bearer {ASR_API_KEY}"},
                )
    except httpx.HTTPError as exc:
        raise LLMError(f"语音转写请求失败：{exc}") from exc
    if resp.status_code >= 400:
        raise LLMError(f"语音转写失败 {resp.status_code}：{resp.text[:300]}")
    payload = resp.json()
    segments = payload.get("segments") or []
    results: list[dict] = []
    for idx, seg in enumerate(segments):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        results.append(
            {
                "idx": idx,
                "start_ms": int(float(seg.get("start", 0)) * 1000) + offset_ms,
                "end_ms": int(float(seg.get("end", 0)) * 1000) + offset_ms,
                "text": text,
            }
        )
    if results:
        return results
    text = (payload.get("text") or "").strip()
    if not text:
        return []
    duration = payload.get("duration") or (payload.get("usage") or {}).get("seconds")
    return pseudo_segments(text, offset_ms, duration or expected_seconds)


def pseudo_segments(text: str, offset_ms: int, duration: float | None = None) -> list[dict]:
    parts = [p.strip() for p in re.split(r"(?<=[。！？.!?])\s*", text) if p.strip()]
    if not parts:
        return []
    total = float(duration or 0) or max(len(text) / 5.5, 10.0)
    weight = sum(len(p) for p in parts) or 1
    cursor = 0.0
    results: list[dict] = []
    for idx, part in enumerate(parts):
        span = total * len(part) / weight
        start = cursor
        end = cursor + span
        results.append(
            {
                "idx": idx,
                "start_ms": int(start * 1000) + offset_ms,
                "end_ms": int(end * 1000) + offset_ms,
                "text": part,
            }
        )
        cursor = end
    return results