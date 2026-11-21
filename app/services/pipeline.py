import re
import shutil
import subprocess
from pathlib import Path

from ..config import WORK_DIR, asr_ready, llm_ready
from ..db import dump, execute, now, query, query_one
from . import docs, mock, notes, questions, transcribe, video as video_svc


def _assert_video(video_id: int) -> None:
    if not query_one("SELECT id FROM videos WHERE id = ?", [video_id]):
        raise RuntimeError("视频已被删除，任务终止。")


def _assert_document(doc_id: int) -> None:
    if not query_one("SELECT id FROM documents WHERE id = ?", [doc_id]):
        raise RuntimeError("文档已被删除，任务终止。")


def _update_video(video_id: int, **fields) -> None:
    fields["updated_at"] = now()
    keys = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE videos SET {keys} WHERE id=?", [*fields.values(), video_id])


def _update_document(doc_id: int, **fields) -> None:
    fields["updated_at"] = now()
    keys = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE documents SET {keys} WHERE id=?", [*fields.values(), doc_id])


def probe_duration(path: Path | None) -> float:
    if not path:
        return 0.0
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        return float(result.stdout.strip() or 0)
    except Exception:
        return 0.0


def _transcribe_parts(
    parts: list[Path], progress, total_duration: float = 0.0, chunk_seconds: float = 1200.0
) -> list[dict]:
    segments: list[dict] = []
    offset = 0
    for index, part in enumerate(parts, start=1):
        progress(26 + int(20 * index / len(parts)), f"语音转写 {index}/{len(parts)}")
        if total_duration and len(parts) == 1:
            expected = total_duration
        else:
            remaining = max(total_duration - offset / 1000.0, 0.0) if total_duration else chunk_seconds
            expected = min(chunk_seconds, remaining) if remaining else chunk_seconds
        piece = transcribe.transcribe(part, offset, expected)
        segments.extend(piece)
        offset = piece[-1]["end_ms"] if piece else offset
    if not segments:
        raise RuntimeError("没有识别到有效语音，视频可能无人声或转写服务不可用。")
    return segments


def run_video_pipeline(video_id: int, url: str | None, local_path: Path | None, progress) -> None:
    workdir = WORK_DIR / f"video_{video_id}"
    workdir.mkdir(parents=True, exist_ok=True)
    title = "本地上传视频"
    duration = probe_duration(local_path)
    platform = "upload"
    info: dict = {}

    if url:
        progress(6, "解析视频信息")
        platform, _ = video_svc.detect_platform(url)
        if platform == "bilibili":
            info = video_svc.resolve_bilibili(url)
        else:
            info = video_svc.fetch_info(url, workdir)
        embed = video_svc.embed_info(info, platform)
        title = str(info.get("title") or "未命名视频")
        duration = float(info.get("duration") or duration or 0)
        _update_video(
            video_id,
            platform=platform,
            title=title,
            author=str(info.get("uploader") or info.get("channel") or ""),
            duration=duration,
            cover=str(info.get("thumbnail") or ""),
            external_id=embed["external_id"],
            play_url=embed["play_url"],
        )

    segments: list[dict] = []
    if info:
        picked = video_svc.pick_subtitle(info)
        if picked:
            lang, item = picked
            progress(16, f"读取 {lang} 字幕")
            downloaded = video_svc.download_subtitle(item, workdir)
            if downloaded:
                segments = video_svc.parse_subtitle(*downloaded)

    source = "subtitle"
    if not segments:
        source = "asr"
        if not asr_ready():
            raise RuntimeError(
                "这个视频没有可用的平台字幕，且没有配置语音转写服务（ASR），"
                "无法得到真实讲解内容。两条路：一是在 .env 配置 ASR_BASE_URL / ASR_API_KEY 后重试；"
                "二是在视频详情页手动导入字幕或讲义文本，再重新提炼。"
            )
        progress(22, "获取音频")
        if local_path:
            audio_path = local_path
        elif platform == "bilibili" and info.get("_audio_url"):
            audio_path = video_svc.download_bilibili_audio(info, workdir)
        else:
            audio_path = video_svc.download_audio(url or "", workdir)
        parts = video_svc.split_audio(audio_path, workdir)
        segments = _transcribe_parts(parts, progress, duration)

    _analyze(video_id, segments, duration, title, source, progress)


