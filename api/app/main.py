import os
from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from .ingest import router as ingest_router
from .vectorstore import retrieve

app = FastAPI(title="SLM Chat API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY", "dummy"),
    base_url=os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1"),
)
MODEL = os.getenv("LLM_MODEL", "phi3:mini")

@app.get("/health")
def health():
    return {"status": "ok"}

app.include_router(ingest_router)

@app.post("/chat")
def chat(body: dict = Body(...)):
    messages = body.get("messages", [{"role": "user", "content": "Hi"}])
    # Take last user message for retrieval
    user_q = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            user_q = m.get("content", "")
            break

    # Retrieve context
    docs = retrieve(user_q, k=4)
    ctx = "\n\n".join([f"[DOC {i+1}] {d.metadata.get('source','')}\n{d.page_content}" for i, d in enumerate(docs)])

    system = (
        "You are a concise customer service agent. "
        "Answer ONLY using the provided company context. "
        "If the answer isn't in the context, say you'll escalate to a human agent."
    )

    augmented = [
        {"role": "system", "content": system},
        {"role": "system", "content": f"Company Context:\n{ctx}"},
        *messages,
    ]

    resp = client.chat.completions.create(model=MODEL, messages=augmented, temperature=0.2)
    return {
        "content": resp.choices[0].message.content,
        "sources": [d.metadata.get("source") for d in docs]
    }
