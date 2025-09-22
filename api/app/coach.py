# api/app/coach.py
import os, json, re
from typing import Optional, Literal, List, Dict
from fastapi import APIRouter, Body
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .vectorstore import retrieve
from .coffee import (
    CoffeeRequest,
    _espresso_rules, _pourover_rules, _aeropress_rules,
    _french_press_rules, _moka_rules
)

router = APIRouter(prefix="/coach", tags=["coffee-coach"])

# -------- slot schema --------
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

# -------- rules & helpers --------
def _recommend_from_slots(s: CoffeeSlots):
    """Compute rule-based targets from slots (no LLM)."""
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

def _dedupe_sources(docs) -> List[str]:
    """Keep unique filenames from retrieved docs for citation."""
    seen, out = set(), []
    for d in docs:
        src = os.path.basename(d.metadata.get("source",""))
        if src and src not in seen:
            seen.add(src); out.append(src)
    return out

def _advice(slots: CoffeeSlots, rec, docs) -> List[str]:
    """
    Concrete tweaks based on user inputs vs. typical ranges + retrieved docs.
    Always returns a list of short bullet suggestions.
    """
    tips: List[str] = []
    roast = (slots.roast or "medium").lower()
    bev   = (slots.beverage or rec.beverage or "espresso").lower()

    # Roast-based typical brew temperature ranges (°C)
    roast_temp = {
        "light":  (94, 96),
        "medium": (92, 94),
        "dark":   (90, 92),
    }
    lo, hi = roast_temp.get(roast, (92, 94))

    # Temperature advice
    if slots.water_temp_c is not None:
        t = float(slots.water_temp_c)
        if t < lo:
            tips.append(f"Temperature {t:.0f}°C is low for {roast}; try ~{lo}–{hi}°C next time.")
        elif t > hi:
            tips.append(f"Temperature {t:.0f}°C is high for {roast}; try ~{lo}–{hi}°C next time.")
        else:
            tips.append(f"Temperature {t:.0f}°C looks good for {roast}.")
    else:
        tips.append(f"For {roast} roasts, aim around {lo}–{hi}°C.")

    # Beverage-specific guidance
    if bev == "espresso":
        # Pressure advice
        if slots.pressure_bar is not None:
            p = float(slots.pressure_bar)
            if p < 8:
                tips.append(f"~{p:.1f} bar is on the low side—grind a bit finer or extend shot to hit your ratio.")
            elif p > 10:
                tips.append(f"~{p:.1f} bar is high—consider a touch coarser to avoid bitterness.")
            else:
                tips.append(f"~{p:.1f} bar is in the classic range.")
        else:
            tips.append("Aim for ~9 bar at the puck for classic pump espresso.")

        # Ratio/yield suggestion if dose known
        if slots.dose_g:
            try:
                # prefer explicit brew_ratio from rules
                r = rec.targets.get("brew_ratio") or "1:2"
                # try to parse numeric factor from "1:2.2" etc. for calc range
                m = re.search(r"^1:(\d+(\.\d+)?)$", str(r))
                mult = float(m.group(1)) if m else 2.0
                dose = float(slots.dose_g)
                low = round(dose * mult, 0)
                high = round(dose * (mult + 0.5), 0)
                tips.append(f"With {dose:.0f} g in, target ~{r} (≈ {int(low)}–{int(high)} g out).")
            except Exception:
                pass

    # Retrieved sources for traceability
    srcs = _dedupe_sources(docs)
    if srcs:
        tips.append(f"(Sources: {', '.join(srcs[:3])})")
    return tips

def _format_advice_only(advice: List[str]) -> str:
    """Render only the advice bullets as text."""
    if not advice:
        return "Everything looks good based on your inputs."
    return "\n".join(f"- {t}" for t in advice if t.strip())

# -------- request model --------
class CoachTurnRequest(BaseModel):
    messages: List[Dict[str,str]]  # [{role, content}]

