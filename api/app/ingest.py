import os
from pathlib import Path
from fastapi import APIRouter
from .vectorstore import upsert_texts

router = APIRouter()

def _read_text_file(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="ignore")

def _read_pdf_file(p: Path) -> str:
    try:
        from pypdf import PdfReader
        r = PdfReader(str(p))
        return "\n".join(page.extract_text() or "" for page in r.pages)
    except Exception:
        return ""

def _chunk(text: str, chunk_size=800, overlap=120):
    if not text:
        return []

    # guard: overlap must be < chunk_size
    overlap = max(0, min(overlap, chunk_size - 1))

    chunks = []
    start = 0
    n = len(text)

    while start < n:
        print("CHUNK start:", start)
        print("     n:", n)
        end = min(start + chunk_size, n)
        chunks.append(text[start:end])

        if end == n:    # we've reached the end; don't compute a new start
            break

        # advance; always make progress
        start = max(end - overlap, start + 1)

    return chunks


@router.post("/ingest")
def ingest(dir_path: str = "/data/company_kb"):
    print(f"INGEST DIR: {dir_path} *************************", flush=True)
    base = Path(dir_path)
    if not base.exists():
        return {"status": "error", "message": f"{dir_path} not found"}

    pairs = []
    for p in base.rglob("*"):
        if p.is_dir(): continue
        ext = p.suffix.lower()
        if ext in [".md", ".txt"]:
            raw = _read_text_file(p)
            print(f"INGEST TXT: {p}", flush=True)
        elif ext == ".pdf":
            raw = _read_pdf_file(p)
            print(f"INGEST PDF: {p}", flush=True)
        else:
            print(f"SKIP (unknown type): {p}", flush=True)
            continue
        for ch in _chunk(raw):
            print(f"  - chunk: {len(ch)} chars", flush=True)
            pairs.append((ch, {"source": str(p)}))
           

    if not pairs:
        return {"status": "ok", "ingested": 0}

    upsert_texts(pairs)
    return {"status": "ok", "ingested": len(pairs)}
