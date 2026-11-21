import asyncio
import json
import shutil
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import (
    ASR_MODEL,
    HOST,
    LLM_BASE_URL,
    LLM_MODEL,
    MAX_DOCUMENT_UPLOAD,
    MAX_VIDEO_UPLOAD,
    UPLOAD_DIR,
    asr_ready,
    llm_ready,
)
from .db import dump, execute, init_db, now, query, query_one
from .services import jobs, pipeline, video as video_svc

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".flv"}
DOC_EXT = {".pdf", ".docx", ".txt", ".md", ".markdown", ".csv", ".png", ".jpg", ".jpeg", ".webp", ".bmp"}

init_db()

app = FastAPI(title="知萃 · 学习知识点提炼站")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/media", StaticFiles(directory=UPLOAD_DIR), name="media")


class VideoIn(BaseModel):
    url: str


class QuestionPatch(BaseModel):
    cluster_id: int | None = None
    tags: list[str] | None = None


class TranscriptIn(BaseModel):
    text: str


def _error(message: str, code: int = 400) -> None:
    raise HTTPException(status_code=code, detail=message)


def _parse_summary(raw: str) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def _save_upload(file: UploadFile, target: Path, limit: int) -> int:
    size = 0
    with target.open("wb") as handle:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                handle.close()
                target.unlink(missing_ok=True)
                _error(f"文件超过 {limit // 1024 // 1024}MB 限制。", 413)
            handle.write(chunk)
    if size == 0:
        target.unlink(missing_ok=True)
        _error("上传的文件为空。")
    return size


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
def status() -> dict:
    return {
        "llm_ready": llm_ready(),
        "asr_ready": asr_ready(),
        "model": LLM_MODEL,
        "base_url": LLM_BASE_URL,
        "asr_model": ASR_MODEL,
        "demo_mode": not llm_ready(),
        "ffmpeg": bool(video_svc.ffmpeg_path()),
        "utorrent": False,
        "max_video_mb": MAX_VIDEO_UPLOAD // 1024 // 1024,
    }


@app.get("/api/stats")
def stats() -> dict:
    videos = query_one("SELECT COUNT(*) AS n FROM videos") or {"n": 0}
    points = query_one("SELECT COUNT(*) AS n FROM knowledge_points") or {"n": 0}
    documents = query_one("SELECT COUNT(*) AS n FROM documents") or {"n": 0}
    questions = query_one("SELECT COUNT(*) AS n FROM questions") or {"n": 0}
    clusters = query_one("SELECT COUNT(*) AS n FROM clusters") or {"n": 0}
    return {
        "videos": videos["n"],
        "knowledge_points": points["n"],
        "documents": documents["n"],
        "questions": questions["n"],
        "clusters": clusters["n"],
    }


@app.post("/api/videos")
def create_video(payload: VideoIn) -> dict:
    url = payload.url.strip()
    if not url.lower().startswith(("http://", "https://")):
        _error("请粘贴以 http:// 或 https:// 开头的视频链接。")
    video_svc.ensure_available()
    platform, _ = video_svc.detect_platform(url)
    timestamp = now()
    video_id = execute(
        "INSERT INTO videos (url, platform, status, stage, created_at, updated_at)"
        " VALUES (?, ?, 'queued', '排队中', ?, ?)",
        (url, platform, timestamp, timestamp),
    )
    job_id = jobs.create_job("video", "video", video_id)

    def work(progress):
        pipeline.run_video_pipeline(video_id, url, None, progress)

    def on_error(message: str):
        execute(
            "UPDATE videos SET status='failed', stage='失败', progress=0, error=?, updated_at=? WHERE id=?",
            (message[:800], now(), video_id),
        )

    jobs.submit(job_id, work, on_error)
    return {"video_id": video_id, "job_id": job_id}


