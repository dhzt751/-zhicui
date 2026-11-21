import difflib
import re

from ..config import llm_ready
from . import mock
from .llm import LLMError, chat_json

KP_SYSTEM = """你是严谨的课程内容编辑，负责从视频字幕中提炼知识点。判断标准只有一条：这条内容对复习是否有用。

必须保留：概念定义、原理与因果、公式与推导、方法步骤、典型例题、明确结论、易错点。
必须丢弃：寒暄、自我介绍、求关注点赞投币、广告与商务口播、口头禅、重复啰嗦、情绪表达、闲聊、下集预告、感谢观看、纯过渡语。
宁可少提，也不要凑数。每个知识点必须能在给定字幕里找到原文依据，禁止编造、禁止把一句话拆成多个知识点。
confidence 表示你对"这确实是一个独立知识点"的把握，低于 0.55 的直接不要输出。只输出 JSON。"""

KP_USER = """视频标题：{title}
可能涉及的领域术语：{context}
上一段已提取的知识点标题（不要重复）：{previous}

以下是第 {index} 段字幕（格式为 [开始秒-结束秒] 文本）：
{body}

请输出 JSON：
{{"knowledge_points": [{{"title": "不超过20字，是名词性短语", "summary": "40到80字，讲清楚是什么、为什么重要", "start": 12.5, "end": 45.0, "keywords": ["3到5个术语"], "difficulty": "入门或进阶或深入", "kind": "定义或公式或步骤或例题或结论或易错点", "confidence": 0.85, "evidence": "支撑这条知识点的原文短句"}}]}}
要求：
1. 知识点之间时间不要重叠，本段最多 {limit} 个；
2. confidence 低于 0.55 的不要输出；
3. 如果这段只是寒暄、广告或过渡，返回空数组。"""

SUMMARY_SYSTEM = """你是课程总结助手。根据已提炼的知识点写结构化总结，不引入视频中没有的内容，不夸大。只输出 JSON。"""

SUMMARY_USER = """视频标题：{title}
时长：{duration} 秒
知识点：
{points}

输出 JSON：
{{"overview": "60到120字总览", "chapters": [{{"title": "章节名", "start": 0.0, "summary": "40到80字", "key_points": ["2到4条"]}}], "conclusion": "40到80字的学习建议或核心结论"}}
章节数量不超过 8 个，按时间顺序。"""

KINDS = {"定义", "公式", "步骤", "例题", "结论", "易错点", "背景", "其他"}


def _chunks(segments: list[dict], max_chars: int = 3200, overlap: int = 2) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for seg in segments:
        line = f"[{seg['start_ms'] / 1000:.1f}-{seg['end_ms'] / 1000:.1f}] {seg['text']}"
        if current and size + len(line) > max_chars:
            chunks.append(current)
            kept = current[-overlap:] if overlap else []
            current = list(kept)
            size = sum(
                len(f"[{s['start_ms'] / 1000:.1f}-{s['end_ms'] / 1000:.1f}] {s['text']}") + 1
                for s in current
            )
        current.append(seg)
        size += len(line) + 1
    if current:
        chunks.append(current)
    return chunks


def _render(chunk: list[dict]) -> str:
    return "\n".join(
        f"[{seg['start_ms'] / 1000:.1f}-{seg['end_ms'] / 1000:.1f}] {seg['text']}" for seg in chunk
    )


def _normalize_title(title: str) -> str:
    return re.sub(r"[\s，。、：:；;（）()【】\[\]\"'“”‘’]", "", title or "").lower()


def _merge(points: list[dict], duration: float) -> list[dict]:
    points.sort(key=lambda p: p.get("start_ms", 0))
    merged: list[dict] = []
    for point in points:
        if not point.get("title"):
            continue
        if merged:
            prev = merged[-1]
            same_title = (
                difflib.SequenceMatcher(
                    None, _normalize_title(prev["title"]), _normalize_title(point["title"])
                ).ratio()
                >= 0.72
            )
            overlap = min(prev["end_ms"], point["end_ms"]) - max(prev["start_ms"], point["start_ms"])
            close = 0 <= point["start_ms"] - prev["end_ms"] <= 8000
            if same_title or (overlap > 0 and close):
                prev["end_ms"] = max(prev["end_ms"], point["end_ms"])
                if len(point["summary"]) > len(prev["summary"]):
                    prev["summary"] = point["summary"]
                if len(point.get("evidence") or "") > len(prev.get("evidence") or ""):
                    prev["evidence"] = point["evidence"]
                prev["confidence"] = max(prev.get("confidence") or 0, point.get("confidence") or 0)
                if point.get("kind") and not prev.get("kind"):
                    prev["kind"] = point["kind"]
                prev["keywords"] = list(dict.fromkeys(prev["keywords"] + point["keywords"]))[:6]
                continue
        merged.append(point)
    limit_ms = int(duration * 1000) if duration else 0
    for point in merged:
        point["start_ms"] = max(0, int(point["start_ms"]))
        point["end_ms"] = max(point["start_ms"] + 1000, int(point["end_ms"]))
        if limit_ms:
            point["start_ms"] = min(point["start_ms"], max(0, limit_ms - 1000))
            point["end_ms"] = min(point["end_ms"], limit_ms)
    return merged[:32]


