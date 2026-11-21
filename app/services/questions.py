import math
import re
from collections import Counter

from ..config import llm_ready
from . import mock
from .llm import LLMError, chat_json, embeddings

QUESTION_SYSTEM = """你是题库整理助手，从文档中抽取"题目"。
只抽取真正的题目（选择题、填空题、判断题、简答题、计算题）。
每道题提取：序号、题干、选项、答案、解析、考查的知识点标签。
题干要完整，不要截断；没有答案或解析就留空，绝不编造；不是题目的文字直接忽略。
只输出 JSON。"""

QUESTION_USER = """以下是文档的第 {index} 段内容：

{body}

输出 JSON：
{{"questions": [{{"number": 1, "stem": "完整题干", "options": ["A. ...", "B. ..."], "answer": "答案", "analysis": "解析", "tags": ["知识点标签"]}}]}}
要求：标签用 2 到 4 个简短术语；这段最多 {limit} 道题；没有题目就返回空数组。"""

CLUSTER_SYSTEM = """你是知识体系梳理助手。为同一知识点的题组起一个准确、简短的知识点名称和一句概述。只输出 JSON。"""

CLUSTER_USER = """下面每行是一个题组，格式为「编号 || 标签 || 代表题目」：
{lines}

输出 JSON：
{{"clusters": [{{"id": 编号, "title": "知识点名称（不超过20字）", "summary": "一句话说明这类题考什么"}}]}}"""


def _normalize(text: str) -> str:
    return re.sub(r"[\s，。、；;：:（）()【】\[\]\"'“”‘’]", "", text or "").lower()


def _chunks(text: str, max_chars: int = 5000, max_chunks: int = 10) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(text) and len(chunks) < max_chunks:
        end = min(len(text), start + max_chars)
        if end < len(text):
            cut = text.rfind("\n", start, end)
            if cut > start + max_chars // 2:
                end = cut
        chunks.append(text[start:end])
        start = end
    return chunks


def _clean_question(raw: dict, index: int) -> dict | None:
    stem = re.sub(r"\s+", " ", str(raw.get("stem") or "")).strip()
    if len(stem) < 6:
        return None
    options = raw.get("options") or []
    if not isinstance(options, list):
        options = [str(options)]
    options = [re.sub(r"\s+", " ", str(o)).strip() for o in options if str(o).strip()][:8]
    tags = raw.get("tags") or []
    if not isinstance(tags, list):
        tags = [str(tags)]
    tags = [re.sub(r"\s+", " ", str(t)).strip() for t in tags if str(t).strip()][:5]
    return {
        "number": int(raw.get("number") or index),
        "stem": stem[:2000],
        "options": options,
        "answer": str(raw.get("answer") or "")[:400],
        "analysis": str(raw.get("analysis") or "")[:1200],
        "tags": tags,
    }


def extract_questions(text: str, progress_cb=None) -> tuple[list[dict], bool]:
    chunks = _chunks(text)
    if not chunks:
        return [], False
    if not llm_ready():
        if progress_cb:
            progress_cb(45, "演示模式：规则切分题目")
        return mock.demo_questions(text), True

    demo = False
    questions: list[dict] = []
    for index, body in enumerate(chunks, start=1):
        if progress_cb:
            progress_cb(36 + int(34 * index / len(chunks)), f"抽取题目 {index}/{len(chunks)}")
        try:
            data = chat_json(
                QUESTION_SYSTEM,
                QUESTION_USER.format(index=index, body=body, limit=40),
            )
        except LLMError:
            demo = True
            questions.extend(mock.demo_questions(text))
            break
        raw_list = data.get("questions") if isinstance(data, dict) else data
        if not isinstance(raw_list, list):
            continue
        for offset, raw in enumerate(raw_list, start=1):
            if isinstance(raw, dict):
                cleaned = _clean_question(raw, len(questions) + offset)
                if cleaned:
                    questions.append(cleaned)
    if not questions and not demo:
        demo = True
        questions = mock.demo_questions(text)

    seen: set[str] = set()
    unique: list[dict] = []
    for question in questions:
        key = _normalize(question["stem"])[:120]
        if key in seen:
            continue
        seen.add(key)
        unique.append(question)
    for idx, question in enumerate(unique, start=1):
        question["number"] = idx
    return unique, demo


