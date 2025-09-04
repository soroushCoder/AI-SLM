import hashlib
from typing import List, Tuple
from fastapi import APIRouter
from pathlib import Path
from .vectorstore import upsert_texts
from .celery_app import celery
from celery.result import AsyncResult

router = APIRouter()

def _read_text_file(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="ignore")

def _read_pdf_file(p: Path) -> str:
    from pypdf import PdfReader
    r = PdfReader(str(p))
    return "\n".join((page.extract_text() or "") for page in r.pages)

def _chunk(text: str, chunk_size=800, overlap=120) -> List[str]:
    if not text: return []
    overlap = max(0, min(overlap, chunk_size - 1))
    chunks, start, n = [], 0, len(text)
    while start < n:
        end = min(start + chunk_size, n)
        chunks.append(text[start:end])
        if end == n: break
        start = max(end - overlap, start + 1)
    return chunks

def _ingest_dir(dir_path: str) -> dict:
    base = Path(dir_path)
    if not base.exists():
        return {"status": "error", "message": f"{dir_path} not found"}
    pairs: List[Tuple[str, dict]] = []
    seen = set()  # de-dup within this run
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
            h = hashlib.sha1(ch.encode("utf-8")).hexdigest()
            if h in seen: 
                continue
            seen.add(h)
            pairs.append((ch, {"source": str(p), "digest": h}))
    if not pairs:
        return {"status": "ok", "ingested": 0}
    upsert_texts(pairs)
    return {"status": "ok", "ingested": len(pairs)}
#Celery = Python task queue for background jobs (workers, retries, schedules).
@celery.task(name="app.tasks.ingest_dir_task")
def ingest_dir_task(dir_path: str = "/data/company_kb"):
    return _ingest_dir(dir_path)

# POST /ingest is the endpoint that builds (or refreshes) your search index for RAG. In plain words: it reads your company docs, turns them into vectors, and stores them in Milvus so /chat can retrieve relevant context.
@router.post("/ingest")
def enqueue_ingest(dir_path: str = "/data/company_kb"):
    task = ingest_dir_task.delay(dir_path)
    return {"task_id": task.id, "status": "queued"}

@router.get("/ingest/status/{task_id}")
def ingest_status(task_id: str):
    r = AsyncResult(task_id, app=celery)
    return {"task_id": task_id, "state": r.state, "result": r.result if r.ready() else None}
