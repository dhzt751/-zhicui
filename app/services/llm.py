import base64
import json
import re
from typing import Any

import httpx

from ..config import LLM_API_KEY, LLM_BASE_URL, LLM_EMBEDDING_MODEL, LLM_MODEL


class LLMError(RuntimeError):
    pass


def _headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _request(path: str, payload: dict, key: str, base: str, timeout: float = 180.0) -> dict:
    url = f"{base}{path}"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload, headers=_headers(key))
    except httpx.HTTPError as exc:
        raise LLMError(f"模型服务连接失败：{exc}") from exc
    if resp.status_code >= 400:
        raise LLMError(f"模型服务返回 {resp.status_code}：{resp.text[:400]}")
    try:
        return resp.json()
    except Exception as exc:
        raise LLMError("模型服务返回了非 JSON 内容") from exc


def _chat_once(messages: list[dict], temperature: float, json_mode: bool) -> str:
    payload: dict[str, Any] = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        data = _request("/chat/completions", payload, LLM_API_KEY, LLM_BASE_URL)
    except LLMError:
        if not json_mode:
            raise
        payload.pop("response_format", None)
        data = _request("/chat/completions", payload, LLM_API_KEY, LLM_BASE_URL)
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError) as exc:
        raise LLMError("模型返回结构异常") from exc


def chat(system: str, user: str, temperature: float = 0.2, retries: int = 2) -> str:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    last: Exception | None = None
    for _ in range(retries + 1):
        try:
            return _chat_once(messages, temperature, False)
        except LLMError as exc:
            last = exc
    raise last or LLMError("模型调用失败")


def chat_json(system: str, user: str, temperature: float = 0.1, retries: int = 2) -> Any:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    last: Exception | None = None
    for _ in range(retries + 1):
        try:
            raw = _chat_once(messages, temperature, True)
            return extract_json(raw)
        except LLMError as exc:
            last = exc
    raise last or LLMError("模型调用失败")


def extract_json(raw: str) -> Any:
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "[{":
            try:
                obj, _ = decoder.raw_decode(text[i:])
                return obj
            except Exception:
                continue
    raise LLMError("模型未返回可解析的 JSON")


def embeddings(texts: list[str]) -> list[list[float]] | None:
    if not LLM_EMBEDDING_MODEL or not LLM_BASE_URL or not LLM_API_KEY:
        return None
    vectors: list[list[float]] = []
    for start in range(0, len(texts), 32):
        batch = texts[start : start + 32]
        payload = {"model": LLM_EMBEDDING_MODEL, "input": batch}
        try:
            data = _request("/embeddings", payload, LLM_API_KEY, LLM_BASE_URL, timeout=120)
            items = sorted(data["data"], key=lambda x: x.get("index", 0))
            vectors.extend(item["embedding"] for item in items)
        except (LLMError, KeyError):
            return None
    return vectors if len(vectors) == len(texts) else None


def vision_ocr(image: bytes, mime: str) -> str:
    encoded = base64.b64encode(image).decode()
    payload = {
        "model": LLM_MODEL,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": "你是 OCR 工具。逐行输出图片中的全部文字，保留题号、选项、公式符号，不要任何解释。",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "提取这张图片中的文字。"},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{encoded}"},
                    },
                ],
            },
        ],
    }
    data = _request("/chat/completions", payload, LLM_API_KEY, LLM_BASE_URL)
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError) as exc:
        raise LLMError("视觉模型返回结构异常") from exc