@app.post("/api/videos/upload")
async def upload_video(file: UploadFile = File(...)) -> dict:
    filename = file.filename or "video.mp4"
    suffix = Path(filename).suffix.lower()
    if suffix not in VIDEO_EXT:
        _error("仅支持 mp4 / mkv / mov / webm / avi / m4v / flv。")
    stored = f"{uuid.uuid4().hex}{suffix}"
    target = UPLOAD_DIR / stored
    await _save_upload(file, target, MAX_VIDEO_UPLOAD)

    duration = pipeline.probe_duration(target)
    timestamp = now()
    title = Path(filename).stem[:120] or "本地上传视频"
    video_id = execute(
        "INSERT INTO videos (platform, url, title, duration, play_url, status, stage, created_at, updated_at)"
        " VALUES ('upload', '', ?, ?, ?, 'queued', '排队中', ?, ?)",
        (title, duration, f"/media/{stored}", timestamp, timestamp),
    )
    job_id = jobs.create_job("video", "video", video_id)

    def work(progress):
        pipeline.run_video_pipeline(video_id, None, target, progress)

    def on_error(message: str):
        execute(
            "UPDATE videos SET status='failed', stage='失败', progress=0, error=?, updated_at=? WHERE id=?",
            (message[:800], now(), video_id),
        )

    jobs.submit(job_id, work, on_error)
    return {"video_id": video_id, "job_id": job_id, "play_url": f"/media/{stored}"}


@app.get("/api/videos")
def list_videos() -> list[dict]:
    rows = query(
        "SELECT v.*, (SELECT COUNT(*) FROM knowledge_points k WHERE k.video_id = v.id) AS kp_count"
        " FROM videos v ORDER BY v.id DESC"
    )
    for row in rows:
        row["summary"] = _parse_summary(row.get("summary") or "")
    return rows


@app.get("/api/videos/{video_id}")
def get_video(video_id: int) -> dict:
    video = query_one("SELECT * FROM videos WHERE id = ?", [video_id])
    if not video:
        _error("视频不存在。", 404)
    video["summary"] = _parse_summary(video.get("summary") or "")
    segments = query(
        "SELECT idx, start_ms, end_ms, text FROM segments WHERE video_id = ? ORDER BY idx LIMIT 4000",
        [video_id],
    )
    points = query(
        "SELECT * FROM knowledge_points WHERE video_id = ? ORDER BY start_ms",
        [video_id],
    )
    job = query_one(
        "SELECT * FROM jobs WHERE ref_type = 'video' AND ref_id = ? ORDER BY id DESC LIMIT 1",
        [video_id],
    )
    return {"video": video, "segments": segments, "knowledge_points": points, "job": job}


@app.delete("/api/videos/{video_id}")
def delete_video(video_id: int) -> dict:
    video = query_one("SELECT * FROM videos WHERE id = ?", [video_id])
    if not video:
        _error("视频不存在。", 404)
    execute("DELETE FROM segments WHERE video_id = ?", [video_id])
    execute("DELETE FROM knowledge_points WHERE video_id = ?", [video_id])
    execute("DELETE FROM jobs WHERE ref_type = 'video' AND ref_id = ?", [video_id])
    execute("DELETE FROM videos WHERE id = ?", [video_id])
    if video.get("platform") == "upload" and video.get("play_url", "").startswith("/media/"):
        (UPLOAD_DIR / video["play_url"].split("/")[-1]).unlink(missing_ok=True)
    return {"ok": True}


