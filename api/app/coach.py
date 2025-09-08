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

# -------- helpers --------
def _recommend_from_slots(s: CoffeeSlots):
    # sensible defaults if missing
    bev = (s.beverage or "espresso")
    mach = s.machine or ("espresso_pump" if bev in ("espresso","latte","cappuccino","americano") else
                         "pourover_kettle" if bev=="pourover" else
                         "aeropress" if bev=="aeropress" else
                         "french_press" if bev=="french_press" else
                         "moka")
    roast = s.roast or "medium"
    req = CoffeeRequest(
        beverage=bev, machine=mach, roast=roast,
        dose_g=s.dose_g, yield_g=s.yield_g,
        water_temp_c=s.water_temp_c, pressure_bar=s.pressure_bar,
        grind_setting=s.grind_setting, extraction_time_s=s.extraction_time_s
    )
    if bev=="espresso":      return _espresso_rules(req)
    if bev=="pourover":      return _pourover_rules(req)
    if bev=="aeropress":     return _aeropress_rules(req)
    if bev=="french_press":  return _french_press_rules(req)
    if bev=="moka":          return _moka_rules(req)
    # milk drinks use espresso base
    base = _espresso_rules(req)
    base.notes.append("This drink uses an espresso base—adjust milk/water to taste.")
    return base

def _needed_slots(s: CoffeeSlots) -> List[str]:
    """Return which fields we still need to give a solid rec."""
    need = []
    if not s.beverage: need.append("beverage (espresso / pourover / aeropress / french_press / moka …)")
    if not s.machine: need.append("machine (espresso_pump / lever / pod / pourover_kettle / aeropress / french_press / moka)")
    if not s.roast: need.append("roast (light / medium / dark)")
    # the rest are nice-to-have for precision:
    if s.beverage == "espresso":
        if s.pressure_bar is None: need.append("pump pressure (bar)")
        if s.water_temp_c is None: need.append("brew temperature (°C)")
    else:
        if s.water_temp_c is None: need.append("water temperature (°C)")
    if s.dose_g is None: need.append("dose (g)")
    return need

def _slots_query(s: CoffeeSlots) -> str:
    parts = []
    if s.beverage: parts.append(s.beverage)
    if s.roast: parts.append(f"{s.roast} roast")
    if s.machine and "espresso" in s.machine: parts.append("espresso brew pressure temperature ratio")
    elif s.beverage=="pourover": parts.append("pourover ratio temperature bloom time")
    elif s.beverage in ("aeropress","french_press","moka"): parts.append("recipe ratio temperature time")
    return " ".join(parts) or "espresso recipe ratio temperature pressure"

def _client() -> OpenAI:
    return OpenAI(
        api_key=os.getenv("OPENAI_API_KEY", "dummy"),
        base_url=os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1"),
    )

MODEL = os.getenv("LLM_MODEL", "phi3:mini")

# -------- request models --------
class CoachTurnRequest(BaseModel):
    messages: List[Dict[str,str]]  # [{role, content}]

