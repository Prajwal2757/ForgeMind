"""Turn uploaded files into overlapping text chunks."""
import io
import re
from pathlib import Path

SUPPORTED = (".txt", ".md", ".pdf", ".docx")


def extract_text(filename: str, data: bytes) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED:
        raise ValueError(f"unsupported file type '{ext}'; use one of {', '.join(SUPPORTED)}")
    try:
        if ext == ".pdf":
            from pypdf import PdfReader
            text = "\n\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)
        elif ext == ".docx":
            from docx import Document
            doc = Document(io.BytesIO(data))
            parts = [p.text for p in doc.paragraphs]
            parts += [" | ".join(c.text for c in r.cells) for t in doc.tables for r in t.rows]
            text = "\n\n".join(parts)
        else:
            text = data.decode("utf-8")
    except Exception as exc:
        raise ValueError(f"could not read {filename}: {type(exc).__name__}") from exc
    if not text.strip():
        raise ValueError("no extractable text (scanned PDFs need OCR first)")
    return text


def chunk_text(text: str, size: int = 900, overlap: int = 150):
    """Pack paragraphs into chunks of about `size` chars, carrying an overlap forward."""
    pieces = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        while len(para) > size:
            cut = para.rfind(" ", size // 2, size)
            cut = cut if cut > 0 else size
            pieces.append(para[:cut].strip())
            para = para[cut:].strip()
        if para:
            pieces.append(para)

    chunks, cur = [], ""
    for piece in pieces:
        if cur and len(cur) + len(piece) + 2 > size:
            chunks.append(cur)
            tail = cur[-overlap:] if overlap else ""
            if " " in tail:
                tail = tail[tail.index(" ") + 1:]
            cur = tail
        cur = f"{cur}\n\n{piece}" if cur else piece
    if cur:
        chunks.append(cur)
    return chunks
