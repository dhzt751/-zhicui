import json
import re
import shutil
import subprocess
from pathlib import Path

import httpx

from ..config import BILIBILI_COOKIE_FILE, MAX_AUDIO_BYTES, PROXY, proxy_map

try:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError
except Exception:  # pragma: no cover
    YoutubeDL = None
    DownloadError = Exception


class VideoError(RuntimeError):
    pass


BILI_RE = re.compile(r"(?:bilibili\.com/video/|b23\.tv/|^BV)([A-Za-z0-9]+)")
YT_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?v=|embed/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{6,})"
)
BVID_RE = re.compile(r"BV[0-9A-Za-z]{10}")
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def detect_platform(url: str) -> tuple[str, str]:
    if "bilibili.com" in url or "b23.tv" in url:
        return "bilibili", ""
    if "youtube.com" in url or "youtu.be" in url:
        match = YT_RE.search(url)
        return "youtube", match.group(1) if match else ""
    return "other", ""


def _base_opts(workdir: Path) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 30,
        "retries": 2,
        "nocheckcertificate": True,
        "outtmpl": str(workdir / "%(id)s.%(ext)s"),
    }
    proxy = proxy_map()
    if proxy:
        opts["proxy"] = proxy["https"]
    if BILIBILI_COOKIE_FILE and Path(BILIBILI_COOKIE_FILE).exists():
        opts["cookiefile"] = BILIBILI_COOKIE_FILE
    return opts


def ensure_available() -> None:
    if YoutubeDL is None:
        raise VideoError("未安装 yt-dlp，请先执行 pip install -r requirements.txt")


def fetch_info(url: str, workdir: Path) -> dict:
    ensure_available()
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        with YoutubeDL(_base_opts(workdir)) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as exc:
        raise VideoError(friendly_error(str(exc))) from exc
    except Exception as exc:
        raise VideoError(friendly_error(str(exc))) from exc
    if info.get("_type") == "playlist" and info.get("entries"):
        info = info["entries"][0]
    if not info:
        raise VideoError("没有解析到视频信息，请检查链接。")
    return info


def friendly_error(message: str) -> str:
    text = message.replace("\x1b", "").strip()
    low = text.lower()
    if "sign in" in low or "login" in low or "登录" in text:
        return "该视频需要登录才能访问，请配置 BILIBILI_COOKIE_FILE 或更换公开视频。"
    if "geo" in low or "not available in your country" in low:
        return "该视频存在地区限制，请配置代理后重试。"
    if "timed out" in low or "ssl" in low or "connection" in low or "network" in low:
        return "网络无法访问该平台，YouTube 需在 .env 配置 HTTPS_PROXY。"
    if "private" in low or "unavailable" in low or "removed" in low:
        return "视频不可用（私享、已删除或受版权限制）。"
    if "ffmpeg" in low:
        return "缺少 ffmpeg，长音频切片需要先安装 ffmpeg。"
    return f"平台解析失败：{text[:300]}"


def pick_subtitle(info: dict) -> tuple[str, dict] | None:
    candidates: dict[str, list] = {}
    for source in ("subtitles", "automatic_captions"):
        for lang, items in (info.get(source) or {}).items():
            if items:
                candidates.setdefault(lang, items)
    if not candidates:
        return None
    priority = ["zh-Hans", "zh-CN", "zh", "zh-Hant", "en", "en-US", "en-GB"]
    ordered = [lang for lang in priority if lang in candidates]
    ordered += [lang for lang in candidates if lang not in ordered]
    for lang in ordered:
        items = candidates[lang]
        vtt = [i for i in items if str(i.get("ext", "")).lower() == "vtt"]
        chosen = vtt[0] if vtt else items[0]
        if chosen.get("url"):
            return lang, chosen
    return None


