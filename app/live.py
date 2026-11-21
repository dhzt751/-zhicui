import io
import json
import time
import wave

from fastapi import WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from .config import WORK_DIR, asr_ready, llm_ready
from .services import notes, transcribe


def pcm_to_wav(pcm: bytes, sample_rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)
    return buffer.getvalue()


def register_live(app) -> None:
    @app.websocket("/ws/live")
    async def live_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        session_dir = WORK_DIR / f"live_{int(time.time())}"
        session_dir.mkdir(parents=True, exist_ok=True)
        title = "实时听课"
        offset_ms = 0
        pending: list[dict] = []
        seen_titles: set[str] = set()
        seq = 0
        try:
            await websocket.send_json(
                {"type": "ready", "asr": asr_ready(), "llm": llm_ready()}
            )
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                if message.get("text"):
                    try:
                        payload = json.loads(message["text"])
                    except Exception:
                        continue
                    if payload.get("type") == "ping":
                        await websocket.send_json({"type": "pong"})
                        continue
                    if payload.get("type") == "start":
                        title = str(payload.get("title") or "实时听课")[:100]
                        await websocket.send_json({"type": "started", "title": title})
                    elif payload.get("type") == "stop":
                        break
                    continue
                pcm = message.get("bytes")
                if not pcm:
                    continue
                if not asr_ready():
                    await websocket.send_json(
                        {"type": "error", "message": "未配置语音转写服务，无法实时转写。"}
                    )
                    continue
                seq += 1
                path = session_dir / f"chunk_{seq:04d}.wav"
                path.write_bytes(pcm_to_wav(pcm))
                try:
                    segments = await run_in_threadpool(
                        transcribe.transcribe, path, offset_ms, 8.0
                    )
                except Exception as exc:
                    await websocket.send_json(
                        {"type": "error", "message": f"转写失败：{str(exc)[:160]}"}
                    )
                    continue
                if not segments:
                    continue
                offset_ms = max(segments[-1]["end_ms"], offset_ms)
                text = " ".join(seg["text"] for seg in segments).strip()
                await websocket.send_json(
                    {
                        "type": "transcript",
                        "text": text,
                        "start_ms": segments[0]["start_ms"],
                        "end_ms": segments[-1]["end_ms"],
                    }
                )
                pending.extend(segments)
                chars = sum(len(seg["text"]) for seg in pending)
                if llm_ready() and (len(pending) >= 4 or chars >= 400):
                    try:
                        points, _ = await run_in_threadpool(
                            notes.extract_knowledge, pending, 0.0, title
                        )
                    except Exception:
                        points = []
                    fresh = []
                    for point in points:
                        if point["title"] in seen_titles:
                            continue
                        seen_titles.add(point["title"])
                        fresh.append(
                            {
                                "title": point["title"],
                                "summary": point["summary"],
                                "start_ms": point["start_ms"],
                                "end_ms": point["end_ms"],
                                "kind": point.get("kind", ""),
                                "confidence": point.get("confidence", 0),
                                "keywords": point.get("keywords", []),
                            }
                        )
                    pending = []
                    if fresh:
                        await websocket.send_json({"type": "knowledge", "points": fresh})
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            try:
                await websocket.send_json({"type": "error", "message": str(exc)[:200]})
            except Exception:
                pass
        finally:
            try:
                await websocket.close()
            except Exception:
                pass