def _analyze(
    video_id: int,
    segments: list[dict],
    duration: float,
    title: str,
    source: str,
    progress,
) -> None:
    segments = [seg for seg in segments if str(seg.get("text") or "").strip()]
    if not segments:
        raise RuntimeError("没有可分析的字幕内容，无法提炼知识点。")
    segments.sort(key=lambda seg: seg["start_ms"])
    _assert_video(video_id)
    labels = {"subtitle": "平台字幕", "asr": "语音转写", "import": "导入字幕"}
    progress(48, f"保存字幕分段（来源：{labels.get(source, source)}）")
    execute("DELETE FROM segments WHERE video_id=?", [video_id])
    for index, seg in enumerate(segments):
        execute(
            "INSERT INTO segments (video_id, idx, start_ms, end_ms, text) VALUES (?, ?, ?, ?, ?)",
            (video_id, index, int(seg["start_ms"]), int(seg["end_ms"]), seg["text"]),
        )

    points, _demo = notes.extract_knowledge(segments, duration, title, progress)
    _assert_video(video_id)
    execute("DELETE FROM knowledge_points WHERE video_id=?", [video_id])
    for point in points:
        execute(
            "INSERT INTO knowledge_points"
            " (video_id, title, summary, start_ms, end_ms, keywords, difficulty, kind, confidence, evidence)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                video_id,
                point["title"],
                point["summary"],
                point["start_ms"],
                point["end_ms"],
                dump(point["keywords"]),
                point["difficulty"],
                point.get("kind") or "",
                float(point.get("confidence") or 0),
                point.get("evidence") or "",
            ),
        )

    progress(84, "生成视频总结")
    summary = notes.summarize(title, points, duration)
    stage = "完成" if points else "完成（未发现明确知识点）"
    _update_video(
        video_id,
        summary=dump(summary),
        transcript_source=source,
        status="done",
        progress=100,
        stage=stage,
        error="",
    )


TIME_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def _to_ms(hours: str, minutes: str, seconds: str, millis: str) -> int:
    ms = int((millis + "00")[:3])
    return ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + ms


def parse_transcript_text(text: str, duration: float = 0.0) -> list[dict]:
    content = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not content:
        return []
    segments: list[dict] = []
    for block in re.split(r"\n\s*\n", content):
        lines = [
            line.strip()
            for line in block.split("\n")
            if line.strip() and not line.strip().upper().startswith(("WEBVTT", "NOTE", "KIND:", "LANGUAGE:"))
        ]
        time_line = None
        texts: list[str] = []
        for line in lines:
            match = TIME_RE.search(line)
            if match and time_line is None:
                time_line = match
                continue
            if re.fullmatch(r"\d{1,5}", line):
                continue
            texts.append(re.sub(r"<[^>]+>", "", line))
        if time_line and texts:
            groups = time_line.groups()
            segments.append(
                {
                    "start_ms": _to_ms(*groups[:4]),
                    "end_ms": _to_ms(*groups[4:]),
                    "text": re.sub(r"\s+", " ", " ".join(texts)).strip(),
                }
            )
    if segments:
        segments.sort(key=lambda seg: seg["start_ms"])
        return segments

    parts = [part.strip() for part in re.split(r"[。！？!?\n]+", content) if len(part.strip()) >= 4]
    if not parts:
        return []
    total = duration or max(len(content) / 5.0, 30.0)
    step = total / len(parts)
    return [
        {
            "start_ms": int(index * step * 1000),
            "end_ms": int((index + 1) * step * 1000),
            "text": part,
        }
        for index, part in enumerate(parts)
    ]


def run_reanalyze(video_id: int, text: str, progress) -> None:
    video = query_one("SELECT title, duration FROM videos WHERE id = ?", [video_id])
    if not video:
        raise RuntimeError("视频不存在。")
    progress(20, "解析导入的字幕")
    duration = float(video.get("duration") or 0)
    segments = parse_transcript_text(text, duration)
    if not segments:
        raise RuntimeError("导入的内容无法识别为字幕：请使用 SRT / VTT，或一段连贯的讲解文本。")
    _analyze(video_id, segments, duration, str(video.get("title") or ""), "import", progress)


