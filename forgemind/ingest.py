"""Turn uploaded files into overlapping text chunks."""
import os
import re
import subprocess
import sys
from pathlib import Path

SUPPORTED = (".txt", ".md", ".pdf", ".docx")
WORKER = Path(__file__).with_name("parse_worker.py")


def extract_text(filename: str, data: bytes, timeout: int = 60) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED:
        raise ValueError(f"unsupported file type '{ext}'; use one of {', '.join(SUPPORTED)}")
    # The child gets no secrets from our environment.
    env = {k: os.environ[k] for k in ("PATH", "HOME", "SYSTEMROOT") if k in os.environ}
    try:
        done = subprocess.run([sys.executable, "-E", "-P", str(WORKER), ext], input=data,
                              capture_output=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise ValueError(f"could not read {filename}: parsing took too long")
    if done.returncode != 0:
        reason = done.stderr.decode("utf-8", "replace").strip()[-200:] or "parser crashed"
        raise ValueError(f"could not read {filename}: {reason}")
    text = done.stdout.decode("utf-8")
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
