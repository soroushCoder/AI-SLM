import os
from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI

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

@app.post("/chat")
def chat(body: dict = Body(...)):
    messages = body.get("messages", [{"role": "user", "content": "Say hello briefly."}])
    # Simple non-streaming call for Step 1 wiring
    resp = client.chat.completions.create(model=MODEL, messages=messages, temperature=0.2)
    return {"content": resp.choices[0].message.content}
