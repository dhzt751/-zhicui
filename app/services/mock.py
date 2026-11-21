import re
from collections import Counter

CJK_STOP = {
    "我们",
    "这个",
    "那个",
    "然后",
    "就是",
    "可以",
    "大家",
    "现在",
    "什么",
    "怎么",
    "因为",
    "所以",
    "但是",
    "如果",
    "这样",
    "没有",
    "一个",
    "一下",
    "来看",
    "知道",
    "其实",
    "今天",
    "时候",
}

NOISE_PATTERNS = [
    r"关注",
    r"点赞",
    r"投币",
    r"收藏",
    r"转发",
    r"订阅",
    r"一键三连",
    r"充电",
    r"打赏",
    r"广告",
    r"商务",
    r"优惠",
    r"下期",
    r"下节课",
    r"再见",
    r"拜拜",
    r"哈喽",
    r"大家好",
    r"我是.{0,8}(老师|up主|主播)",
]

_SENT_SPLIT = re.compile(r"[。！？；\n]|(?<=[.!?])\s+")


def is_noise(text: str) -> bool:
    for pattern in NOISE_PATTERNS:
        if re.search(pattern, text, re.I):
            return True
    return False


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text) if s and s.strip()]


def keywords(text: str, limit: int = 5) -> list[str]:
    try:
        import jieba.posseg as pseg

        counter: Counter[str] = Counter()
        for pair in pseg.cut(text):
            word = str(pair.word).strip()
            flag = str(pair.flag)
            if not word or len(word) > 8:
                continue
            if not re.search(r"[\u4e00-\u9fa5A-Za-z]", word):
                continue
            if word in CJK_STOP:
                continue
            is_multi = len(word) >= 2
            if flag.startswith("n") or flag in ("vn", "eng", "an", "i", "l") or (flag == "v" and is_multi):
                if is_multi or flag.startswith("n"):
                    counter[word] += 1
        picked = [
            word
            for word, count in sorted(
                counter.items(), key=lambda item: (item[1] * min(len(item[0]), 4)), reverse=True
            )
            if count >= 2 or len(word) >= 2
        ]
        if picked:
            return picked[:limit]
    except Exception:
        pass

    counter = Counter()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_\-]{2,}", text):
        counter[token.lower()] += 2
    for run in re.findall(r"[\u4e00-\u9fa5]{2,12}", text):
        for size in (4, 3, 2):
            for i in range(0, len(run) - size + 1):
                gram = run[i : i + size]
                if gram in CJK_STOP:
                    continue
                counter[gram] += 1
    picked = []
    for term, count in counter.most_common(80):
        if count < 2:
            break
        if any(term in other for other in picked):
            continue
        picked.append(term)
        if len(picked) >= limit:
            break
    return picked


def demo_knowledge_points(segments: list[dict], duration: float) -> list[dict]:
    if not segments:
        return []
    total_ms = int((duration or segments[-1]["end_ms"] / 1000) * 1000)
    window = 90_000
    points: list[dict] = []
    bucket: list[dict] = []
    bucket_start = segments[0]["start_ms"]

    def flush(items: list[dict]) -> None:
        usable = [item for item in items if not is_noise(item["text"])]
        if not usable:
            return
        text = " ".join(item["text"] for item in usable).strip()
        if len(text) < 30:
            return
        clean = [s for s in sentences(text) if len(s) >= 6 and not is_noise(s)]
        if not clean:
            return
        title = re.sub(r"\s+", "", clean[0])[:22]
        body = " ".join(clean[:3])[:120]
        points.append(
            {
                "title": title,
                "summary": body,
                "start_ms": usable[0]["start_ms"],
                "end_ms": min(usable[-1]["end_ms"], total_ms or usable[-1]["end_ms"]),
                "keywords": keywords(text),
                "difficulty": "中",
                "kind": "知识点",
                "confidence": 0.66,
                "evidence": clean[0][:100],
            }
        )

    for seg in segments:
        if seg["start_ms"] - bucket_start > window and bucket:
            flush(bucket)
            bucket = []
            bucket_start = seg["start_ms"]
        bucket.append(seg)
    flush(bucket)
    return points[:24]


def demo_summary(title: str, points: list[dict]) -> dict:
    if not points:
        return {
            "overview": f"《{title}》暂未提取到明确的知识点，建议检查视频是否包含讲解内容。",
            "chapters": [],
            "conclusion": "内容以叙述为主，未形成可提炼的知识结构。",
        }
    chapters = [
        {
            "title": p["title"],
            "start_ms": p["start_ms"],
            "summary": p["summary"],
            "key_points": p["keywords"][:3] or [p["title"]],
        }
        for p in points[:8]
    ]
    return {
        "overview": f"《{title}》共提炼出 {len(points)} 个知识点，主要围绕"
        + "、".join(p["title"] for p in points[:3])
        + "展开。",
        "chapters": chapters,
        "conclusion": "建议按时间轴顺序过一遍知识点，再针对薄弱环节回看对应片段。",
    }


_QUESTION_SPLIT = re.compile(r"(?m)^\s*(?:\d{1,3}[.、)）]|[（(]\d{1,3}[)）]|[一二三四五六七八九十]+[、.])\s*")


def demo_questions(text: str, limit: int = 60) -> list[dict]:
    chunks = [c.strip() for c in _QUESTION_SPLIT.split(text) if c and len(c.strip()) > 12]
    results: list[dict] = []
    for i, chunk in enumerate(chunks[:limit]):
        lines = [line.strip() for line in chunk.splitlines() if line.strip()]
        if not lines:
            continue
        options = [line for line in lines if re.match(r"^[A-DＡ-Ｄ][.、)）]", line)]
        stem = lines[0] if not options else lines[0].split(options[0])[0].strip()
        answer = ""
        analysis = ""
        for line in lines:
            if re.match(r"^(答案|正确答案)[:：]", line):
                answer = re.sub(r"^(答案|正确答案)[:：]\s*", "", line)
            elif re.match(r"^(解析|分析)[:：]", line):
                analysis = re.sub(r"^(解析|分析)[:：]\s*", "", line)
        tags = keywords(chunk, limit=3)
        results.append(
            {
                "number": i + 1,
                "stem": stem[:400],
                "options": options[:6],
                "answer": answer[:120],
                "analysis": analysis[:400],
                "tags": tags,
            }
        )
    return results


def demo_cluster_title(questions: list[dict]) -> tuple[str, list[str]]:
    counter: Counter[str] = Counter()
    for q in questions:
        for tag in q.get("tags") or []:
            counter[tag] += 1
    tags = [t for t, _ in counter.most_common(3)]
    if tags:
        return tags[0][:20], tags
    stem = (questions[0].get("stem") or "未命名知识点").strip()
    return stem[:20], tags