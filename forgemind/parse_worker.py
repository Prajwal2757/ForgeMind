"""Document parser, run as a separate resource-limited process.

Usage: python parse_worker.py .pdf  < file  > text
Deliberately imports nothing from forgemind.
"""
import io
import sys
import zipfile

MAX_CHARS = 2_000_000
MAX_UNZIPPED = 100 * 1024 * 1024
MAX_PAGES = 2000


class Rejected(Exception):
    pass


def limit_resources():
    try:
        import resource
    except ImportError:  # Windows: only the parent's timeout applies
        return
    for kind, value in ((resource.RLIMIT_CPU, 45), (resource.RLIMIT_AS, 1536 * 1024 * 1024),
                        (resource.RLIMIT_FSIZE, 0)):
        try:
            resource.setrlimit(kind, (value, value))
        except (ValueError, OSError):
            pass


def parse(ext, data):
    if ext == ".pdf":
        from pypdf import PdfReader
        pages = PdfReader(io.BytesIO(data)).pages
        if len(pages) > MAX_PAGES:
            raise Rejected(f"more than {MAX_PAGES} pages")
        return "\n\n".join(p.extract_text() or "" for p in pages)
    if ext == ".docx":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED:
                raise Rejected("document expands to more than 100 MB")
        from docx import Document
        doc = Document(io.BytesIO(data))
        parts = [p.text for p in doc.paragraphs]
        parts += [" | ".join(c.text for c in r.cells) for t in doc.tables for r in t.rows]
        return "\n\n".join(parts)
    return data.decode("utf-8")


def main():
    limit_resources()
    try:
        text = parse(sys.argv[1], sys.stdin.buffer.read())
        if len(text) > MAX_CHARS:
            raise Rejected(f"more than {MAX_CHARS} characters of text")
    except Rejected as exc:
        sys.stderr.write(str(exc))
        return 2
    except BaseException as exc:
        sys.stderr.write(type(exc).__name__)
        return 2
    sys.stdout.buffer.write(text.encode("utf-8", "replace"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