@app.get("/api/videos/{video_id}/summary.md")
def export_summary(video_id: int) -> Response:
    video = query_one("SELECT * FROM videos WHERE id = ?", [video_id])
    if not video:
        _error("视频不存在。", 404)
    points = query(
        "SELECT * FROM knowledge_points WHERE video_id = ? ORDER BY start_ms", [video_id]
    )
    markdown = pipeline.video_summary_markdown(video, points, _parse_summary(video.get("summary") or ""))
    filename = quote(f"{video.get('title') or 'video'}-知识点总结.md")
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@app.post("/api/videos/{video_id}/transcript")
def import_transcript(video_id: int, payload: TranscriptIn) -> dict:
    if not query_one("SELECT id FROM videos WHERE id = ?", [video_id]):
        _error("视频不存在。", 404)
    text = (payload.text or "").strip()
    if len(text) < 20:
        _error("内容太短，至少需要一段完整的讲解文字。")
    if len(text) > 5_000_000:
        _error("文本过大，请控制在 5MB 以内。")
    execute(
        "UPDATE videos SET status='running', progress=3, stage='导入字幕', error='', updated_at=? WHERE id=?",
        (now(), video_id),
    )
    job_id = jobs.create_job("reanalyze", "video", video_id)

    def work(progress):
        pipeline.run_reanalyze(video_id, text, progress)

    def on_error(message: str):
        execute(
            "UPDATE videos SET status='failed', stage='失败', progress=0, error=?, updated_at=? WHERE id=?",
            (message[:800], now(), video_id),
        )

    jobs.submit(job_id, work, on_error)
    return {"job_id": job_id}


@app.post("/api/documents")
async def upload_document(file: UploadFile = File(...)) -> dict:
    filename = file.filename or "document.pdf"
    suffix = Path(filename).suffix.lower()
    if suffix not in DOC_EXT:
        _error("仅支持 PDF、DOCX、TXT、MD、CSV 与图片。")
    stored = f"{uuid.uuid4().hex}{suffix}"
    target = UPLOAD_DIR / stored
    await _save_upload(file, target, MAX_DOCUMENT_UPLOAD)

    timestamp = now()
    doc_id = execute(
        "INSERT INTO documents (filename, kind, status, stage, created_at, updated_at)"
        " VALUES (?, ?, 'queued', '排队中', ?, ?)",
        (filename[:160], suffix.lstrip("."), timestamp, timestamp),
    )
    job_id = jobs.create_job("document", "document", doc_id)

    def work(progress):
        pipeline.run_document_pipeline(doc_id, target, filename, progress)

    def on_error(message: str):
        execute(
            "UPDATE documents SET status='failed', stage='失败', progress=0, error=?, updated_at=? WHERE id=?",
            (message[:800], now(), doc_id),
        )

    jobs.submit(job_id, work, on_error)
    return {"document_id": doc_id, "job_id": job_id}


@app.get("/api/documents")
def list_documents() -> list[dict]:
    return query(
        "SELECT d.id, d.filename, d.kind, d.status, d.progress, d.stage, d.error, d.created_at,"
        " (SELECT COUNT(*) FROM questions q WHERE q.document_id = d.id) AS question_count,"
        " (SELECT COUNT(*) FROM clusters c WHERE c.document_id = d.id) AS cluster_count"
        " FROM documents d ORDER BY d.id DESC"
    )


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: int) -> dict:
    document = query_one(
        "SELECT id, filename, kind, status, progress, stage, error, created_at FROM documents WHERE id = ?",
        [doc_id],
    )
    if not document:
        _error("文档不存在。", 404)
    clusters = query(
        "SELECT * FROM clusters WHERE document_id = ? ORDER BY size DESC, id", [doc_id]
    )
    questions = query(
        "SELECT * FROM questions WHERE document_id = ? ORDER BY cluster_id, number", [doc_id]
    )
    job = query_one(
        "SELECT * FROM jobs WHERE ref_type = 'document' AND ref_id = ? ORDER BY id DESC LIMIT 1",
        [doc_id],
    )
    return {"document": document, "clusters": clusters, "questions": questions, "job": job}


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: int) -> dict:
    if not query_one("SELECT id FROM documents WHERE id = ?", [doc_id]):
        _error("文档不存在。", 404)
    execute("DELETE FROM questions WHERE document_id = ?", [doc_id])
    execute("DELETE FROM clusters WHERE document_id = ?", [doc_id])
    execute("DELETE FROM jobs WHERE ref_type = 'document' AND ref_id = ?", [doc_id])
    execute("DELETE FROM documents WHERE id = ?", [doc_id])
    return {"ok": True}


