# api/app/coach.py
import os, json
from typing import Optional, Literal, List, Dict
from fastapi import APIRouter, Body
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from openai import OpenAI

from .vectorstore import retrieve
from .coffee import (
    CoffeeRequest,
    _espresso_rules, _pourover_rules, _aeropress_rules,
    _french_press_rules, _moka_rules
)

router = APIRouter(prefix="/coach", tags=["coffee-coach"])

# -------- slot schema (what we track) --------
Beverage = Literal["espresso","americano","cappuccino","latte","pourover","aeropress","french_press","moka"]
Machine  = Literal["espresso_pump","espresso_lever","pod","moka","pourover_kettle","aeropress","french_press"]
Roast    = Literal["light","medium","dark"]

class CoffeeSlots(BaseModel):
    beverage: Optional[Beverage] = None
    machine:  Optional[Machine]  = None
    roast:    Optional[Roast]    = None
    dose_g:   Optional[float]    = None
    yield_g:  Optional[float]    = None
    water_temp_c: Optional[float] = None
    pressure_bar: Optional[float] = None
    grind_setting: Optional[float] = None
    extraction_time_s: Optional[float] = None

# -------- OpenAI-compatible client (Ollama behind OpenAI API) --------
def _client() -> OpenAI:
    return OpenAI(
        api_key=os.getenv("OPENAI_API_KEY", "dummy"),
        base_url=os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1"),
    )

MODEL = os.getenv("LLM_MODEL", "phi3:mini")

# -------- helpers: rules, needed slots, retrieval context --------
def _recommend_from_slots(s: CoffeeSlots):
    bev = (s.beverage or "espresso")
    mach = s.machine or (
        "espresso_pump"   if bev in ("espresso","latte","cappuccino","americano") else
        "pourover_kettle" if bev == "pourover" else
        "aeropress"       if bev == "aeropress" else
        "french_press"    if bev == "french_press" else
        "moka"
    )
    roast = s.roast or "medium"
    req = CoffeeRequest(
        beverage=bev, machine=mach, roast=roast,
        dose_g=s.dose_g, yield_g=s.yield_g,
        water_temp_c=s.water_temp_c, pressure_bar=s.pressure_bar,
        grind_setting=s.grind_setting, extraction_time_s=s.extraction_time_s
    )
    if bev == "espresso":     return _espresso_rules(req)
    if bev == "pourover":     return _pourover_rules(req)
    if bev == "aeropress":    return _aeropress_rules(req)
    if bev == "french_press": return _french_press_rules(req)
    if bev == "moka":         return _moka_rules(req)
    base = _espresso_rules(req)
    base.notes.append("This drink uses an espresso base—adjust milk/water to taste.")
    return base

def _needed_slots(s: CoffeeSlots) -> List[str]:
    need: List[str] = []
    if not s.beverage:
        need.append("beverage (espresso / pourover / aeropress / french_press / moka)")
    if not s.machine:
        if s.beverage in ("pourover","french_press","aeropress","moka"):
            need.append("machine (pourover_kettle / french_press / aeropress / moka)")
        else:
            need.append("machine (espresso_pump / espresso_lever / pod)")
    if not s.roast:
        need.append("roast (light / medium / dark)")
    # precision fields by beverage
    if s.beverage == "espresso":
        if s.dose_g is None:        need.append("dose (g)")
        if s.water_temp_c is None:  need.append("brew temperature (°C)")
        if s.pressure_bar is None:  need.append("pump pressure (bar)")
    elif s.beverage == "pourover":
        if s.dose_g is None:        need.append("dose (g)")
        if s.water_temp_c is None:  need.append("water temperature (°C)")
    else:
        if s.dose_g is None:        need.append("dose (g)")
        if s.water_temp_c is None:  need.append("water temperature (°C)")
    return need

def _slots_query(s: CoffeeSlots) -> str:
    parts: List[str] = []
    if s.beverage: parts.append(s.beverage)
    if s.roast:    parts.append(f"{s.roast} roast")
    if s.machine and "espresso" in s.machine:
        parts.append("espresso brew pressure temperature ratio")
    elif s.beverage == "pourover":
        parts.append("pourover ratio temperature bloom time")
    elif s.beverage in ("aeropress","french_press","moka"):
        parts.append("recipe ratio temperature time")
    return " ".join(parts) or "espresso recipe ratio temperature pressure"

def _ctx(docs, max_chars=1200) -> str:
    """Compact context to keep tokenization fast."""
    out, n = [], 0
    for i, d in enumerate(docs, 1):
        chunk = f"[DOC {i}] {d.metadata.get('source','')}\n{d.page_content}\n\n"
        if n + len(chunk) > max_chars:
            break
        out.append(chunk); n += len(chunk)
    return "".join(out)