# -------- slot extraction with LLM (JSON) --------
def _extract_slots(messages: List[Dict[str,str]]) -> CoffeeSlots:
    """
    Ask the model to extract slots as JSON. Works even on small SLMs.
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

# -------- core logic: one turn (non-streaming) --------
@router.post("/turn")
def coach_turn(req: CoachTurnRequest):
    messages = req.messages
    slots = _extract_slots(messages)
    missing = _needed_slots(slots)

    # Retrieve supporting book passages (RAG)
    q = _slots_query(slots)
    docs = retrieve(q, k=3)
    sources = [os.path.basename(d.metadata.get("source","")) for d in docs]
    ctx = "\n\n".join([f"[DOC {i+1}] {d.metadata.get('source','')}\n{d.page_content}" for i,d in enumerate(docs)])

    if missing:
        # Ask only for what's missing, grounded by context
        prompt = (
            "You are a friendly coffee coach. Ask concise, practical questions to gather ONLY these missing details: "
            f"{', '.join(missing)}. Ask in a single short paragraph. "
            "Use everyday language. If relevant, reference the context briefly."
            "\n\nContext from coffee books:\n" + ctx
        )
        client = _client()
        ans = client.chat.completions.create(model=MODEL, temperature=0.2,
            messages=[{"role":"system","content":"Be brief and helpful."},
                      *messages,
                      {"role":"user","content":prompt}]
        ).choices[0].message.content
        return {"reply": ans, "need": missing, "sources": sources}

    # We have enough — compute rule-based targets, wrap with book context
    rec = _recommend_from_slots(slots)
    plan = (
        f"Here’s a dialed-in {rec.beverage} plan based on what you told me.\n\n"
        f"Targets:\n"
        + "\n".join([f"- {k}: {v}" for k,v in rec.targets.items()])
        + "\n\nSteps:\n"
        + "\n".join([f"{i+1}. {s.title}: {s.detail}" for i,s in enumerate(rec.steps)])
    )
    # Ask the model to phrase it nicely and include brief citations
    client = _client()
    final = client.chat.completions.create(
        model=MODEL, temperature=0.2,
        messages=[
            {"role":"system","content":
             "You are a calm, precise coffee coach. Keep answers compact, actionable, and friendly. "
             "If the user gave dose/temp/pressure, reflect them; otherwise use the targets I give you."
            },
            {"role":"user","content":
             f"Use these targets & steps to answer, then add a one-line 'Why' with citations of sources filenames.\n\n"
             f"Targets/Steps:\n{plan}\n\nContext:\n{ctx}\n\nCite like: (Sources: file1.md, file2.pdf)"
            }
        ]
    ).choices[0].message.content

    return {"reply": final, "need": [], "sources": sources, "targets": rec.targets}

# -------- streaming variant --------
@router.post("/stream")
def coach_stream(req: CoachTurnRequest = Body(...)):
    messages = req.messages
    slots = _extract_slots(messages)
    missing = _needed_slots(slots)

    q = _slots_query(slots)
    docs = retrieve(q, k=3)
    sources = [os.path.basename(d.metadata.get("source","")) for d in docs]
    ctx = "\n\n".join([f"[DOC {i+1}] {d.metadata.get('source','')}\n{d.page_content}" for i,d in enumerate(docs)])

    if missing:
        prompt = (
            "Ask concise questions to gather ONLY these missing details: "
            f"{', '.join(missing)}. Single short paragraph. Context may help.\n\n"
            "Context:\n" + ctx
        )
        def sse():
            stream = _client().chat.completions.create(
                model=MODEL, temperature=0.2, stream=True,
                messages=[{"role":"system","content":"Be brief and helpful."},
                          *messages, {"role":"user","content":prompt}]
            )
            for chunk in stream:
                if not chunk.choices: continue
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None)
                if content:
                    yield f"data: {content}\n\n"
            yield f"event: meta\ndata: {json.dumps({'need': missing, 'sources': sources})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(sse(), media_type="text/event-stream",
                                 headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no","Connection":"keep-alive"})

    rec = _recommend_from_slots(slots)
    plan = (
        f"Targets:\n" + "\n".join([f"- {k}: {v}" for k,v in rec.targets.items()]) +
        "\n\nSteps:\n" + "\n".join([f"{i+1}. {s.title}: {s.detail}" for i,s in enumerate(rec.steps)])
    )
    def sse2():
        stream = _client().chat.completions.create(
            model=MODEL, temperature=0.2, stream=True,
            messages=[
                {"role":"system","content":
                 "You are a precise coffee coach. Keep answers compact and actionable. "
                 "End with '(Sources: …)' citing filenames."
                },
                {"role":"user","content": f"Compose a helpful reply using:\n{plan}\n\nContext:\n{ctx}"}
            ]
        )
        for chunk in stream:
            if not chunk.choices: continue
            delta = chunk.choices[0].delta
            content = getattr(delta, "content", None)
            if content:
                yield f"data: {content}\n\n"
        yield f"event: meta\ndata: {json.dumps({'sources': sources, 'targets': rec.targets})}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(sse2(), media_type="text/event-stream",
                             headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no","Connection":"keep-alive"})