@app.post("/api/documents/{doc_id}/recluster")
def recluster(doc_id: int) -> dict:
    if not query_one("SELECT id FROM documents WHERE id = ?", [doc_id]):
        _error("文档不存在。", 404)
    execute(
        "UPDATE documents SET status='running', progress=5, stage='重新聚合题目', error='', updated_at=? WHERE id=?",
        (now(), doc_id),
    )
    job_id = jobs.create_job("recluster", "document", doc_id)

    def work(progress):
        pipeline.run_recluster(doc_id, progress)

    def on_error(message: str):
        execute(
            "UPDATE documents SET status='failed', stage='失败', error=?, updated_at=? WHERE id=?",
            (message[:800], now(), doc_id),
        )

    jobs.submit(job_id, work, on_error)
    return {"job_id": job_id}


@app.patch("/api/questions/{question_id}")
def patch_question(question_id: int, payload: QuestionPatch) -> dict:
    question = query_one("SELECT * FROM questions WHERE id = ?", [question_id])
    if not question:
        _error("题目不存在。", 404)
    fields: dict = {}
    if payload.tags is not None:
        fields["tags"] = dump(payload.tags[:8])
    old_cluster = question.get("cluster_id")
    if "cluster_id" in payload.model_fields_set:
        fields["cluster_id"] = payload.cluster_id
    if fields:
        keys = ", ".join(f"{k}=?" for k in fields)
        execute(f"UPDATE questions SET {keys} WHERE id=?", [*fields.values(), question_id])
    if "cluster_id" in payload.model_fields_set and old_cluster != payload.cluster_id:
        for cluster_id in {old_cluster, payload.cluster_id}:
            if cluster_id:
                execute(
                    "UPDATE clusters SET size = (SELECT COUNT(*) FROM questions WHERE cluster_id = ?) WHERE id = ?",
                    (cluster_id, cluster_id),
                )
    return {"ok": True}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int) -> dict:
    job = query_one("SELECT * FROM jobs WHERE id = ?", [job_id])
    if not job:
        _error("任务不存在。", 404)
    return job


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: int) -> StreamingResponse:
    async def generator():
        last = None
        for _ in range(900):
            job = query_one("SELECT * FROM jobs WHERE id = ?", [job_id])
            if not job:
                break
            payload = json.dumps(job, ensure_ascii=False)
            if payload != last:
                yield f"data: {payload}\n\n"
                last = payload
            if job["status"] in ("done", "failed"):
                break
            await asyncio.sleep(0.7)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/knowledge")
def knowledge(q: str = "") -> dict:
    term = q.strip()
    like = f"%{term}%"
    points = query(
        "SELECT k.id, k.title, k.summary, k.start_ms, k.end_ms, k.keywords,"
        " v.id AS video_id, v.title AS video_title, v.platform"
        " FROM knowledge_points k JOIN videos v ON v.id = k.video_id"
        " WHERE (? = '' OR k.title LIKE ? OR k.summary LIKE ? OR k.keywords LIKE ?)"
        " ORDER BY k.id DESC LIMIT 80",
        [term, like, like, like],
    )
    clusters = query(
        "SELECT c.id, c.title, c.summary, c.tags, c.size, c.document_id, d.filename"
        " FROM clusters c JOIN documents d ON d.id = c.document_id"
        " WHERE (? = '' OR c.title LIKE ? OR c.summary LIKE ? OR c.tags LIKE ?)"
        " ORDER BY c.size DESC LIMIT 60",
        [term, like, like, like],
    )
    questions = query(
        "SELECT q.id, q.stem, q.tags, q.document_id, d.filename"
        " FROM questions q JOIN documents d ON d.id = q.document_id"
        " WHERE (? = '' OR q.stem LIKE ? OR q.tags LIKE ?) LIMIT 60",
        [term, like, like],
    )
    return {"points": points, "clusters": clusters, "questions": questions, "term": term}


@app.get("/api/config")
def config_snapshot() -> dict:
    return {
        "model": LLM_MODEL,
        "base_url": LLM_BASE_URL,
        "demo_mode": not llm_ready(),
        "host": HOST,
    }

from .live import register_live

register_live(app)
