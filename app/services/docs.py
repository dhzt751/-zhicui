from pathlib import Path

from ..config import llm_ready
from .llm import vision_ocr


class DocumentError(RuntimeError):
    pass


TEXT_EXT = {".txt", ".md", ".markdown", ".csv"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def extract_text(path: Path, filename: str, progress_cb=None) -> tuple[str, str]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        if progress_cb:
            progress_cb(15, "解析 PDF 文本层")
        return _pdf(path), "pdf"
    if suffix == ".docx":
        if progress_cb:
            progress_cb(15, "解析 Word 文档")
        return _docx(path), "docx"
    if suffix in TEXT_EXT:
        if progress_cb:
            progress_cb(15, "读取文本文件")
        return _plain(path), "text"
    if suffix in IMAGE_EXT:
        if not llm_ready():
            raise DocumentError("图片需要多模态模型 OCR，请先在 .env 配置 LLM_* 后再试。")
        if progress_cb:
            progress_cb(20, "视觉模型识别图片文字")
        mime = "image/png" if suffix == ".png" else "image/jpeg"
        if suffix == ".webp":
            mime = "image/webp"
        if suffix == ".bmp":
            mime = "image/bmp"
        return vision_ocr(path.read_bytes(), mime), "image"
    raise DocumentError(f"暂不支持 {suffix or '该'} 格式，请上传 PDF、DOCX、TXT、MD 或图片。")


def _pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise DocumentError("未安装 pypdf，无法解析 PDF。") from exc
    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise DocumentError(f"PDF 打开失败：{exc}") from exc
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    text = "\n".join(pages).strip()
    if not text:
        raise DocumentError("这个 PDF 没有文本层（可能是扫描件），请导出为图片后上传，或用可复制文字的版本。")
    return text


def _docx(path: Path) -> str:
    try:
        import docx
    except Exception as exc:
        raise DocumentError("未安装 python-docx，无法解析 Word 文档。") from exc
    try:
        document = docx.Document(str(path))
    except Exception as exc:
        raise DocumentError(f"Word 打开失败：{exc}") from exc
    lines = [p.text for p in document.paragraphs if p.text and p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
            if cells:
                lines.append(" | ".join(cells))
    text = "\n".join(lines).strip()
    if not text:
        raise DocumentError("Word 文档里没有可提取的文字。")
    return text


def _plain(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8", "gb18030", "utf-16"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")