# -------- ultra-light slot extraction (no LLM) --------
def _extract_slots_simple(messages: List[Dict[str,str]]) -> CoffeeSlots:
    """
    Heuristic extraction from the latest user messages (fast & deterministic).
    Looks for beverage/machine/roast and numbers for dose(g), temp(C), pressure(bar), yield(g), time(s).
    """
    text = " ".join([m["content"] for m in messages if m.get("role") == "user"]).lower()

    def fnum(pat):
        m = re.search(pat, text)
        return float(m.group(1)) if m else None

    beverage = None
    for b in ["espresso","pourover","aeropress","french_press","moka","americano","cappuccino","latte"]:
        if b in text: beverage = b; break

    machine = None
    if "lever" in text: machine = "espresso_lever"
    elif "pump" in text or "breville" in text or "gaggia" in text: machine = "espresso_pump"
    elif "pod" in text: machine = "pod"
    elif "moka" in text: machine = "moka"
    elif "aeropress" in text: machine = "aeropress"
    elif "french press" in text: machine = "french_press"
    elif "pourover" in text or "pour-over" in text or "v60" in text: machine = "pourover_kettle"

    roast = "light" if "light roast" in text else "dark" if "dark roast" in text else ("medium" if "medium roast" in text else None)

    dose_g = fnum(r"(?:dose|dosing|in)\s*(\d+(?:\.\d+)?)\s*g")
    if dose_g is None:
        dose_g = fnum(r"(\d+(?:\.\d+)?)\s*g(?:ram)?\b")
    water_temp_c = fnum(r"(?:temp|temperature|brew)\s*(\d+(?:\.\d+)?)\s* ?c")
    pressure_bar = fnum(r"(?:pressure|bar)\s*(\d+(?:\.\d+)?)\s*bar")
    yield_g = fnum(r"(?:yield|out)\s*(\d+(?:\.\d+)?)\s*g")
    extraction_time_s = fnum(r"(?:time|shot|extraction)\s*(\d+(?:\.\d+)?)\s*s")

    return CoffeeSlots(
        beverage=beverage, machine=machine, roast=roast,
        dose_g=dose_g, yield_g=yield_g,
        water_temp_c=water_temp_c, pressure_bar=pressure_bar,
        extraction_time_s=extraction_time_s
    )

# =========================
#     /coach/turn (advice only)
# =========================
@router.post("/turn")
def coach_turn(req: CoachTurnRequest):
    messages = req.messages

    # 1) Extract slots (no LLM)
    slots = _extract_slots_simple(messages)

    # 2) Retrieve context (for citations) — small, fast
    q = " ".join([x for x in [slots.beverage, slots.roast] if x]) or "coffee recipe basics"
    docs = retrieve(q, k=2)

    # 3) Compute rule targets
    rec = _recommend_from_slots(slots)

    # 4) Build advice-only text
    tips = _advice(slots, rec, docs)
    reply_text = _format_advice_only(tips)

    # 5) Meta
    sources = _dedupe_sources(docs)
    return {"reply": reply_text, "sources": sources, "targets": rec.targets}

# =========================
#     /coach/stream (advice only)
# =========================
@router.post("/stream")
def coach_stream(req: CoachTurnRequest = Body(...)):
    messages = req.messages

    # 1) Extract slots (no LLM)
    slots = _extract_slots_simple(messages)

    # 2) Retrieve context
    q = " ".join([x for x in [slots.beverage, slots.roast] if x]) or "coffee recipe basics"
    docs = retrieve(q, k=2)

    # 3) Compute rule targets
    rec = _recommend_from_slots(slots)

    # 4) Advice-only text
    tips = _advice(slots, rec, docs)
    text = _format_advice_only(tips)
    sources = _dedupe_sources(docs)

    # 5) Stream only the advice lines + meta
    def sse():
        for line in text.splitlines():
            yield f"data: {line}\n\n"
        meta = {"sources": sources, "targets": rec.targets}
        yield f"event: meta\ndata: {json.dumps(meta)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no","Connection":"keep-alive"}
    )
