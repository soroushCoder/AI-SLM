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
    if not text: return []
    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        chunks.append(text[start:end])
        start = end - overlap
        if start < 0: start = 0
    return chunks

@router.post("/ingest")
def ingest(dir_path: str = "/data/company_kb"):
    base = Path(dir_path)
    if not base.exists():
        return {"status": "error", "message": f"{dir_path} not found"}

    pairs = []
    for p in base.rglob("*"):
        if p.is_dir(): continue
        ext = p.suffix.lower()
        if ext in [".md", ".txt"]:
            raw = _read_text_file(p)
        elif ext == ".pdf":
            raw = _read_pdf_file(p)
        else:
            continue
        for ch in _chunk(raw):
            pairs.append((ch, {"source": str(p)}))

    if not pairs:
        return {"status": "ok", "ingested": 0}

    upsert_texts(pairs)
    return {"status": "ok", "ingested": len(pairs)}
