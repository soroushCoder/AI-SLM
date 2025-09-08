import os
from fastapi import FastAPI, Body,Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from .ingest import router as ingest_router
from .vectorstore import retrieve
from .auth import require_api_key, rate_limit
from .coffee import router as coffee_router
from .coach import router as coach_router


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

app.include_router(ingest_router, dependencies=[Depends(require_api_key), Depends(rate_limit)])
app.include_router(coffee_router)
app.include_router(coach_router)

@app.get("/health")
def health():
    return {"status": "ok"}



@app.post("/chat")
def chat(body: dict = Body(...)):
    print("LLM MODEL:", MODEL)
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



@app.post("/chat/stream")
def chat_stream(body: dict = Body(...)):
    messages = body.get("messages", [{"role": "user", "content": "Hi"}])

    # --- build augmented messages exactly like /chat ---
    user_q = next((m.get("content","") for m in reversed(messages) if m.get("role")=="user"), "")
    # calls your vector store to get the 4 most relevant chunks.
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
    # ---------------------------------------------------

    def sse():
        # NOTE: stream=True returns a generator of ChatCompletionChunk objects
        stream = client.chat.completions.create(
            model=MODEL,
            messages=augmented,
            temperature=0.2,
            stream=True,
            # (optional for Ollama to limit latency)
            # extra_body={"options": {"num_predict": 128}}
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            content = getattr(delta, "content", None)
            if content:
                yield f"data: {content}\n\n"
        yield "data: [DONE]\n\n"

    # These headers help in some proxies/browsers
    # Server-Sent Events (SSE) = a simple way for a server to push a one-way stream of text updates to a browser over a single long-lived HTTP connection.
    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