def download_subtitle(item: dict, workdir: Path) -> tuple[Path, str] | None:
    ext = str(item.get("ext") or "vtt").lower()
    suffix = "vtt" if ext == "vtt" else "json"
    target = workdir / f"subtitle.{suffix}"
    try:
        kwargs = {"timeout": 60, "follow_redirects": True}
        client = httpx.Client(**kwargs, proxy=PROXY) if PROXY else httpx.Client(**kwargs)
        with client:
            resp = client.get(
                item["url"],
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": "https://www.bilibili.com/",
                },
            )
        if resp.status_code >= 400 or not resp.content:
            return None
        target.write_bytes(resp.content)
    except Exception:
        return None
    return target, ("vtt" if suffix == "vtt" else "json")


def parse_subtitle(path: Path, kind: str) -> list[dict]:
    if kind == "vtt":
        return parse_vtt(path)
    return parse_bilibili_json(path)


def parse_bilibili_json(path: Path) -> list[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return []
    body = payload.get("body") if isinstance(payload, dict) else payload
    if not isinstance(body, list):
        return []
    results: list[dict] = []
    for idx, item in enumerate(body):
        text = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()
        if not text:
            continue
        results.append(
            {
                "idx": idx,
                "start_ms": int(float(item.get("from", 0)) * 1000),
                "end_ms": int(float(item.get("to", 0)) * 1000),
                "text": text,
            }
        )
    return results


_TIMESTAMP = re.compile(
    r"(\d{1,2}:)?(\d{2}):(\d{2})[.,](\d{1,3})\s*-->\s*(\d{1,2}:)?(\d{2}):(\d{2})[.,](\d{1,3})"
)


def _to_ms(hours: str | None, minutes: str, seconds: str, millis: str) -> int:
    h = int(hours.rstrip(":")) if hours else 0
    ms = int((millis + "00")[:3])
    return ((h * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + ms


def parse_vtt(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    segments: list[dict] = []
    current: dict | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal current, buffer
        if current and buffer:
            text = " ".join(buffer)
            text = re.sub(r"<[^>]+>", "", text)
            text = text.replace("&nbsp;", " ").replace("&amp;", "&")
            text = re.sub(r"\s+", " ", text).strip()
            if not text:
                pass
            elif re.fullmatch(r"[\[\(（].*[\]\)）]", text):
                pass
            elif segments and text == segments[-1]["text"]:
                pass
            else:
                current["text"] = text
                segments.append(current)
        current = None
        buffer = []

    for line in lines:
        stripped = line.strip()
        match = _TIMESTAMP.search(stripped)
        if match:
            flush()
            groups = match.groups()
            current = {
                "start_ms": _to_ms(*groups[:4]),
                "end_ms": _to_ms(*groups[4:]),
                "text": "",
            }
            continue
        if current is not None and stripped and not stripped.startswith(("WEBVTT", "NOTE", "Kind:", "Language:")):
            buffer.append(stripped)
    flush()
    return [s for s in segments if s["text"] and s["end_ms"] > s["start_ms"]]


def download_audio(url: str, workdir: Path) -> Path:
    opts = _base_opts(workdir)
    opts.update({"format": "bestaudio/best", "outtmpl": str(workdir / "audio.%(ext)s")})
    try:
        with YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as exc:
        raise VideoError(friendly_error(str(exc))) from exc
    files = [p for p in workdir.glob("audio.*") if p.suffix.lower() not in {".part", ".ytdl"}]
    if not files:
        raise VideoError("音频下载失败，平台可能限制了该视频的下载。")
    return max(files, key=lambda p: p.stat().st_size)


def ffmpeg_path() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def split_audio(path: Path, workdir: Path, chunk_seconds: int = 1200) -> list[Path]:
    if path.stat().st_size <= MAX_AUDIO_BYTES:
        return [path]
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise VideoError(
            f"音频体积 {path.stat().st_size // 1024 // 1024}MB 超过转写接口上限，"
            "且系统未安装 ffmpeg，无法切片。请安装 ffmpeg 后重试。"
        )
    normalized = workdir / "audio_normalized.mp3"
    extract = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "48k",
            str(normalized),
        ],
        capture_output=True,
        text=True,
    )
    if extract.returncode != 0:
        raise VideoError(f"音频提取失败：{extract.stderr[:200]}")
    if normalized.stat().st_size <= MAX_AUDIO_BYTES:
        return [normalized]
    pattern = workdir / "part_%03d.mp3"
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(normalized),
        "-f",
        "segment",
        "-segment_time",
        str(chunk_seconds),
        "-c",
        "copy",
        str(pattern),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise VideoError(f"音频切片失败：{result.stderr[:200]}")
    parts = sorted(workdir.glob("part_*.mp3"))
    if not parts:
        raise VideoError("音频切片后没有生成有效片段。")
    return parts


def embed_info(info: dict, platform: str) -> dict:
    video_id = str(info.get("id") or "")
    if platform == "youtube":
        return {"external_id": video_id, "play_url": f"https://www.youtube.com/embed/{video_id}"}
    if platform == "bilibili":
        cid = info.get("cid") or info.get("cid".upper()) or ""
        if not cid:
            cid = _extract_cid(info)
        query = f"bvid={video_id}&cid={cid}&autoplay=0&danmaku=0"
        return {"external_id": video_id, "play_url": f"https://player.bilibili.com/player.html?{query}"}
    return {"external_id": video_id, "play_url": info.get("webpage_url") or ""}


def _extract_cid(info: dict) -> str:
    for fmt in info.get("formats") or []:
        url = str(fmt.get("url") or "")
        match = re.search(r"[?&]cid=(\d+)", url)
        if match:
            return match.group(1)
    return ""


def _bili_client() -> httpx.Client:
    kwargs = {
        "headers": {
            "User-Agent": BROWSER_UA,
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
        },
        "follow_redirects": True,
        "timeout": 30,
    }
    client = httpx.Client(**kwargs, proxy=PROXY) if PROXY else httpx.Client(**kwargs)
    try:
        finger = client.get("https://api.bilibili.com/x/frontend/finger/spi")
        data = finger.json().get("data") or {}
        if data.get("b_3"):
            client.cookies.set("buvid3", data["b_3"], domain=".bilibili.com")
        if data.get("b_4"):
            client.cookies.set("buvid4", data["b_4"], domain=".bilibili.com")
    except Exception:
        try:
            client.get("https://www.bilibili.com/")
        except Exception:
            pass
    return client


def _extract_bvid(url: str, client: httpx.Client) -> str:
    match = BVID_RE.search(url)
    if match:
        return match.group(0)
    try:
        resp = client.get(url)
        match = BVID_RE.search(str(resp.url))
        if match:
            return match.group(0)
    except Exception:
        pass
    return ""


def _page_index(url: str) -> int:
    match = re.search(r"[?&]p=(\d+)", url)
    try:
        return max(1, int(match.group(1))) if match else 1
    except (TypeError, ValueError):
        return 1


def resolve_bilibili(url: str) -> dict:
    client = _bili_client()
    try:
        bvid = _extract_bvid(url, client)
        if not bvid:
            raise VideoError("没有从链接中识别到 BV 号，请检查链接。")
        try:
            view = client.get(
                "https://api.bilibili.com/x/web-interface/view", params={"bvid": bvid}
            ).json()
        except Exception as exc:
            raise VideoError(f"B 站接口请求失败：{exc}") from exc
        if view.get("code") != 0:
            raise VideoError(f"B 站返回错误：{view.get('message') or view.get('code')}")
        data = view["data"]
        pages = data.get("pages") or []
        page = _page_index(url)
        chosen = pages[page - 1] if 0 < page <= len(pages) else None
        cid = chosen.get("cid") if chosen else data.get("cid")
        duration = float((chosen or {}).get("duration") or data.get("duration") or 0)
        info = {
            "id": bvid,
            "title": data.get("title") or "",
            "duration": duration,
            "uploader": (data.get("owner") or {}).get("name") or "",
            "thumbnail": data.get("pic") or "",
            "description": data.get("desc") or "",
            "cid": cid,
            "webpage_url": f"https://www.bilibili.com/video/{bvid}",
            "subtitles": {},
            "automatic_captions": {},
        }
        if not cid:
            return info
        try:
            play = client.get(
                "https://api.bilibili.com/x/player/playurl",
                params={
                    "bvid": bvid,
                    "cid": cid,
                    "fnval": 16,
                    "fnver": 0,
                    "fourk": 1,
                },
            ).json()
        except Exception:
            return info
        dash = ((play.get("data") or {}).get("dash") or {})
        audio = sorted(
            dash.get("audio") or [], key=lambda item: int(item.get("bandwidth") or 0), reverse=True
        )
        urls: list[str] = []
        for item in audio[:3]:
            base = item.get("baseUrl") or item.get("base_url")
            if base:
                urls.append(base)
            for backup in item.get("backupUrl") or item.get("backup_url") or []:
                if backup:
                    urls.append(backup)
        urls = list(dict.fromkeys(urls))
        if urls:
            info["_audio_urls"] = urls
            info["_audio_url"] = urls[0]
        return info
    finally:
        client.close()


def _range_download(url: str, target: Path, headers: dict) -> int:
    timeout = httpx.Timeout(60.0, connect=8.0, read=60.0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        total_size = 0
        try:
            probe = client.get(url, headers={**headers, "Range": "bytes=0-0"})
            content_range = probe.headers.get("content-range") or ""
            if "/" in content_range:
                total_size = int(content_range.split("/")[-1])
        except Exception:
            total_size = 0
        if 0 < total_size <= 40 * 1024 * 1024:
            resp = client.get(url, headers=headers, timeout=60)
            if resp.status_code not in (200, 206):
                raise RuntimeError(f"HTTP {resp.status_code}")
            target.write_bytes(resp.content)
            return len(resp.content)
        total = 0
        chunk = 8 * 1024 * 1024
        with target.open("wb") as handle:
            while True:
                hh = dict(headers)
                hh["Range"] = f"bytes={total}-{total + chunk - 1}"
                with client.stream("GET", url, headers=hh) as resp:
                    if resp.status_code == 416:
                        break
                    if resp.status_code not in (200, 206):
                        raise RuntimeError(f"HTTP {resp.status_code}")
                    got = 0
                    for block in resp.iter_bytes(64 * 1024):
                        handle.write(block)
                        got += len(block)
                    status = resp.status_code
                    content_range = resp.headers.get("content-range") or ""
                total += got
                if got == 0 or status == 200:
                    break
                if content_range and "/" in content_range and total >= int(content_range.split("/")[-1]):
                    break
                if got < chunk:
                    break
    return total


def download_bilibili_audio(info: dict, workdir: Path) -> Path:
    urls = list(info.get("_audio_urls") or [])
    if not urls and info.get("_audio_url"):
        urls = [info["_audio_url"]]
    if not urls:
        raise VideoError(
            "没有取到 B 站音频地址，可能是付费、番剧或地区限制视频，请换一个公开视频。"
        )
    target = workdir / "audio.m4a"
    headers = {
        "User-Agent": BROWSER_UA,
        "Referer": f"https://www.bilibili.com/video/{info.get('id', '')}",
        "Origin": "https://www.bilibili.com",
    }
    errors = []
    for index, url in enumerate(urls[:8], start=1):
        try:
            size = _range_download(url, target, headers)
            if size >= 1024:
                return target
            errors.append(f"#{index} empty")
        except Exception as exc:
            errors.append(f"#{index} {type(exc).__name__}")
            target.unlink(missing_ok=True)
    raise VideoError(
        f"B 站音频下载失败（{'; '.join(errors[:4])}），请改用导入字幕或本地视频。"
    )