def _clean_point(raw: dict) -> dict | None:
    title = re.sub(r"\s+", " ", str(raw.get("title") or "")).strip()
    if not title:
        return None
    summary = re.sub(r"\s+", " ", str(raw.get("summary") or "")).strip()
    keywords = raw.get("keywords") or []
    if not isinstance(keywords, list):
        keywords = [str(keywords)]
    keywords = [str(k).strip() for k in keywords if str(k).strip()][:6]
    try:
        start = float(raw.get("start", 0)) * 1000
        end = float(raw.get("end", start / 1000 + 30)) * 1000
    except (TypeError, ValueError):
        return None
    try:
        confidence = float(raw.get("confidence", 0.8))
    except (TypeError, ValueError):
        confidence = 0.8
    confidence = max(0.0, min(1.0, confidence))
    if confidence < 0.55:
        return None
    kind = str(raw.get("kind") or "").strip()[:10]
    if kind not in KINDS:
        kind = "其他" if kind else ""
    if end <= start:
        end = start + 30_000
    return {
        "title": title[:40],
        "summary": summary[:200],
        "start_ms": int(start),
        "end_ms": int(end),
        "keywords": keywords,
        "difficulty": str(raw.get("difficulty") or "")[:10],
        "kind": kind,
        "confidence": round(confidence, 2),
        "evidence": re.sub(r"\s+", " ", str(raw.get("evidence") or ""))[:200],
    }


def extract_knowledge(
    segments: list[dict], duration: float, title: str, progress_cb=None
) -> tuple[list[dict], bool]:
    if not segments:
        return [], False
    if not llm_ready():
        if progress_cb:
            progress_cb(45, "演示模式：规则切分知识点")
        return mock.demo_knowledge_points(segments, duration), True

    chunks = _chunks(segments)
    context = "、".join(mock.keywords(title, 6)) or "未识别"
    points: list[dict] = []
    demo = False
    previous_titles: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        if progress_cb:
            progress_cb(36 + int(30 * index / max(len(chunks), 1)), f"提炼知识点 {index}/{len(chunks)}")
        body = _render(chunk)
        user = KP_USER.format(
            title=title or "未命名视频",
            context=context,
            previous="、".join(previous_titles[-12:]) or "无",
            index=index,
            body=body,
            limit=8,
        )
        try:
            data = chat_json(KP_SYSTEM, user)
        except LLMError:
            demo = True
            points.extend(mock.demo_knowledge_points(segments, duration))
            break
        raw_points = data.get("knowledge_points") if isinstance(data, dict) else data
        if not isinstance(raw_points, list):
            continue
        for raw in raw_points:
            if not isinstance(raw, dict):
                continue
            cleaned = _clean_point(raw)
            if cleaned:
                points.append(cleaned)
                previous_titles.append(cleaned["title"])
    if not points and not demo:
        return [], False
    return _merge(points, duration), demo


def summarize(title: str, points: list[dict], duration: float) -> dict:
    if not points:
        return {
            "overview": "这段内容没有提炼出明确的知识点，可能是闲聊、广告或纯演示内容。",
            "chapters": [],
            "conclusion": "可以换一段讲解更集中的视频，或手动导入字幕/讲义后重试。",
        }
    if not llm_ready():
        return mock.demo_summary(title, points)
    body = "\n".join(
        f"- [{p['start_ms'] / 1000:.0f}s] {p['title']}：{p['summary']}（关键词：{'、'.join(p['keywords'])}）"
        for p in points
    )
    user = SUMMARY_USER.format(title=title or "未命名视频", duration=int(duration or 0), points=body)
    try:
        data = chat_json(SUMMARY_SYSTEM, user)
    except LLMError:
        return mock.demo_summary(title, points)
    if not isinstance(data, dict):
        return mock.demo_summary(title, points)
    chapters = []
    for chapter in data.get("chapters") or []:
        if not isinstance(chapter, dict):
            continue
        chapters.append(
            {
                "title": str(chapter.get("title") or "")[:60],
                "start_ms": int(float(chapter.get("start", 0) or 0) * 1000),
                "summary": str(chapter.get("summary") or "")[:300],
                "key_points": [str(k)[:80] for k in (chapter.get("key_points") or [])][:6],
            }
        )
    return {
        "overview": str(data.get("overview") or "")[:600],
        "chapters": chapters[:8],
        "conclusion": str(data.get("conclusion") or "")[:400],
    }