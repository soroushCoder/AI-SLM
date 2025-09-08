# api/app/coffee.py
from typing import Literal, Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/coffee", tags=["coffee"])

# ------- Models -------
Beverage = Literal["espresso", "americano", "cappuccino", "latte",
                   "pourover", "aeropress", "french_press", "moka"]

Machine = Literal["espresso_pump", "espresso_lever", "pod", "moka",
                  "pourover_kettle", "aeropress", "french_press"]

Roast = Literal["light", "medium", "dark"]

class CoffeeRequest(BaseModel):
    beverage: Beverage = "espresso"
    machine: Machine = "espresso_pump"
    roast: Roast = "medium"
    # user-known inputs (optional)
    dose_g: Optional[float] = Field(None, ge=5, le=30)            # ground coffee mass
    yield_g: Optional[float] = Field(None, ge=10, le=300)         # beverage mass
    water_temp_c: Optional[float] = Field(None, ge=80, le=100)
    pressure_bar: Optional[float] = Field(None, ge=3, le=12)
    grind_setting: Optional[float] = None                          # arbitrary scale (user grinder)
    extraction_time_s: Optional[float] = Field(None, ge=5, le=600)

class Step(BaseModel):
    title: str
    detail: str

class CoffeeRecommendation(BaseModel):
    beverage: Beverage
    targets: Dict[str, Any]     # doses, temps, pressures, ratios, times
    adjustments: List[str]      # “go finer”, “lower temp”, etc
    steps: List[Step]           # step-by-step guide
    notes: List[str]            # general tips
    warnings: List[str]         # out-of-range flags

# ------- Rule helpers -------
def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def _round(x: float, n: int = 1) -> float:
    return round(x, n)

def _espresso_rules(req: CoffeeRequest) -> CoffeeRecommendation:
    # Defaults
    base_dose = req.dose_g or 18.0
    # Ratio by roast: lighter → longer ratio; darker → shorter
    target_ratio = {"light": 2.4, "medium": 2.0, "dark": 1.8}[req.roast]
    target_yield = req.yield_g or _round(base_dose * target_ratio, 1)

    # Temp by roast (C)
    tmin, tmax = {"light": (94, 96), "medium": (93, 95), "dark": (90, 93)}[req.roast]
    target_temp = req.water_temp_c or (tmin + tmax) / 2

    # Pressure by machine
    if req.machine == "espresso_lever":
        pressure = req.pressure_bar or 7.0  # many levers run 6–8 bar
    elif req.machine == "pod":
        pressure = req.pressure_bar or 9.0  # varies, keep 8–9
    else:
        pressure = req.pressure_bar or 9.0  # pump standard
    pressure = _clamp(pressure, 6.0, 10.0)

    # Time target (double shot often ~25–32s from first drip)
    target_time = 28

    adjustments = []
    warnings = []

    # Validate incoming params and propose moves
    if req.water_temp_c:
        if req.water_temp_c < tmin:
            adjustments.append(f"Raise temperature to ~{int(target_temp)}°C for {req.roast} roast.")
        elif req.water_temp_c > tmax:
            adjustments.append(f"Lower temperature to ~{int(target_temp)}°C to avoid bitterness.")

    if req.pressure_bar:
        if req.pressure_bar < 8 and req.machine == "espresso_pump":
            adjustments.append("Grind a touch finer or increase dose to compensate for low pressure.")
        if req.pressure_bar > 10:
            warnings.append("Pressure >10 bar can channel; consider lowering to ~9 bar.")

    # If time given, infer grind direction
    if req.extraction_time_s:
        if req.extraction_time_s < 22:
            adjustments.append("Shot ran fast → grind finer or increase dose.")
        elif req.extraction_time_s > 34:
            adjustments.append("Shot ran slow → grind coarser or lower dose slightly.")

    steps = [
        Step(title="Dose", detail=f"Grind {_round(base_dose,1)} g into a dry, clean basket."),
        Step(title="Distribute & tamp", detail="Level the bed, light WDT if needed, tamp level with consistent pressure."),
        Step(title="Brew", detail=f"Aim for {_round(target_yield,1)} g out in ~{target_time}s at ~{_round(target_temp)}°C and ~{pressure} bar."),
        Step(title="Taste & adjust", detail="Sour/under → finer grind or higher temp; bitter/over → coarser or lower temp."),
    ]

    notes = [
        "Consider 3–8s pre-infusion; extend a bit for light roasts.",
        "Purge the group to stabilize temperature between shots.",
        "Fresh, evenly ground coffee is critical; burr grinder recommended.",
    ]

    targets = {
        "dose_g": _round(base_dose, 1),
        "yield_g": _round(target_yield, 1),
        "brew_ratio": f"1:{_round(target_ratio,2)}",
        "water_temp_c": int(target_temp),
        "pressure_bar": _round(pressure, 1),
        "time_s": target_time,
        "preinfusion_s": 5,
    }

    return CoffeeRecommendation(
        beverage="espresso",
        targets=targets,
        adjustments=adjustments,
        steps=steps,
        notes=notes,
        warnings=warnings,
    )