def _format_recipe(rec, sources: List[str]) -> str:
    """Deterministic recipe text from the rule engine (no LLM)."""
    lines: List[str] = []
    lines.append(f"Dial-in {rec.beverage} — practical recipe")
    lines.append("")
    if rec.targets:
        lines.append("Targets")
        for k, v in rec.targets.items():
            lines.append(f"- {k}: {v}")
    if getattr(rec, "adjustments", None):
        lines.append("")
        lines.append("Adjust if needed")
        for adj in rec.adjustments:
            lines.append(f"- {adj}")
    if getattr(rec, "steps", None):
        lines.append("")
        lines.append("Steps")
        for i, step in enumerate(rec.steps, 1):
            lines.append(f"{i}. {step.title}: {step.detail}")
    if getattr(rec, "notes", None):
        lines.append("")
        lines.append("Notes")
        for n in rec.notes:
            lines.append(f"- {n}")
    if sources:
        lines.append("")
        lines.append(f"(Sources: {', '.join(sources)})")
    return "\n".join(lines)

# -------- request model --------
class CoachTurnRequest(BaseModel):
    messages: List[Dict[str,str]]  # [{role, content}]

# -------- slot extraction with LLM (JSON) --------
def _extract_slots(messages: List[Dict[str,str]]) -> CoffeeSlots:
    """
    Ask the model to extract slots as JSON.
    Falls back to empty slots on parse errors.
    """
    sys = (
        "Extract coffee parameters from the conversation. "
        "Return ONLY compact JSON with keys: beverage, machine, roast, dose_g, yield_g, "
        "water_temp_c, pressure_bar, grind_setting, extraction_time_s. "
        "Values can be null if unknown. Do not include any text outside JSON."
    )
    client = _client()
    try:
        resp = client.chat.completions.create(
            model=MODEL, temperature=0,
            messages=[{"role":"system","content":sys}, *messages]
        )
        raw = resp.choices[0].message.content.strip()
        data = json.loads(raw) if raw.startswith("{") else {}
        return CoffeeSlots(**data)
    except Exception:
        return CoffeeSlots()

# =========================
#     /coach/turn
# =========================
@router.post("/turn")
def coach_turn(req: CoachTurnRequest):
    messages = req.messages
    slots = _extract_slots(messages)
    missing = _needed_slots(slots)

    # RAG
    q = _slots_query(slots)
    docs = retrieve(q, k=2)
    sources = [os.path.basename(d.metadata.get("source","")) for d in docs]
    ctx = _ctx(docs)

    # Ask for missing info (LLM, strict)
    if missing:
        bev = slots.beverage or "unknown"
        need_list = ", ".join(missing)
        prompt = (
            f"You are a coffee coach. Beverage: {bev}.\n"
            f"Ask ONLY for these missing fields: {need_list}.\n"
            "Do not ask about anything else. "
            "If beverage is not 'espresso', NEVER ask about shots, pumps, levers, or pressure. "
            "Write one short question (<= 25 words), no bullets, no preamble."
            "\n\nContext from coffee books (optional):\n" + ctx
        )
        ans = _client().chat.completions.create(
            model=MODEL, temperature=0.1,
            messages=[{"role":"system","content":"Be brief and helpful."},
                      *messages, {"role":"user","content":prompt}]
        ).choices[0].message.content
        return {"reply": ans, "need": missing, "sources": sources}

    # We have enough — compute rule-based targets and return deterministic text
    rec = _recommend_from_slots(slots)
    det_text = _format_recipe(rec, sources)
    return {"reply": det_text, "need": [], "sources": sources, "targets": rec.targets}

# =========================
#     /coach/stream
# =========================
@router.post("/stream")
def coach_stream(req: CoachTurnRequest = Body(...)):
    messages = req.messages
    slots = _extract_slots(messages)
    missing = _needed_slots(slots)

    # RAG
    q = _slots_query(slots)
    docs = retrieve(q, k=2)
    sources = [os.path.basename(d.metadata.get("source","")) for d in docs]
    ctx = _ctx(docs)

    # If missing → stream a strict, short question from the LLM
    if missing:
        bev = slots.beverage or "unknown"
        need_list = ", ".join(missing)
        prompt = (
            f"You are a coffee coach. Beverage: {bev}.\n"
            f"Ask ONLY for these missing fields: {need_list}.\n"
            "Do not ask about anything else. "
            "If beverage is not 'espresso', NEVER ask about shots, pumps, levers, or pressure. "
            "Write one short question (<= 25 words), no bullets, no preamble."
            "\n\nContext from coffee books (optional):\n" + ctx
        )
        def sse_q():
            stream = _client().chat.completions.create(
                model=MODEL, temperature=0.1, stream=True,
                messages=[{"role":"system","content":"Be brief and helpful."},
                          *messages, {"role":"user","content":prompt}]
            )
            for chunk in stream:
                if not chunk.choices: 
                    continue
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None)
                if content:
                    yield f"data: {content}\n\n"
            yield f"event: meta\ndata: {json.dumps({'need': missing, 'sources': sources})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(
            sse_q(),
            media_type="text/event-stream",
            headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no","Connection":"keep-alive"}
        )

    # Not missing → deterministic streaming of the recipe (fast, structured)
    rec = _recommend_from_slots(slots)
    det_text = _format_recipe(rec, sources)

    def sse_recipe():
        for line in det_text.splitlines():
            yield f"data: {line}\n\n"
        yield f"event: meta\ndata: {json.dumps({'sources': sources, 'targets': rec.targets})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        sse_recipe(),
        media_type="text/event-stream",
        headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no","Connection":"keep-alive"}
    )