def _grams(text: str) -> Counter[str]:
    clean = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]+", " ", text or "").lower()
    tokens: list[str] = []
    for word in re.findall(r"[a-z][a-z0-9_\-]{1,}", clean):
        tokens.append(word)
    try:
        import jieba

        for word in jieba.lcut(clean):
            token = word.strip()
            if len(token) >= 2 and re.search(r"[\u4e00-\u9fa5]", token):
                tokens.append(token)
    except Exception:
        pass
    for run in re.findall(r"[\u4e00-\u9fa5]{2,}", clean):
        for size in (2, 3):
            tokens.extend(run[i : i + size] for i in range(len(run) - size + 1))
    return Counter(tokens)


def _tfidf(texts: list[str]) -> list[Counter[str]]:
    docs = [_grams(t) for t in texts]
    df: Counter[str] = Counter()
    for doc in docs:
        df.update(doc.keys())
    total = max(len(docs), 1)
    vectors: list[Counter[str]] = []
    for doc in docs:
        vector: Counter[str] = Counter()
        for term, count in doc.items():
            tf = count / (sum(doc.values()) or 1)
            idf = math.log((total + 1) / (df[term] + 1)) + 1
            vector[term] = tf * idf
        vectors.append(vector)
    return vectors


def _cosine(left: dict, right: dict) -> float:
    if not left or not right:
        return 0.0
    common = set(left) & set(right)
    numerator = sum(left[k] * right[k] for k in common)
    norm_left = math.sqrt(sum(v * v for v in left.values()))
    norm_right = math.sqrt(sum(v * v for v in right.values()))
    if not norm_left or not norm_right:
        return 0.0
    return numerator / (norm_left * norm_right)


def cluster_questions(questions: list[dict]) -> list[dict]:
    if not questions:
        return []
    texts = [
        " ".join(
            [
                q["stem"],
                " ".join(q.get("options") or []),
                q.get("analysis") or "",
                " ".join(q.get("tags") or []),
            ]
        )
        for q in questions
    ]
    vectors = embeddings(texts)
    using_embedding = vectors is not None
    if vectors is None:
        vectors = _tfidf(texts)

    clusters: list[dict] = []
    for index, question in enumerate(questions):
        vector = vectors[index]
        best: dict | None = None
        best_score = 0.0
        for cluster in clusters:
            similarity = _cosine(vector, cluster["vector"])
            overlap = bool(set(question.get("tags") or []) & set(cluster["tags"]))
            score = similarity + (0.1 if overlap else 0)
            threshold = (0.84 if using_embedding else 0.58) - (0.12 if overlap else 0)
            if score >= threshold and score > best_score:
                best_score = score
                best = cluster
        if best is not None:
            best["members"].append(index)
            best["tags"] = list(dict.fromkeys(best["tags"] + (question.get("tags") or [])))[:8]
        else:
            clusters.append(
                {
                    "vector": vector,
                    "members": [index],
                    "tags": list(question.get("tags") or [])[:8],
                }
            )

    titles = _name_clusters(clusters, questions)
    results: list[dict] = []
    for order, cluster in enumerate(clusters, start=1):
        title, summary = titles.get(order - 1, ("", ""))
        if not title:
            title, tags = mock.demo_cluster_title([questions[i] for i in cluster["members"]])
            cluster["tags"] = list(dict.fromkeys(cluster["tags"] + tags))
            summary = summary or f"共 {len(cluster['members'])} 道题，围绕{title}。"
        results.append(
            {
                "title": title,
                "summary": summary,
                "tags": cluster["tags"][:8],
                "member_indexes": cluster["members"],
            }
        )
    return results


def _name_clusters(clusters: list[dict], questions: list[dict]) -> dict[int, tuple[str, str]]:
    if not llm_ready():
        return {}
    lines = []
    for index, cluster in enumerate(clusters):
        sample = questions[cluster["members"][0]]
        tags = "、".join(cluster["tags"][:4]) or "无"
        stem = sample["stem"][:80]
        lines.append(f"{index} || {tags} || {stem}")
    try:
        data = chat_json(CLUSTER_SYSTEM, CLUSTER_USER.format(lines="\n".join(lines)))
    except LLMError:
        return {}
    named: dict[int, tuple[str, str]] = {}
    for item in (data.get("clusters") if isinstance(data, dict) else []) or []:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        named[idx] = (str(item.get("title") or "")[:40], str(item.get("summary") or "")[:200])
    return named