def _pourover_rules(req: CoffeeRequest) -> CoffeeRecommendation:
    base_dose = req.dose_g or 20.0
    # Ratio by roast: darker → stronger feel at same ratio; allow tweak
    ratio_map = {"light": 16.5, "medium": 16.0, "dark": 15.0}
    r = ratio_map[req.roast]
    target_water = req.yield_g or _round(base_dose * r, 0)

    # Temp windows (C)
    tmin, tmax = {"light": (94, 96), "medium": (92, 95), "dark": (90, 94)}[req.roast]
    target_temp = req.water_temp_c or (tmin + tmax) / 2

    adjustments = []
    warnings = []

    if req.water_temp_c:
        if req.water_temp_c < tmin:
            adjustments.append(f"Raise water temperature to ~{int(target_temp)}°C for {req.roast} roast.")
        elif req.water_temp_c > tmax:
            adjustments.append(f"Lower water temperature to ~{int(target_temp)}°C to avoid harshness.")

    steps = [
        Step(title="Rinse filter", detail="Rinse paper with hot water; preheat brewer and cup."),
        Step(title="Bloom", detail=f"Pour ~{int(base_dose*2)} g water (≈2× dose), 30–45s bloom."),
        Step(title="Main pours", detail=f"Finish to ~{int(target_water)} g total by 2:30–3:30."),
        Step(title="Swirl & serve", detail="Remove filter, swirl gently, enjoy."),
    ]
    notes = [
        "Grind medium; adjust: sour/weak → finer; bitter/silenced → coarser.",
        "Target a flat bed at the end—avoid channeling by steady pours.",
    ]
    targets = {
        "dose_g": _round(base_dose, 1),
        "water_g": int(target_water),
        "brew_ratio": f"1:{_round(r,1)}",
        "water_temp_c": int(target_temp),
        "time_s": "150–210",
        "bloom_g": int(base_dose*2),
        "bloom_s": "30–45",
    }

    return CoffeeRecommendation(
        beverage="pourover",
        targets=targets,
        adjustments=adjustments,
        steps=steps,
        notes=notes,
        warnings=warnings,
    )

def _aeropress_rules(req: CoffeeRequest) -> CoffeeRecommendation:
    base_dose = req.dose_g or 15.0
    target_water = req.yield_g or 220
    target_temp = req.water_temp_c or {"light":95, "medium":92, "dark":90}[req.roast]
    steps = [
        Step(title="Prep", detail="Use inverted method. Rinse filter, preheat."),
        Step(title="Brew", detail=f"{base_dose} g coffee, pour to {target_water} g at {target_temp}°C, 1:30 steep, gentle swirl."),
        Step(title="Press", detail="Insert cap, flip, 20–30s press. Top up water if needed."),
    ]
    targets = {
        "dose_g": base_dose, "water_g": target_water,
        "water_temp_c": target_temp, "steep_s": 90
    }
    notes = ["Adjust grind: bitter → coarser; sour → finer. Try paper+metal filters for clarity/body."]
    return CoffeeRecommendation(
        beverage="aeropress",
        targets=targets, adjustments=[], steps=steps, notes=notes, warnings=[]
    )

def _french_press_rules(req: CoffeeRequest) -> CoffeeRecommendation:
    base_dose = req.dose_g or 30.0
    ratio = {"light":16.5,"medium":16.0,"dark":15.0}[req.roast]
    water = req.yield_g or int(base_dose*ratio)
    temp = req.water_temp_c or {"light":95,"medium":93,"dark":90}[req.roast]
    steps = [
        Step(title="Brew", detail=f"Coarse grind. {base_dose} g coffee + {water} g water @ {temp}°C."),
        Step(title="Steep", detail="4:00, break crust, skim, then press slowly."),
        Step(title="Serve", detail="Decant immediately to avoid over-extraction."),
    ]
    targets = {"dose_g":base_dose,"water_g":water,"water_temp_c":temp,"steep_s":240,"ratio":f"1:{ratio}"}
    notes = ["If silty, go coarser or use a secondary paper filter."]
    return CoffeeRecommendation(
        beverage="french_press", targets=targets, adjustments=[], steps=steps, notes=notes, warnings=[]
    )

def _moka_rules(req: CoffeeRequest) -> CoffeeRecommendation:
    dose = req.dose_g or 15.0
    water = req.yield_g or 200
    temp = req.water_temp_c or 90
    steps = [
        Step(title="Prep", detail="Fill boiler with hot water to valve; add medium-fine coffee, level (do not tamp)."),
        Step(title="Brew", detail="As it starts to sputter, remove from heat; cool base if it races."),
    ]
    targets = {"dose_g":dose,"water_g":water,"water_temp_c":temp}
    notes = ["Avoid scorching: use preheated water and low heat; stop early for sweetness."]
    return CoffeeRecommendation(
        beverage="moka", targets=targets, adjustments=[], steps=steps, notes=notes, warnings=[]
    )

# ------- Router -------
@router.post("/recommend", response_model=CoffeeRecommendation)
def recommend(req: CoffeeRequest):
    # Simple dispatcher by beverage
    if req.beverage == "espresso":
        return _espresso_rules(req)
    if req.beverage in ("pourover",):
        return _pourover_rules(req)
    if req.beverage == "aeropress":
        return _aeropress_rules(req)
    if req.beverage == "french_press":
        return _french_press_rules(req)
    if req.beverage == "moka":
        return _moka_rules(req)
    # milk drinks use espresso base; user can steam separately
    if req.beverage in ("latte", "cappuccino", "americano"):
        base = _espresso_rules(req)
        base.notes.append("This drink uses an espresso base—adjust milk/water to taste.")
        return base
    raise HTTPException(400, "Unsupported beverage")