def run_document_pipeline(doc_id: int, path: Path, filename: str, progress) -> None:
    progress(8, "读取文档")
    text, kind = docs.extract_text(path, filename, progress)
    _update_document(doc_id, kind=kind, text=text, status="running")
    if len(text.strip()) < 20:
        raise RuntimeError("文档内容过少，无法提炼题目。")

    extracted, _demo = questions.extract_questions(text, progress)
    _assert_document(doc_id)
    progress(74, "按知识点聚合题目")
    clusters = questions.cluster_questions(extracted)

    execute("DELETE FROM questions WHERE document_id=?", [doc_id])
    execute("DELETE FROM clusters WHERE document_id=?", [doc_id])
    for order, cluster in enumerate(clusters, start=1):
        cluster_id = execute(
            "INSERT INTO clusters (document_id, title, summary, tags, size, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                doc_id,
                cluster["title"],
                cluster["summary"],
                dump(cluster["tags"]),
                len(cluster["member_indexes"]),
                now(),
            ),
        )
        for member in cluster["member_indexes"]:
            question = extracted[member]
            execute(
                "INSERT INTO questions (document_id, cluster_id, number, stem, options, answer, analysis, tags, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    doc_id,
                    cluster_id,
                    question["number"],
                    question["stem"],
                    dump(question["options"]),
                    question["answer"],
                    question["analysis"],
                    dump(question["tags"]),
                    now(),
                ),
            )
    _update_document(doc_id, status="done", progress=100, stage="完成", error="")


def run_recluster(doc_id: int, progress) -> None:
    rows = query(
        "SELECT id, stem, options, answer, analysis, tags FROM questions WHERE document_id=? ORDER BY number",
        [doc_id],
    )
    if not rows:
        raise RuntimeError("该文档还没有题目。")
    progress(12, "刷新知识点标签")
    if not llm_ready():
        for row in rows:
            full_text = " ".join(
                [row["stem"] or "", " ".join(row.get("options") or []), row.get("analysis") or ""]
            )
            tags = mock.keywords(full_text, 3)
            execute("UPDATE questions SET tags=? WHERE id=?", (dump(tags), row["id"]))
            row["tags"] = tags
    progress(30, "重新计算题目相似度")
    payload = [
        {
            "stem": row["stem"],
            "options": row.get("options") or [],
            "analysis": row.get("analysis") or "",
            "tags": row["tags"],
        }
        for row in rows
    ]
    clusters = questions.cluster_questions(payload)
    execute("DELETE FROM clusters WHERE document_id=?", [doc_id])
    execute("UPDATE questions SET cluster_id=NULL WHERE document_id=?", [doc_id])
    for cluster in clusters:
        cluster_id = execute(
            "INSERT INTO clusters (document_id, title, summary, tags, size, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                doc_id,
                cluster["title"],
                cluster["summary"],
                dump(cluster["tags"]),
                len(cluster["member_indexes"]),
                now(),
            ),
        )
        for member in cluster["member_indexes"]:
            execute(
                "UPDATE questions SET cluster_id=? WHERE id=?",
                (cluster_id, rows[member]["id"]),
            )
    _update_document(doc_id, status="done", progress=100, stage="重新聚合完成", error="")


def video_summary_markdown(video: dict, points: list[dict], summary: dict | None) -> str:
    lines = [f"# {video.get('title') or '未命名视频'}"]
    meta = []
    if video.get("author"):
        meta.append(f"作者：{video['author']}")
    if video.get("duration"):
        meta.append(f"时长：{format_duration(video['duration'])}")
    if meta:
        lines.append(" · ".join(meta))
    if summary:
        lines.append("")
        lines.append("## 总览")
        lines.append(summary.get("overview") or "")
        if summary.get("chapters"):
            lines.append("")
            lines.append("## 章节精要")
            for chapter in summary["chapters"]:
                lines.append(
                    f"### {chapter['title']}（{format_duration(chapter.get('start_ms', 0) / 1000)}）"
                )
                lines.append(chapter.get("summary") or "")
                for key in chapter.get("key_points") or []:
                    lines.append(f"- {key}")
        if summary.get("conclusion"):
            lines.append("")
            lines.append("## 结论与建议")
            lines.append(summary["conclusion"])
    if points:
        lines.append("")
        lines.append("## 知识点时间轴")
        for point in points:
            keywords = "、".join(point.get("keywords") or [])
            lines.append(
                f"- `{format_duration(point['start_ms'] / 1000)}` **{point['title']}** — {point['summary']}"
                + (f"（{keywords}）" if keywords else "")
            )
    return "\n".join(lines)


def format_duration(seconds: float) -> str:
    total = int(seconds or 0)
    hours, remain = divmod(total, 3600)
    minutes, secs = divmod(remain, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"