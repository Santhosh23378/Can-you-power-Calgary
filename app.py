"""
CAN YOU POWER CALGARY?  —  Energy Grid Simulator
=================================================
An interactive electrical-grid management simulation.

You are the Chief Energy Analyst for Calgary. Keep the lights on while balancing
supply, demand, cost, reliability, emissions and -- above all -- STORAGE.

Central learning outcome:
    "The hard problem isn't finding energy. It's storing and delivering it
     when people need it."

Run it:
    pip install streamlit pandas altair
    streamlit run app.py

(Optional but recommended: put config.toml inside a .streamlit/ folder next to
this file so the whole app uses the dark theme.)
"""

import datetime as dt
import math
import random
import time

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(
    page_title="Can You Power Calgary?",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# =============================================================================
# 1. MODEL CONSTANTS
# =============================================================================
LAT = 51.05                      # Calgary latitude (solar geometry)
GAS_CO2, IMPORT_CO2 = 0.45, 0.50  # tonnes CO2 per MWh (gas fleet / neighbouring grids)
CARBON_PRICE = 80.0              # $/tonne
GAS_FUEL = 38.0                  # $/MWh fuel cost
RETAIL = 90.0                    # $/MWh revenue from delivered electricity
SHORTAGE_PENALTY = 2000.0        # $/MWh of unserved energy (regulator fines + economic damage)
ETA = 0.94                       # one-way battery efficiency (0.94 x 0.94 = 88% round trip)
POP0 = 1.42                      # population in millions
GRANT = 60.0                     # $M city capital grant every 30 days

# Share of the day's average load for each hour (morning ramp + evening peak)
PROFILE = [0.84, 0.80, 0.78, 0.77, 0.79, 0.86, 0.95, 1.05, 1.10, 1.09, 1.08, 1.07,
           1.06, 1.05, 1.05, 1.07, 1.11, 1.16, 1.17, 1.14, 1.08, 1.00, 0.92, 0.87]

# Fixed operating & maintenance cost, $ per MW-year
OM = dict(gas=18000, hydro=30000, wind=38000, solar=18000, nuclear=120000, battery_mw=12000)

START_OPTIONS = {
    "🍂 Autumn (October) — recommended start": 288,
    "🌱 Spring (April) — mild and windy": 105,
    "☀️ Summer (July) — solar surplus, AC peaks": 196,
    "❄️ Winter (January) — HARD: peak heating season": 15,
}

LEVELS = {
    1: dict(title="Junior Grid Operator", badge="🟢", demand=1.00, events=0.18, streak=5, clean=None, ci=None,
            blurb="Learn the controls. Keep the lights on."),
    2: dict(title="Senior Grid Analyst", badge="🟡", demand=1.06, events=0.28, streak=8, clean=0.30, ci=None,
            blurb="Demand is growing and the weather is nastier. Nuclear is now approved."),
    3: dict(title="Head of Calgary Energy Planning", badge="🔴", demand=1.14, events=0.40, streak=10, clean=0.45, ci=260,
            blurb="Run a reliable AND clean grid under extreme conditions."),
}

BUILDS = {
    "solar": dict(label="Solar Farm", icon="☀️", cost=60, days=3, unit="+50 MW", unlock=1,
                  desc="Cheap and clean, but only when the sun is up. Zero at night, weak in winter, crushed by clouds."),
    "wind": dict(label="Wind Farm", icon="🌬️", cost=75, days=5, unit="+50 MW", unlock=1,
                 desc="Alberta is windy, but wind is unpredictable. A wind drought can idle it for days."),
    "battery": dict(label="Battery Storage", icon="🔋", cost=55, days=3, unit="+25 MW / +100 MWh", unlock=1,
                    desc="Saves surplus clean power for later. 100 MWh is about 4 hours at full power. Only helps if it gets charged."),
    "nuclear": dict(label="Nuclear (SMR)", icon="⚛️", cost=240, days=14, unit="+150 MW", unlock=2,
                    desc="Steady, carbon-free power 24/7. Huge cost and a long build, but it ignores the weather."),
    "intertie": dict(label="Intertie Upgrade", icon="🔌", cost=45, days=4, unit="+50 MW import limit", unlock=1,
                     desc="A bigger power line to neighbouring regions."),
}

DR = {
    "dr": dict(label="Demand Response", icon="🏭", pct=0.08, hours=4, cooldown=12, cost=1.5, sat=0.35,
               note="Industry + smart thermostats cut ~8% of demand for 4 hours. Costs money and annoys some customers."),
    "appeal": dict(label="Conservation Appeal", icon="📣", pct=0.03, hours=6, cooldown=24, cost=0.0, sat=0.05,
                   note="A public appeal to use less power: ~3% for 6 hours. Free, but people tire of it."),
}

EVENTS = {
    "cloud": dict(name="Cloudy Day", icon="☁️", dur=(18, 36), w=1.2,
                  headline="Heavy cloud cover is cutting solar output by 70% across southern Alberta.",
                  effect="Solar output −70%"),
    "wind_drought": dict(name="Wind Drought", icon="🌬️", dur=(24, 72), w=1.0,
                         headline="The wind has died down across the region. Wind farms are nearly idle.",
                         effect="Wind output −88%"),
    "heat": dict(name="Heat Wave", icon="🔥", dur=(48, 96), w=1.0,
                 headline="A heat dome has settled over Calgary. Air-conditioning demand is surging.",
                 effect="Temp +12°C · AC demand spikes · gas plants derate 6% · imports −25%"),
    "storm": dict(name="Winter Storm", icon="❄️", dur=(36, 72), w=1.2,
                  headline="A major winter storm is driving heating demand to record levels.",
                  effect="Temp −14°C · heating demand spikes · gas supply −8% · solar −40% · wind −20%"),
    "transmission": dict(name="Transmission Failure", icon="⚡", dur=(10, 30), w=0.8,
                         headline="A major transmission line has failed. Power imports are cut off.",
                         effect="Imports unavailable"),
    "equipment": dict(name="Equipment Failure", icon="🔧", dur=(24, 72), w=0.9,
                      headline="A large generating unit has tripped offline.",
                      effect="Large generator offline"),
    "chinook": dict(name="Chinook Winds", icon="🌤️", dur=(24, 48), w=0.5,
                    headline="Warm Chinook winds are sweeping in: milder air and strong wind output.",
                    effect="Temp +11°C (less heating) · wind +40%"),
}

SPEEDS = {
    "🐢 Slow": (1, 3.0),      # 1 game hour every 3 real seconds (a day takes ~72 s)
    "🚶 Normal": (1, 1.5),
    "🐇 Fast": (1, 0.7),
}

SOURCES = [("nuclear", "Nuclear", "#a78bfa"), ("hydro", "Hydro", "#38bdf8"), ("wind", "Wind", "#34d399"),
           ("solar", "Solar", "#fbbf24"), ("battery", "Battery", "#f472b6"),
           ("gas", "Natural gas", "#94a3b8"), ("imports", "Imports", "#fb923c")]


# =============================================================================
# 2. SMALL HELPERS
# =============================================================================
def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def lerp(a, b, t):
    return a + (b - a) * t


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(c1, c2, t):
    t = clamp(t, 0.0, 1.0)
    a, b = _rgb(c1), _rgb(c2)
    return "#%02x%02x%02x" % tuple(int(lerp(a[i], b[i], t)) for i in range(3))


def H(s):
    """Collapse an HTML snippet to a single line (keeps Markdown from mangling it)."""
    return " ".join(line.strip() for line in s.splitlines() if line.strip())


def md_safe(text):
    return text.replace("$", "\\$")


def html_safe(text):
    return text.replace("$", "&#36;")


def fmt_hour(h):
    return f"{(h % 12) or 12}:00 {'AM' if h < 12 else 'PM'}"


def clock(S, t):
    total = S["start_hour"] + t
    day, hour = divmod(total, 24)
    return day, hour, S["start_date"] + dt.timedelta(days=day)


def seasonal_temp(doy):
    """Average daily temperature (°C) for Calgary by day of year (−7.5 in Jan to +16.5 in July)."""
    return 4.5 + 12.0 * math.sin(2 * math.pi * (doy - 110) / 365.0)


def news(S, text, kind="info"):
    day, hour, _ = clock(S, S["t"])
    S["news"].append(dict(label=f"Day {day + 1} · {fmt_hour(hour)}", text=text, kind=kind))
    del S["news"][:-80]


def btn(label, key, **kw):
    try:
        return st.button(label, key=key, width="stretch", **kw)
    except TypeError:
        return st.button(label, key=key, use_container_width=True, **kw)


def show_chart(ch):
    try:
        st.altair_chart(ch, width="stretch", theme=None)
    except TypeError:
        st.altair_chart(ch, use_container_width=True, theme=None)


# =============================================================================
# 3. GAME STATE
# =============================================================================
def new_game(season_label, mode):
    doy = START_OPTIONS[season_label]
    career = mode.startswith("Career")
    level = 1 if career else 2
    S = dict(
        mode="career" if career else "sandbox", season=season_label, rng=random.Random(),
        start_date=dt.date(2027, 1, 1) + dt.timedelta(days=doy - 1), start_hour=6, t=0,
        level=level, demand_mult=1.0, demand_target=LEVELS[level]["demand"],
        cash=220.0 if career else 1500.0, month_net=0.0, last_month_net=0.0,
        pop=POP0, satisfaction=75.0, stability=95.0,
        cap=dict(gas=820.0, hydro=100.0, wind=120.0, solar=60.0, nuclear=0.0, battery_mw=25.0, battery_mwh=50.0),
        soc=25.0, import_limit=150, import_max=250,
        batt_strategy="peak", grid_charge=False, rand_events=True, autopause=True,
        queue=[], events=[], news=[], hist=[], daily=[],
        wind_v=7.0, clear=0.9, anomaly=0.0,
        dr_until={"dr": -1, "appeal": -1}, dr_ready={"dr": 0, "appeal": 0},
        streak=0, running=False, over=None, won=False, balloons=False, alert=None, promo=None,
        in_blackout=False, hosp_emerg=False, blackouts=0, blackout_hours=0, unserved_total=0.0,
        co2_total=0.0, last=None,
    )
    news(S, f"🏙️ You are now {LEVELS[level]['title']} for the City of Calgary. Keep the lights on!", "info")
    simulate_hour(S, allow_events=False)
    return S


def effects(S):
    e = dict(solar=1.0, wind=1.0, temp=0.0, gas_derate=0.0, gas_loss=0.0, nuc_loss=0.0, imp=1.0)
    for ev in S["events"]:
        k, sev = ev["key"], ev["sev"]
        if k == "cloud":
            e["solar"] *= 0.30
        elif k == "wind_drought":
            e["wind"] *= 0.12
        elif k == "heat":
            e["temp"] += 12.0 * sev
            e["gas_derate"] += 0.06
            e["imp"] *= 0.75
        elif k == "storm":
            e["temp"] -= 14.0 * sev
            e["solar"] *= 0.6
            e["wind"] *= 0.8
            e["gas_derate"] += 0.08
        elif k == "transmission":
            e["imp"] = 0.0
        elif k == "equipment":
            if ev["asset"] == "nuclear":
                e["nuc_loss"] += ev["loss"]
            else:
                e["gas_loss"] += ev["loss"]
        elif k == "chinook":
            e["temp"] += 11.0
            e["wind"] *= 1.4
    return e


def event_allowed(key, doy):
    m = seasonal_temp(doy)
    if key == "heat":
        return m >= 8
    if key == "storm":
        return m <= 6
    if key == "chinook":
        return m <= 4
    return True


def start_event(S, key, forced=False):
    if any(e["key"] == key for e in S["events"]):
        return False
    spec, rng = EVENTS[key], S["rng"]
    dur = rng.randint(*spec["dur"])
    ev = dict(key=key, start=S["t"], end=S["t"] + dur, sev=1.0 + 0.2 * (S["level"] - 1), asset=None, loss=0.0)
    head = spec["headline"]
    if key == "equipment":
        if S["cap"]["nuclear"] > 0 and rng.random() < 0.5:
            ev["asset"], ev["loss"] = "nuclear", min(S["cap"]["nuclear"], 150.0)
        else:
            ev["asset"], ev["loss"] = "gas", float(rng.choice([120, 150, 180]))
        head = f"Unit trip: a {ev['loss']:.0f} MW {'nuclear' if ev['asset'] == 'nuclear' else 'natural-gas'} generator has gone offline."
    ev["headline"] = head
    S["events"].append(ev)
    news(S, f"{spec['icon']} {head}", "good" if key == "chinook" else "bad")
    if not forced:
        S["alert"] = ev
    return True


def shed_districts(D, unserved, hour):
    w = dict(homes=0.37 * (1 + 0.30 * math.cos(2 * math.pi * (hour - 19) / 24)),
             downtown=0.29 * (1 + 0.35 * math.cos(2 * math.pi * (hour - 13) / 24)),
             industry=0.26 * (1 + 0.10 * math.cos(2 * math.pi * (hour - 13) / 24)),
             hospital=0.05, school=0.03 * (1.0 if 7 <= hour <= 16 else 0.4))
    tot = sum(w.values())
    load = {k: D * v / tot for k, v in w.items()}
    frac = {k: 1.0 for k in w}
    rem = unserved
    for k in ("industry", "homes", "downtown", "school", "hospital"):  # shed order: least critical first
        if rem <= 0:
            break
        cut = min(load[k], rem)
        frac[k] = 1 - cut / load[k]
        rem -= cut
    return frac


def dispatch(S, D, a):
    """Merit-order dispatch for one hour. Returns MW from each source."""
    cap = S["cap"]
    Pb, Eb, soc = cap["battery_mw"], cap["battery_mwh"], S["soc"]
    o = dict(nuclear=a["nuclear"], hydro=a["hydro"], wind=a["wind"], solar=a["solar"], battery=0.0, gas=0.0,
             imports=0.0, charge=0.0, curtailed=0.0, unserved=0.0, grid_charge=0.0)
    R = a["nuclear"] + a["hydro"] + a["wind"] + a["solar"]
    if R >= D:  # clean power surplus: charge batteries, curtail the rest
        surplus = R - D
        ch = min(surplus, Pb, max(0.0, (Eb - soc) / ETA))
        soc += ch * ETA
        curt = surplus - ch
        left = curt
        for k in ("solar", "wind", "hydro", "nuclear"):
            c = min(o[k], left)
            o[k] -= c
            left -= c
        o["charge"], o["curtailed"] = ch, curt
    else:
        gap = D - R
        gas_av = a["gas"]
        deliverable = min(Pb, soc * ETA)
        if S["batt_strategy"] == "greedy":          # use storage as much as possible
            b = min(gap, deliverable); gap -= b
            g = min(gap, gas_av); gap -= g
        else:                                       # peak shaving: keep charge for the crunch
            g1 = min(gap, 0.85 * gas_av); gap -= g1
            b = min(gap, deliverable); gap -= b
            g2 = min(gap, gas_av - g1); gap -= g2
            g = g1 + g2
        soc -= b / ETA
        im = min(gap, a["import"]); gap -= im
        o.update(battery=b, gas=g, imports=im, unserved=max(0.0, gap))
        if S["grid_charge"] and o["unserved"] <= 0 and b <= 0.01 and im <= 0.01 and soc < Eb - 0.5:
            head = gas_av - g - 0.15 * D
            if head > 0:
                ch = min(head, Pb, (Eb - soc) / ETA)
                soc += ch * ETA
                o["gas"] += ch
                o["charge"] = o["grid_charge"] = ch
    S["soc"] = clamp(soc, 0.0, Eb)
    return o


def simulate_hour(S, allow_events=True):
    rng, t, cap = S["rng"], S["t"], S["cap"]
    day, hour, date = clock(S, t)
    doy = date.timetuple().tm_yday

    # ---- day / month housekeeping ------------------------------------------
    if hour == 0 and t > 0:
        finalize_day(S, day - 1)
        if day % 30 == 0:
            S["cash"] += GRANT
            S["last_month_net"], S["month_net"] = S["month_net"], 0.0
            news(S, f"🏛️ New month: council capital grant of ${GRANT:.0f}M received.", "good")
    if hour == 0 or t == 0:
        S["anomaly"] = 0.65 * S["anomaly"] + rng.gauss(0, 3.2)
        S["clear"] = clamp(0.35 + rng.betavariate(5, 2) * 0.8, 0.5, 1.0)
    S["demand_mult"] += (S["demand_target"] - S["demand_mult"]) * 0.01

    # ---- events ends, construction, random events ---------------------------
    keep = []
    for ev in S["events"]:
        if ev["end"] <= t:
            news(S, f"✅ {EVENTS[ev['key']]['name']} has ended.", "good")
        else:
            keep.append(ev)
    S["events"] = keep
    q_keep = []
    for item in S["queue"]:
        if item["eta"] <= t:
            k = item["kind"]
            if k == "solar":
                cap["solar"] += 50
            elif k == "wind":
                cap["wind"] += 50
            elif k == "battery":
                cap["battery_mw"] += 25
                cap["battery_mwh"] += 100
            elif k == "nuclear":
                cap["nuclear"] += 150
            elif k == "intertie":
                S["import_max"] += 50
            news(S, f"🏗️ Construction complete: {BUILDS[k]['label']} ({BUILDS[k]['unit']}) is online!", "good")
        else:
            q_keep.append(item)
    S["queue"] = q_keep
    if allow_events and S["rand_events"] and len(S["events"]) < 2:
        if rng.random() < LEVELS[S["level"]]["events"] / 24.0:
            keys = [k for k in EVENTS if event_allowed(k, doy) and not any(e["key"] == k for e in S["events"])]
            if keys:
                start_event(S, rng.choices(keys, [EVENTS[k]["w"] for k in keys])[0])
    fx = effects(S)

    # ---- weather & renewable resources --------------------------------------
    vm = 7.2 + 1.0 * math.cos(2 * math.pi * (doy - 15) / 365.0)
    S["wind_v"] = max(0.0, vm + 0.96 * (S["wind_v"] - vm) + rng.gauss(0, 0.8))
    v = S["wind_v"]
    cf_w = 0.0 if (v < 2.5 or v > 25) else clamp(((v - 2.5) / 7.5) ** 2, 0, 1)
    wind_out = cap["wind"] * cf_w * fx["wind"]

    dec = math.radians(23.44 * math.sin(2 * math.pi * (doy - 81) / 365.0))
    lat = math.radians(LAT)
    ha = math.radians(15 * (hour + 0.5 - 12))
    sin_el = math.sin(lat) * math.sin(dec) + math.cos(lat) * math.cos(dec) * math.cos(ha)
    sun_el = math.degrees(math.asin(clamp(sin_el, -1, 1)))
    cf_s = (clamp(sin_el, 0, 1) ** 0.9) * 0.88 * S["clear"]
    solar_out = cap["solar"] * cf_s * fx["solar"]

    hydro_out = cap["hydro"] * (0.45 + 0.25 * math.sin(2 * math.pi * (doy - 100) / 365.0))
    nuc_out = max(0.0, cap["nuclear"] * 0.95 - fx["nuc_loss"])
    gas_av = max(0.0, cap["gas"] * (1 - fx["gas_derate"]) - fx["gas_loss"])
    imp_cap = min(S["import_limit"], S["import_max"]) * fx["imp"]

    # ---- demand --------------------------------------------------------------
    T = seasonal_temp(doy) + 4.5 * math.sin(2 * math.pi * (hour - 9) / 24) + S["anomaly"] + fx["temp"]
    popf = S["pop"] / POP0 * S["demand_mult"]
    weather_load = max(0.0, 15 - T) * 5.5 + max(0.0, T - 21) * 8.0    # heating + cooling (MW)
    raw = (640.0 + weather_load) * popf * PROFILE[hour] * (1 + rng.gauss(0, 0.01))
    dr_pct = sum(DR[k]["pct"] for k in DR if S["dr_until"][k] > t)
    dr_sat = sum(DR[k]["sat"] for k in DR if S["dr_until"][k] > t)
    D = raw * (1 - dr_pct)

    # ---- dispatch ------------------------------------------------------------
    soc0 = S["soc"]
    avail = dict(nuclear=nuc_out, hydro=hydro_out, wind=wind_out, solar=solar_out, gas=gas_av, **{"import": imp_cap})
    avail_cap = nuc_out + hydro_out + wind_out + solar_out + min(cap["battery_mw"], soc0 * ETA) + gas_av + imp_cap
    reserve = (avail_cap - D) / D
    o = dispatch(S, D, avail)
    served = D - o["unserved"]
    in_bo = o["unserved"] > 0.5

    # ---- prices, costs, emissions -------------------------------------------
    clean_gen = o["nuclear"] + o["hydro"] + o["wind"] + o["solar"]
    clean = min(served, clean_gen + o["battery"])
    co2 = o["gas"] * GAS_CO2 + o["imports"] * IMPORT_CO2
    ci = co2 * 1000.0 / max(served, 1.0)
    if in_bo:
        price = 999.0
    elif o["curtailed"] > 0.5:
        price = 3.0
    else:
        resid = max(0.0, D - clean_gen)
        util = resid / gas_av if gas_av > 1 else 1.5
        price = 30 + 95 * min(util, 1.0) ** 2 + (45 if o["imports"] > 0.5 else 0)
    cost_fuel = o["gas"] * (GAS_FUEL + GAS_CO2 * CARBON_PRICE) + o["nuclear"] * 10.0
    cost_imp = o["imports"] * (min(price, 250.0) + 20.0)
    cost_om = (cap["gas"] * OM["gas"] + cap["hydro"] * OM["hydro"] + cap["wind"] * OM["wind"] + cap["solar"] * OM["solar"]
               + cap["nuclear"] * OM["nuclear"] + cap["battery_mw"] * OM["battery_mw"]) / 8760.0
    cost_pen = o["unserved"] * SHORTAGE_PENALTY
    revenue = served * RETAIL
    net = revenue - cost_fuel - cost_imp - cost_om - cost_pen
    S["cash"] += net / 1e6
    S["month_net"] += net / 1e6
    S["co2_total"] += co2

    # ---- districts, stability, satisfaction ----------------------------------
    fr = shed_districts(D, o["unserved"], hour)
    if in_bo and not S["in_blackout"]:
        news(S, "🔴 Rolling blackouts reported across Calgary.", "bad")
        S["blackouts"] += 1
    if not in_bo and S["in_blackout"]:
        news(S, "✅ Power has been restored across Calgary.", "good")
    S["in_blackout"] = in_bo
    if fr["hospital"] < 0.999 and not S["hosp_emerg"]:
        news(S, "🏥 Calgary hospitals have switched to emergency generators.", "bad")
    S["hosp_emerg"] = fr["hospital"] < 0.999
    if in_bo:
        S["blackout_hours"] += 1
        S["unserved_total"] += o["unserved"]

    if in_bo:
        inst = max(0.0, 40.0 - 200.0 * o["unserved"] / D)
    elif reserve < 0.15:
        inst = 60.0 + max(0.0, reserve) / 0.15 * 40.0
    else:
        inst = 100.0
    S["stability"] += (inst - S["stability"]) * 0.18

    sat = S["satisfaction"]
    sat = sat - (0.5 + 10.0 * o["unserved"] / D) if in_bo else sat + 0.12
    S["satisfaction"] = clamp(sat - dr_sat, 0.0, 100.0)

    if in_bo:
        freq = 60.0 - min(1.2, 0.25 + 3.5 * o["unserved"] / D)
    elif reserve < 0.05:
        freq = 60.0 - 0.03 * (0.05 - reserve) / 0.05
    else:
        freq = 60.0 + rng.gauss(0, 0.004)

    # ---- record --------------------------------------------------------------
    recent = S["hist"][-23:]
    co2_24 = sum(r["co2"] for r in recent) + co2
    served_24 = sum(r["served"] for r in recent) + served
    em_score = clamp(100 - (co2_24 * 1000 / max(served_24, 1.0)) / 4.5, 0, 100)
    cloud_ev = any(e["key"] == "cloud" for e in S["events"])
    row = dict(
        t=t, day=day, hour=hour, date=date.strftime("%a %b %d"),
        time=dt.datetime.combine(date, dt.time(hour)),
        demand=D, served=served, available=avail_cap, reserve=reserve, unserved=o["unserved"],
        nuclear=o["nuclear"], hydro=o["hydro"], wind=o["wind"], solar=o["solar"], battery=o["battery"],
        gas=o["gas"], imports=o["imports"], charge=o["charge"], curtailed=o["curtailed"],
        variable=wind_out + solar_out, clean=clean, co2=co2, ci=ci, em_score=em_score,
        price=price, revenue=revenue, net=net, cost_fuel=cost_fuel, cost_imp=cost_imp, cost_om=cost_om,
        cost_pen=cost_pen, soc=S["soc"], soc_frac=S["soc"] / max(cap["battery_mwh"], 1.0),
        freq=freq, stability=S["stability"], satisfaction=S["satisfaction"], temp=T, wind_ms=v,
        wind_cf=wind_out / max(cap["wind"], 1.0), solar_cf=solar_out / max(cap["solar"], 1.0),
        hydro_cf=hydro_out / max(cap["hydro"], 1.0), nuc_cf=nuc_out / max(cap["nuclear"], 1.0) if cap["nuclear"] else 0.0,
        sun_el=sun_el, sun_ha=ha, cover=1.0 if cloud_ev else clamp((1 - S["clear"]) * 1.6, 0, 1),
        f_home=fr["homes"], f_down=fr["downtown"], f_ind=fr["industry"], f_hosp=fr["hospital"], f_sch=fr["school"],
        imp_cap=imp_cap, gas_av=gas_av, pop=S["pop"],
    )
    S["hist"].append(row)
    del S["hist"][:-336]
    S["last"] = row
    S["t"] += 1

    if S["satisfaction"] <= 5:
        S["over"] = "fired"
    elif S["cash"] < -80:
        S["over"] = "bankrupt"


def avg_last(S, key, n=7):
    ds = S["daily"][-n:]
    return sum(d[key] for d in ds) / len(ds) if ds else 0.0


def goal_status(S):
    L = LEVELS[S["level"]]
    items = [("Days in a row with no blackout", S["streak"], L["streak"], True)]
    if L["clean"]:
        items.append(("Clean energy share (7-day average, %)", avg_last(S, "clean_share") * 100, L["clean"] * 100, True))
    if L["ci"]:
        items.append(("Carbon intensity (7-day average, g/kWh)", avg_last(S, "ci"), float(L["ci"]), False))
    ok = all((cur >= tgt) if hi else (cur <= tgt) for _, cur, tgt, hi in items)
    return items, ok


def finalize_day(S, d):
    rows = [r for r in S["hist"] if r["day"] == d]
    if not rows:
        return
    served = sum(r["served"] for r in rows)
    unserved = sum(r["unserved"] for r in rows)
    co2 = sum(r["co2"] for r in rows)
    S["daily"].append(dict(
        day=d + 1, date=rows[-1]["date"], unserved=unserved,
        blackout_hours=sum(1 for r in rows if r["unserved"] > 0.5),
        clean_share=sum(r["clean"] for r in rows) / max(served, 1.0), ci=co2 * 1000 / max(served, 1.0), co2=co2,
        net=sum(r["net"] for r in rows) / 1e6, curtailed=sum(r["curtailed"] for r in rows),
        peak=max(r["demand"] for r in rows), cash=S["cash"], satisfaction=S["satisfaction"]))
    reliable = unserved < 0.5
    S["streak"] = S["streak"] + 1 if reliable else 0
    if reliable and S["satisfaction"] >= 60:
        S["pop"] *= 1.0012
    elif S["satisfaction"] < 40:
        S["pop"] *= 0.9995
    if reliable and S["streak"] > 0 and S["streak"] % 7 == 0:
        S["cap"]["solar"] += 5
        news(S, "☀️ A week without blackouts: households add 5 MW of rooftop solar on their own.", "good")
    if S["mode"] == "career" and not S["won"]:
        _, ok = goal_status(S)
        if ok and S["level"] < 3:
            S["level"] += 1
            S["demand_target"] = LEVELS[S["level"]]["demand"]
            S["cash"] += 40
            S["streak"] = 0
            S["promo"] = f"🎉 Promoted to {LEVELS[S['level']]['title']}! Bonus budget: +$40M. " + LEVELS[S["level"]]["blurb"]
            news(S, S["promo"], "good")
        elif ok and S["level"] == 3:
            S["won"] = True
            news(S, "🏆 Mission complete! Calgary has a reliable, cleaner grid because of you.", "good")


def advance(S, hours, stop_on_alert=False):
    S["promo"] = None
    for _ in range(hours):
        if S["over"]:
            break
        S["alert"] = None
        simulate_hour(S)
        if S["over"]:
            S["running"] = False
            break
        if S["won"] and not S["balloons"]:
            S["running"] = False
            break
        if stop_on_alert and (S["alert"] or S["promo"]):
            S["running"] = False
            break


# =============================================================================
# 4. CALLBACKS
# =============================================================================
def G():
    return st.session_state.get("G")


def sync_settings():
    S = G()
    if not S:
        return
    ss = st.session_state
    S["import_limit"] = int(ss.get("import_slider", S["import_limit"]))
    S["batt_strategy"] = "greedy" if str(ss.get("batt_strategy", "")).startswith("Use") else "peak"
    S["grid_charge"] = bool(ss.get("grid_charge", False))
    S["rand_events"] = bool(ss.get("opt_events", True))
    S["autopause"] = bool(ss.get("opt_autopause", True))


def cb_start():
    ss = st.session_state
    ss["G"] = new_game(ss.get("setup_season", list(START_OPTIONS)[0]), ss.get("setup_mode", "Career mode"))
    ss["import_slider"] = 150


def cb_restart():
    st.session_state["G"] = None
    st.session_state.pop("import_slider", None)


def cb_toggle_run():
    sync_settings()
    S = G()
    if S and not S["over"]:
        S["running"] = not S["running"]


def cb_step(hours):
    sync_settings()
    S = G()
    if S:
        S["running"] = False
        advance(S, hours)


def cb_build(kind):
    sync_settings()
    S = G()
    spec = BUILDS[kind]
    if not S or S["cash"] < spec["cost"] or S["over"]:
        return
    S["cash"] -= spec["cost"]
    S["queue"].append(dict(kind=kind, start=S["t"], eta=S["t"] + spec["days"] * 24))
    S["builds"] = S.get("builds", 0) + 1
    news(S, f"🚧 Construction started: {spec['label']} (ready in {spec['days']} days, −${spec['cost']}M).", "info")
    try:
        st.toast(f"{spec['icon']} {spec['label']} under construction — ready in {spec['days']} days")
    except Exception:
        pass


def cb_dr(kind):
    sync_settings()
    S = G()
    spec = DR[kind]
    if not S or S["t"] < S["dr_ready"][kind] or S["cash"] < spec["cost"]:
        return
    S["cash"] -= spec["cost"]
    S["dr_until"][kind] = S["t"] + spec["hours"]
    S["dr_ready"][kind] = S["t"] + spec["hours"] + spec["cooldown"]
    news(S, f"{spec['icon']} {spec['label']} activated for {spec['hours']} hours.", "info")


def cb_event():
    sync_settings()
    S = G()
    if S:
        start_event(S, st.session_state.get("test_event", "cloud"), forced=True)


# =============================================================================
# 5. STYLE
# =============================================================================
CSS = """
<style>
.stApp{background:radial-gradient(1200px 600px at 8% -10%,#16264a 0%,#0b1220 55%) fixed;color:#e2e8f0}
header[data-testid="stHeader"]{background:transparent}
.block-container{padding-top:1.2rem;max-width:1500px}
section[data-testid="stSidebar"]{background:#0a1020;border-right:1px solid #1f2c47}
.stApp h1,.stApp h2,.stApp h3,.stApp h4{color:#f1f5f9;letter-spacing:-.01em}
.stApp p,.stApp li,.stApp label,.stApp span{color:inherit}
.hero-title{font-size:2.7rem;font-weight:800;line-height:1.1;margin:0;
 background:linear-gradient(90deg,#38bdf8,#34d399,#fbbf24);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.hero-sub{color:#9fb0cc;font-size:1.05rem;margin-top:.35rem}
.big-title{font-size:4rem;font-weight:800;line-height:1.05;margin:.2rem 0;
 background:linear-gradient(90deg,#38bdf8,#34d399,#fbbf24);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.card{background:linear-gradient(180deg,#131d36,#0f1729);border:1px solid #1f2c47;border-radius:14px;padding:12px 14px;height:100%}
.card .lbl{font-size:.7rem;letter-spacing:.09em;text-transform:uppercase;color:#8aa0c0}
.card .val{font-size:1.65rem;font-weight:700;line-height:1.2}
.card .sub{font-size:.78rem;color:#8aa0c0}
.status{border-radius:14px;padding:10px 16px;font-weight:700;font-size:1.05rem;display:flex;justify-content:space-between;align-items:center}
.ticker{overflow:hidden;white-space:nowrap;background:#0d1426;border:1px solid #1f2c47;border-radius:10px;padding:7px 0;margin:8px 0 12px 0}
.ticker span{display:inline-block;padding-left:100%;animation:scrollx 55s linear infinite;color:#fcd9a8;font-size:.9rem}
@keyframes scrollx{to{transform:translateX(-100%)}}
.bar{height:8px;background:#1a2540;border-radius:6px;overflow:hidden;margin-top:3px}
.bar>div{height:100%;border-radius:6px}
.kv{display:flex;justify-content:space-between;font-size:.86rem;margin-top:8px;color:#cbd5e1}
.kv b{color:#f1f5f9}
.panel{background:linear-gradient(180deg,#121c33,#0e1628);border:1px solid #1f2c47;border-radius:14px;padding:14px 16px}
.panel h4{margin:0 0 4px 0;font-size:.95rem;color:#f1f5f9}
.muted{color:#8aa0c0;font-size:.8rem}
.pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:.78rem;font-weight:600;border:1px solid #2b3b5e;color:#cbd5e1}
div[data-testid="stVerticalBlockBorderWrapper"]{border-color:#1f2c47!important;border-radius:14px!important}
.stTabs [data-baseweb="tab"]{font-weight:600}
</style>
"""


def card(label, value, sub="", color="#e2e8f0", border=None):
    b = f"border-color:{border};" if border else ""
    return H(f'<div class="card" style="{b}"><div class="lbl">{label}</div>'
             f'<div class="val" style="color:{color}">{value}</div><div class="sub">{sub}</div></div>')


def score_color(v):
    return "#34d399" if v >= 75 else "#fbbf24" if v >= 45 else "#f87171"


def grid_status(r):
    if r["unserved"] > 0.5:
        return "BLACKOUT", "🔴", "#ef4444"
    if r["reserve"] < 0.04:
        return "Critical", "🔶", "#f97316"
    if r["reserve"] < 0.12:
        return "Tight", "⚠️", "#fbbf24"
    return "Stable", "✅", "#34d399"


# =============================================================================
# 6. THE LIVING CITY (animated SVG)
# =============================================================================
SVG_CSS = """
html,body{height:100%}
body{margin:0;background:#0b1220;overflow:hidden}
svg{display:block;width:100%;height:100%}
.flow{stroke-dasharray:6 8;animation:dash 1s linear infinite}
@keyframes dash{to{stroke-dashoffset:-28}}
.cloud{animation:drift linear infinite}
@keyframes drift{from{transform:translateX(-260px)}to{transform:translateX(1260px)}}
.twinkle{animation:tw 3s ease-in-out infinite}
@keyframes tw{50%{opacity:.2}}
.smoke{animation:rise 4s ease-out infinite}
@keyframes rise{from{transform:translateY(0) scale(.6);opacity:.55}to{transform:translateY(-38px) scale(1.6);opacity:0}}
.pulse{animation:pulse 1.1s ease-in-out infinite}
@keyframes pulse{50%{opacity:.35}}
.car{animation:drive linear infinite}
@keyframes drive{from{transform:translateX(-40px)}to{transform:translateX(1040px)}}
.beam{animation:beam 2.4s ease-in-out infinite}
@keyframes beam{0%,100%{opacity:.35}50%{opacity:1}}
.pulse-soft{animation:psoft 2.5s ease-in-out infinite}
@keyframes psoft{50%{opacity:.5}}
.sway{animation:sway 9s ease-in-out infinite alternate}
@keyframes sway{from{transform:translateX(-26px)}to{transform:translateX(26px)}}
.rain{animation:rain linear infinite}
@keyframes rain{from{transform:translate(0,-20px)}to{transform:translate(-40px,480px)}}
.snow{animation:snow linear infinite}
@keyframes snow{from{transform:translate(0,-10px)}to{transform:translate(-70px,490px)}}
.gust{stroke-dasharray:70 380;animation:gust 2.8s linear infinite}
@keyframes gust{from{stroke-dashoffset:0}to{stroke-dashoffset:-450}}
.leaf{animation:leaf linear infinite}
@keyframes leaf{from{transform:translate(-20px,0) rotate(0deg)}to{transform:translate(1040px,110px) rotate(720deg)}}
.flicker{animation:flk .8s steps(1) infinite}
@keyframes flk{0%{opacity:1}30%{opacity:0}45%{opacity:1}60%{opacity:.2}80%{opacity:1}}
.spark{animation:spark .7s ease-out infinite}
@keyframes spark{from{transform:translate(0,0);opacity:1}to{transform:translate(8px,-28px);opacity:0}}
.shim{animation:shim 2.2s ease-in-out infinite}
@keyframes shim{0%,100%{transform:translateY(0)}50%{transform:translateY(-7px)}}
.flash{animation:flash 1.1s ease-out forwards;pointer-events:none}
@keyframes flash{from{opacity:.65}to{opacity:0}}
.slidein{animation:slidein .6s ease-out}
@keyframes slidein{from{transform:translateY(-60px);opacity:0}to{transform:translateY(0);opacity:1}}
text{font-family:Segoe UI,Helvetica,Arial,sans-serif}
"""

SKY_NIGHT = ("#050816", "#0c1733")


def sky_colors(el):
    if el < -12:
        return SKY_NIGHT
    if el < 0:
        f = (el + 12) / 12.0
        return mix(SKY_NIGHT[0], "#2b2f6b", f), mix(SKY_NIGHT[1], "#f08a5d", f * f)
    if el < 12:
        f = el / 12.0
        return mix("#2b2f6b", "#2a6fb5", f), mix("#f08a5d", "#9fd0ee", f)
    return "#2b78c2", "#a9d8f2"


def windows(x, y, w, h, frac, seed, night, color_on="#ffd98a", always=False):
    cols, rows = int((w - 8) // 7), int((h - 8) // 9)
    out = []
    for i in range(cols):
        for j in range(rows):
            k = ((i * 73856093) ^ (j * 19349663) ^ (seed * 83492791)) % 1000 / 1000.0
            k2 = ((i * 31 + j * 17 + seed * 7) % 100) / 100.0
            lit = always or (k < 0.30 + 0.65 * night and k2 < frac)
            out.append(f'<rect x="{x + 4 + i * 7}" y="{y + 4 + j * 9}" width="4" height="5" '
                       f'fill="{color_on if lit else "#121a2d"}"/>')
    return "".join(out)


def feeder(x1, y1, x2, y2, color, active):
    if active:
        return f'<line class="flow" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="2.5" stroke-linecap="round"/>'
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#2a3a5c" stroke-width="2"/>'


def label(x, y, text, size=11, color="#b6c4dd", weight="700", anchor="middle"):
    return f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}" text-anchor="{anchor}">{text}</text>'


# ---------- plain-English event info (used by the city graphic AND the event card) ----------
EVENT_PLAIN = {
    "cloud": dict(title="CLOUDY DAY", short="Solar makes 70% less power",
                  what="Thick clouds are blocking the sun.", does="☀️ Solar farms make 70% less power",
                  fix="Use your battery, burn more gas, or buy power from neighbours.", color="#334155", good=False),
    "wind_drought": dict(title="WIND DROUGHT", short="Wind farms are almost stopped",
                         what="The wind has stopped blowing.", does="🌬️ Wind farms make almost no power",
                         fix="Use your battery or gas plants. A bigger battery would help next time.", color="#475569", good=False),
    "heat": dict(title="HEAT WAVE", short="Everyone is running AC, so the city needs more power",
                 what="It is very hot, so everyone is running air conditioning.",
                 does="⚡ The city needs MORE power, and gas plants work less well",
                 fix="Ask people to save power, pay factories to cut back, or buy power.", color="#9a3412", good=False),
    "storm": dict(title="WINTER STORM", short="Everyone is heating, so the city needs more power",
                  what="It is freezing, so everyone is turning up the heat.",
                  does="⚡ The city needs MORE power, and solar and wind are weaker",
                  fix="Buy power, ask people to save power, and save battery charge for the evening peak.", color="#1e40af", good=False),
    "transmission": dict(title="POWER LINE DOWN", short="You can't buy power from neighbours",
                         what="A big power line has failed.", does="🔌 You can't buy any power from neighbours",
                         fix="Rely on your own plants and battery. Ask people to save power.", color="#991b1b", good=False),
    "equipment": dict(title="PLANT BROKE DOWN", short="A plant broke down: lost {loss} MW",
                      what="A big power plant has stopped working.", does="🔧 You lose {loss} MW of power",
                      fix="Buy power, use your battery, or ask people to save power.", color="#991b1b", good=False),
    "chinook": dict(title="CHINOOK WINDS", short="Warm wind: wind farms make more power",
                    what="Warm winds are rushing down from the Rocky Mountains.",
                    does="🌬️ Wind farms make MORE power, and less heating is needed",
                    fix="Good news! Use the extra clean power to fill your battery.", color="#166534", good=True),
}


def _bolt(x, y, s=1.0):
    pts = " ".join(f"{x + px * s:.0f},{y + py * s:.0f}" for px, py in ((6, 0), (-6, 22), (2, 22), (-8, 48), (14, 16), (5, 16), (12, 0)))
    return f'<polygon class="flicker" points="{pts}" fill="#fde047" stroke="#fff7ae" stroke-width="1" filter="url(#glow)"/>'


def fx_back(S, r, B, dayf, night, evk):
    """Weather effects drawn BEHIND the city buildings (sun rays, storm clouds, chinook arch)."""
    o = []
    if "heat" in evk:
        if dayf > 0.25:
            k = 0.45 + 0.55 * dayf
            sx, sy = 840, 80
            rays = "".join(f'<polygon points="0,-34 -6,-72 6,-72" transform="rotate({a})" fill="#ffd166"/>' for a in range(0, 360, 22))
            o.append('<defs><linearGradient id="beam" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffb703" stop-opacity="0.6"/>'
                     '<stop offset="1" stop-color="#ff5d2a" stop-opacity="0"/></linearGradient>'
                     '<radialGradient id="hsun"><stop offset="0" stop-color="#fff3b0"/><stop offset="0.5" stop-color="#ff9f1c" stop-opacity="0.9"/>'
                     '<stop offset="1" stop-color="#ff5d2a" stop-opacity="0"/></radialGradient></defs>')
            for i, (x1, x2) in enumerate(((130, 300), (300, 450), (450, 580), (580, 700), (700, 810), (820, 930), (930, 1010))):
                o.append(f'<polygon class="beam" points="{sx},{sy} {x1},{B} {x2},{B}" fill="url(#beam)" style="animation-delay:-{i * 0.35:.2f}s"/>')
            o.append(f'<circle cx="{sx}" cy="{sy}" r="95" fill="url(#hsun)" opacity="{k:.2f}"/>')
            o.append(f'<g transform="translate({sx},{sy})" opacity="{k:.2f}"><g><animateTransform attributeName="transform" type="rotate" from="0" to="360" dur="16s" repeatCount="indefinite"/>{rays}</g></g>')
            o.append(f'<circle cx="{sx}" cy="{sy}" r="27" fill="#fff1a8"/>')
        else:
            o.append(f'<defs><radialGradient id="hglow"><stop offset="0" stop-color="#ff5d2a" stop-opacity="0.5"/><stop offset="1" stop-color="#ff5d2a" stop-opacity="0"/></radialGradient></defs>'
                     f'<ellipse cx="500" cy="{B}" rx="560" ry="190" fill="url(#hglow)" class="pulse-soft"/>')
    if "cloud" in evk:
        o.append('<defs><linearGradient id="stormsky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1f2937" stop-opacity="0.95"/>'
                 '<stop offset="1" stop-color="#374151" stop-opacity="0"/></linearGradient></defs>'
                 '<rect x="0" y="0" width="1000" height="200" fill="url(#stormsky)"/>')
        for i in range(9):
            cx, cy = 20 + i * 120, 42 + (i % 3) * 30
            o.append(f'<g class="sway" style="animation-delay:-{i * 1.3:.1f}s"><g fill="{"#2b3341" if i % 2 else "#3b4454"}" opacity="0.95">'
                     f'<ellipse cx="{cx}" cy="{cy}" rx="88" ry="28"/><ellipse cx="{cx - 40}" cy="{cy + 10}" rx="55" ry="22"/><ellipse cx="{cx + 46}" cy="{cy + 8}" rx="58" ry="24"/></g></g>')
    if "chinook" in evk:
        o.append('<g opacity="0.6" filter="url(#glow)"><ellipse cx="250" cy="98" rx="310" ry="13" fill="#f1f5f9"/>'
                 '<ellipse cx="300" cy="116" rx="250" ry="8" fill="#e2e8f0"/><ellipse cx="190" cy="82" rx="200" ry="7" fill="#ffffff"/></g>')
    return "".join(o)


def fx_front(S, r, B, dayf, night, evk):
    """Weather effects, event banner and flashes drawn IN FRONT of the whole scene."""
    o = []
    # ---- HEAT: orange haze + wobbling heat shimmer ----
    if "heat" in evk:
        o.append(f'<rect x="0" y="0" width="1000" height="{B}" fill="#ff7a1a" opacity="0.10"/>')
        for i in range(4):
            y = B - 16 - i * 24
            o.append(f'<path class="shim" d="M-10,{y} q12,-8 24,0' + " t24,0" * 42 + f'" fill="none" stroke="#fff4d6" stroke-width="1.6" opacity="0.20" style="animation-delay:-{i * 0.5:.1f}s"/>')
    # ---- CLOUD: rain + shaded solar farm ----
    if "cloud" in evk:
        o.append(f'<rect x="0" y="0" width="1000" height="{B}" fill="#0f172a" opacity="0.22"/>')
        for i in range(70):
            x = (i * 37) % 1040
            o.append(f'<line class="rain" x1="{x}" y1="0" x2="{x - 5}" y2="14" stroke="#a5c8ff" stroke-width="1.6" opacity="0.6" '
                     f'style="animation-duration:{0.8 + (i % 5) * 0.12:.2f}s;animation-delay:-{(i * 0.17) % 1:.2f}s"/>')
        o.append('<rect x="18" y="394" width="200" height="46" fill="#000" opacity="0.32"/>'
                 '<text x="118" y="441" font-size="11" font-weight="800" fill="#fca5a5" text-anchor="middle" class="pulse">☁️ SOLAR −70%</text>')
    # ---- WIND DROUGHT: still, hazy air ----
    if "wind_drought" in evk:
        o.append(f'<rect x="0" y="195" width="320" height="{B - 195}" fill="#94a3b8" opacity="0.18"/>'
                 '<text x="150" y="190" font-size="13" font-weight="800" fill="#e2e8f0" text-anchor="middle" class="pulse">🍃 NO WIND · 0 m/s</text>')
        for i in range(4):
            y = 215 + i * 22
            o.append(f'<line x1="10" y1="{y}" x2="300" y2="{y}" stroke="#e2e8f0" stroke-width="1" stroke-dasharray="2 9" opacity="0.4"/>')
    # ---- WINTER STORM: snow, blowing streaks, frost ----
    if "storm" in evk:
        o.append(f'<rect x="0" y="0" width="1000" height="{B}" fill="#bcd7ff" opacity="0.15"/>'
                 '<defs><radialGradient id="frost" cx="0.5" cy="0.5" r="0.75"><stop offset="0.55" stop-color="#dbeafe" stop-opacity="0"/>'
                 '<stop offset="1" stop-color="#dbeafe" stop-opacity="0.5"/></radialGradient></defs>'
                 '<rect x="0" y="0" width="1000" height="470" fill="url(#frost)"/>')
        for i in range(90):
            x = (i * 53) % 1100
            sz = 1.2 + (i % 4) * 0.7
            o.append(f'<circle class="snow" cx="{x}" cy="0" r="{sz:.1f}" fill="#fff" opacity="0.9" '
                     f'style="animation-duration:{4 + (i % 6) * 0.9:.1f}s;animation-delay:-{(i * 0.37) % 8:.1f}s"/>')
        for i in range(7):
            y = 120 + i * 32
            o.append(f'<path class="gust" d="M-30,{y} C250,{y + 30} 600,{y - 20} 1030,{y + 40}" fill="none" stroke="#f1f5f9" stroke-width="2" stroke-linecap="round" '
                     f'opacity="0.55" style="animation-delay:-{i * 0.5:.1f}s"/>')
        o.append(f'<rect x="0" y="{B - 1}" width="1000" height="5" fill="#f8fafc" opacity="0.85"/>')
    # ---- CHINOOK: gusts blowing down from the mountains + flying leaves ----
    if "chinook" in evk:
        o.append(f'<rect x="0" y="0" width="1000" height="{B}" fill="#ffb347" opacity="0.07"/>')
        for i in range(14):
            y0 = 150 + (i * 13) % 90
            y1 = 280 + (i * 17) % 60
            o.append(f'<path class="gust" d="M-30,{y0} C220,{y0 + 40} 560,{y1 - 70} 1030,{y1}" fill="none" stroke="#e0f7ff" stroke-width="{2 + (i % 3)}" stroke-linecap="round" '
                     f'opacity="0.75" style="animation-duration:{2.2 + (i % 4) * 0.4:.1f}s;animation-delay:-{i * 0.37:.2f}s"/>')
        for i in range(10):
            y = 190 + (i * 23) % 130
            o.append(f'<ellipse class="leaf" cx="0" cy="{y}" rx="4" ry="2" fill="{"#a3e635" if i % 2 else "#facc15"}" '
                     f'style="animation-duration:{3.5 + (i % 5) * 0.6:.1f}s;animation-delay:-{i * 0.9:.1f}s"/>')
    # ---- TRANSMISSION FAILURE: lightning + broken import line ----
    if "transmission" in evk:
        o.append(_bolt(893, 366, 0.95))
        o.append('<g class="pulse"><circle cx="880" cy="414" r="15" fill="none" stroke="#ef4444" stroke-width="3"/>'
                 '<line x1="869" y1="403" x2="891" y2="425" stroke="#ef4444" stroke-width="3"/></g>')
    # ---- EQUIPMENT FAILURE: red flashing plant with sparks ----
    ev = evk.get("equipment")
    if ev:
        if ev["asset"] == "nuclear" and S["cap"]["nuclear"] > 0:
            x0, x1, y0, y1 = 660, 786, 388, 434
        else:
            x0, x1, y0, y1 = 514, 598, 394, 434
        xc = (x0 + x1) / 2
        o.append(f'<rect class="pulse" x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" fill="#ef4444" opacity="0.45" stroke="#fecaca" stroke-width="2"/>')
        for i in range(7):
            sx_ = x0 + 8 + (i * 29) % (x1 - x0 - 10)
            o.append(f'<circle class="spark" cx="{sx_}" cy="{y0 + 6}" r="2.2" fill="#fde047" style="animation-delay:-{i * 0.23:.2f}s"/>')
        o.append(f'<text x="{xc}" y="{y1 - 6}" font-size="10.5" font-weight="800" fill="#fff" text-anchor="middle">⚠ −{ev["loss"]:.0f} MW</text>')
    # ---- flash when a brand-new event starts ----
    newest = max(S["events"], key=lambda e: e["start"]) if S["events"] else None
    if newest and S["t"] - newest["start"] <= 1 and newest["key"] != "chinook":
        o.append('<rect x="0" y="0" width="1000" height="470" fill="#fff" class="flash"/>')
    # ---- big plain-English banner for the newest event (+ small pills for any others) ----
    if newest:
        pl = EVENT_PLAIN[newest["key"]]
        short = pl["short"].format(loss=f'{newest["loss"]:.0f}')
        left = max(0, newest["end"] - S["t"])
        cls = ' class="slidein"' if S["t"] - newest["start"] <= 1 else ""
        o.append(f'<g{cls}><rect x="12" y="10" width="664" height="38" rx="10" fill="{pl["color"]}" stroke="#ffffff" stroke-opacity="0.5" stroke-width="1.5"/>'
                 f'<text x="26" y="35" font-size="14" font-weight="800" fill="#fff">{EVENTS[newest["key"]]["icon"]} {pl["title"]}</text>'
                 f'<text x="218" y="35" font-size="13" font-weight="600" fill="#fff" opacity="0.95">{short}</text>'
                 f'<text x="662" y="35" font-size="12" font-weight="700" fill="#fff" text-anchor="end" opacity="0.9">{left}h left</text></g>')
        others = [e for e in S["events"] if e is not newest]
        for i, e in enumerate(others):
            ep = EVENT_PLAIN[e["key"]]
            o.append(f'<rect x="12" y="{54 + i * 26}" width="250" height="22" rx="11" fill="{ep["color"]}" stroke="#fff" stroke-opacity="0.4"/>'
                     f'<text x="24" y="{69 + i * 26}" font-size="12" font-weight="700" fill="#fff">{EVENTS[e["key"]]["icon"]} {ep["title"]} · {max(0, e["end"] - S["t"])}h left</text>')
    return "".join(o)


def infra_svg(S, r):
    """Gas plant and import power line (so every supply source is visible)."""
    o = []
    use = clamp(r["gas"] / max(r["gas_av"], 1.0), 0, 1)
    gcol = mix("#1f2a4a", "#7b8aa8", use)
    o.append(label(570, 381, f'🔥 GAS · {r["gas"]:.0f} MW', 10.5))
    o.append(f'<rect x="516" y="410" width="80" height="22" fill="{gcol}" stroke="#4b5b7d"/>'
             f'<rect x="524" y="396" width="9" height="14" fill="{gcol}" stroke="#4b5b7d"/><rect x="580" y="396" width="9" height="14" fill="{gcol}" stroke="#4b5b7d"/>'
             f'<rect x="540" y="418" width="30" height="8" fill="#121a2d"/>')
    if r["gas"] > 1:
        for i, cx in enumerate((528, 584)):
            o.append(f'<circle class="smoke" cx="{cx}" cy="394" r="5" fill="#8794ad" style="animation-delay:-{i * 1.6:.1f}s"/>')
    o.append(feeder(556, 432, 556, 446, "#94a3b8", r["gas"] > 0.5))
    # import tower
    cut = any(e["key"] == "transmission" for e in S["events"])
    tcol = "#8fa4c9"
    o.append(label(880, 388, '🔌 LINE DOWN', 10, '#fca5a5') if cut else label(880, 388, f'🔌 IMPORT · {r["imports"]:.0f} MW', 10))
    o.append(f'<polyline points="864,436 880,398 896,436" fill="none" stroke="{tcol}" stroke-width="2"/>'
             f'<line x1="868" y1="426" x2="892" y2="426" stroke="{tcol}" stroke-width="1.5"/><line x1="872" y1="414" x2="888" y2="414" stroke="{tcol}" stroke-width="1.5"/>'
             f'<line x1="858" y1="404" x2="902" y2="404" stroke="{tcol}" stroke-width="2.5"/><line x1="868" y1="398" x2="892" y2="398" stroke="{tcol}" stroke-width="2"/>')
    wire = "#ef4444" if cut else ("#fb923c" if r["imports"] > 0.5 else "#2a3a5c")
    cls = ' class="flow"' if (r["imports"] > 0.5 and not cut) else ""
    dash = ' stroke-dasharray="3 7"' if cut else ""
    o.append(f'<line{cls} x1="902" y1="404" x2="1000" y2="392" stroke="{wire}" stroke-width="2"{dash}/>'
             f'<line{cls} x1="858" y1="404" x2="800" y2="414" stroke="{wire}" stroke-width="2"{dash}/>')
    o.append(feeder(880, 436, 880, 446, "#fb923c", r["imports"] > 0.5))
    return "".join(o)


def city_svg(S):
    r, cap, B = S["last"], S["cap"], 335
    el = r["sun_el"]
    dayf = clamp((el + 6) / 20.0, 0, 1)
    night = 1 - dayf
    top, bot = sky_colors(el)
    fd, fh, fs, fi, fho = r["f_down"], r["f_home"], r["f_sch"], r["f_ind"], r["f_hosp"]
    evk = {e["key"]: e for e in S["events"]}
    heat, cloudy = "heat" in evk, "cloud" in evk
    status, _, scol = grid_status(r)
    p = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 470" preserveAspectRatio="xMidYMid meet">']
    p.append(f'''<defs>
<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{top}"/><stop offset="1" stop-color="{bot}"/></linearGradient>
<linearGradient id="ground" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{mix('#16223b', '#2b3a56', dayf)}"/><stop offset="1" stop-color="#080d18"/></linearGradient>
<radialGradient id="sunG"><stop offset="0" stop-color="#fff6c2"/><stop offset="1" stop-color="#ffb703" stop-opacity="0"/></radialGradient>
<filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
</defs>''')
    p.append('<rect width="1000" height="470" fill="url(#sky)"/>')

    # stars, sun, moon
    if night > 0.05:
        for i in range(48):
            x, y, rr = (i * 137 + 31) % 1000, (i * 59 + 17) % 230, 0.8 + ((i * 7) % 10) / 10
            p.append(f'<circle class="twinkle" cx="{x}" cy="{y}" r="{rr:.1f}" fill="#fff" '
                     f'opacity="{night * (0.35 + 0.6 * ((i * 3) % 10) / 10):.2f}" style="animation-delay:-{(i % 7) * 0.4:.1f}s"/>')
    ha = r["sun_ha"]
    if el > -8 and not heat and not cloudy:
        sx, sy = 500 + 430 * math.sin(ha), clamp(330 - 4.2 * el, 40, 360)
        p.append(f'<circle cx="{sx:.0f}" cy="{sy:.0f}" r="52" fill="url(#sunG)" opacity="0.8"/>'
                 f'<circle cx="{sx:.0f}" cy="{sy:.0f}" r="14" fill="{mix("#ff9a3c", "#fff3b0", el / 20)}"/>')
    if el < -3:
        mx, my = 500 - 430 * math.sin(ha), clamp(330 + 3.8 * el, 40, 330)
        p.append(f'<circle cx="{mx:.0f}" cy="{my:.0f}" r="22" fill="#e8eefc" opacity="{min(1, night * 1.2):.2f}" filter="url(#glow)"/>'
                 f'<circle cx="{mx + 7:.0f}" cy="{my - 3:.0f}" r="19" fill="{top}" opacity="{min(1, night * 1.2) * 0.55:.2f}"/>')

    # clouds
    cover = r["cover"]
    ccol = mix("#e8eef7", "#4b5565", cover * 0.7 + night * 0.5)
    for i in range(3 + round(6 * cover)):
        y = 30 + (i * 47) % 110
        sc = 0.8 + (i % 3) * 0.3
        p.append(f'<g transform="translate(0,{y})"><g class="cloud" style="animation-duration:{55 + (i * 9) % 40}s;animation-delay:-{(i * 17) % 60}s">'
                 f'<g transform="translate({(i * 173) % 700},0) scale({sc})" fill="{ccol}" opacity="{0.28 + 0.55 * cover:.2f}">'
                 f'<ellipse cx="0" cy="0" rx="34" ry="11"/><ellipse cx="-18" cy="-8" rx="20" ry="11"/><ellipse cx="14" cy="-10" rx="24" ry="13"/></g></g></g>')

    # Rocky Mountains, wind hills
    p.append(f'<path d="M0,335 L0,215 L50,175 L85,200 L140,130 L190,185 L230,160 L300,215 L370,150 L430,200 L480,175 L560,225 '
             f'L640,190 L720,235 L800,205 L880,240 L940,215 L1000,235 L1000,335 Z" fill="{mix("#0d1730", "#3b5078", dayf * 0.8)}"/>')
    snow = mix("#8ea3c7", "#f1f5ff", dayf * 0.8 + 0.2)
    for (sx_, sy_) in ((140, 130), (370, 150), (230, 160)):
        p.append(f'<path d="M{sx_},{sy_} L{sx_ - 14},{sy_ + 22} L{sx_ - 6},{sy_ + 18} L{sx_ + 1},{sy_ + 26} L{sx_ + 8},{sy_ + 18} L{sx_ + 16},{sy_ + 22} Z" fill="{snow}" opacity="0.9"/>')
    p.append(f'<path d="M0,335 L0,262 Q70,232 140,262 T260,282 L300,335 Z" fill="{mix("#0e182e", "#22345a", dayf * 0.7)}"/>')

    # wind turbines (spin speed follows real wind output)
    n_t = min(6, max(2, math.ceil(cap["wind"] / 30)))
    for (tx, ty) in [(30, 251), (75, 247), (118, 254), (160, 267), (205, 282), (250, 286)][:n_t]:
        cf = r["wind_cf"]
        spin = (f'<animateTransform attributeName="transform" type="rotate" from="0" to="360" dur="{9 - 7.5 * clamp(cf, 0, 1):.1f}s" repeatCount="indefinite"/>'
                if (cf > 0.03 and "wind_drought" not in evk) else "")
        blades = "".join(f'<line x1="0" y1="0" x2="{22 * math.cos(math.radians(a)):.1f}" y2="{22 * math.sin(math.radians(a)):.1f}" '
                         f'stroke="#e5ecf8" stroke-width="2.2" stroke-linecap="round"/>' for a in (-90, 30, 150))
        p.append(f'<line x1="{tx}" y1="{ty}" x2="{tx}" y2="{ty - 46}" stroke="#cbd5e1" stroke-width="2"/>'
                 f'<g transform="translate({tx},{ty - 46})"><g>{spin}{blades}</g><circle r="2.6" fill="#f1f5f9"/></g>')

    p.append(fx_back(S, r, B, dayf, night, evk))
    # ground, road
    p.append(f'<rect x="0" y="{B}" width="1000" height="135" fill="url(#ground)"/>')
    p.append(f'<rect x="0" y="{B + 3}" width="1000" height="10" fill="#1a2336"/>'
             f'<line x1="0" y1="{B + 8}" x2="1000" y2="{B + 8}" stroke="#3a4a6b" stroke-width="1" stroke-dasharray="10 10"/>')

    # homes
    for i in range(8):
        x, hh = 262 + i * 26, 14 + (i * 5) % 6
        body = mix("#0d1526", "#2b3a5c", fh * 0.8 + 0.15)
        roof = mix("#10192c", "#4a3b3b", fh * 0.7 + 0.1)
        lit = [("#ffd98a" if (((i * 7 + j * 3) % 10) / 10.0 < fh * (0.35 + 0.6 * night)) else "#121a2d") for j in range(2)]
        p.append(f'<rect x="{x}" y="{B - hh}" width="20" height="{hh}" fill="{body}"/>'
                 f'<polygon points="{x - 2},{B - hh} {x + 10},{B - hh - 12} {x + 22},{B - hh}" fill="{roof}"/>'
                 f'<rect x="{x + 4}" y="{B - hh + 4}" width="5" height="5" fill="{lit[0]}"/><rect x="{x + 12}" y="{B - hh + 4}" width="5" height="5" fill="{lit[1]}"/>')

    # downtown + Calgary Tower
    for idx, (x, w, h) in enumerate([(498, 26, 98), (526, 30, 142), (558, 24, 112), (612, 34, 168), (648, 28, 128), (678, 24, 102), (704, 26, 150)]):
        p.append(f'<rect x="{x}" y="{B - h}" width="{w}" height="{h}" fill="{mix("#0b1222", "#2a3a5e", fd * 0.85 + 0.12)}"/>'
                 f'<rect x="{x}" y="{B - h}" width="{w}" height="3" fill="{mix("#142038", "#46608f", fd)}"/>'
                 + windows(x, B - h, w, h, fd, idx + 3, night))
    tcol = mix("#0f1829", "#8fa4c9", fd * 0.8 + 0.1)
    p.append(f'<rect x="582" y="{B - 176}" width="8" height="176" fill="{tcol}"/>'
             f'<ellipse cx="586" cy="{B - 168}" rx="17" ry="7" fill="{tcol}"/>'
             f'<ellipse cx="586" cy="{B - 168}" rx="14" ry="3" fill="{"#ffd98a" if (fd > 0.3 and night > 0.2) else "#121a2d"}"/>'
             f'<line x1="586" y1="{B - 176}" x2="586" y2="{B - 206}" stroke="{tcol}" stroke-width="2"/>'
             f'<circle cx="586" cy="{B - 207}" r="2" fill="{"#ef4444" if fd > 0.3 else "#3a4a6b"}" class="pulse"/>')

    # hospital (backup generators keep it lit during blackouts)
    hosp_col = "#ff9a62" if fho < 0.999 else "#ffd98a"
    p.append(f'<rect x="738" y="{B - 66}" width="60" height="66" fill="{mix("#0e1626", "#2e3f63", fho * 0.9 + 0.1)}"/>'
             f'<rect x="762" y="{B - 60}" width="6" height="18" fill="#ef4444"/><rect x="756" y="{B - 54}" width="18" height="6" fill="#ef4444"/>'
             + windows(738, B - 42, 60, 42, 1.0, 11, night, color_on=hosp_col, always=True)
             + (f'<text x="768" y="{B - 72}" font-size="9" fill="#ff9a62" text-anchor="middle" class="pulse" font-weight="700">EMERGENCY POWER</text>' if fho < 0.999 else ""))

    # school
    p.append(f'<rect x="815" y="{B - 34}" width="56" height="34" fill="{mix("#0e1626", "#3b4a6e", fs * 0.9 + 0.1)}"/>'
             f'<polygon points="811,{B - 34} 843,{B - 50} 875,{B - 34}" fill="{mix("#10192c", "#5a4040", fs * 0.7 + 0.1)}"/>'
             + windows(815, B - 34, 56, 34, fs, 17, night)
             + f'<line x1="843" y1="{B - 50}" x2="843" y2="{B - 66}" stroke="#94a3b8" stroke-width="1.5"/><rect x="843" y="{B - 66}" width="11" height="7" fill="#ef4444"/>')

    # industrial district
    on = fi > 0.3
    p.append(f'<rect x="888" y="{B - 38}" width="66" height="38" fill="{mix("#0d1424", "#2f3d5c", fi * 0.9 + 0.1)}"/>'
             f'<polygon points="888,{B - 38} 904,{B - 50} 904,{B - 38} 921,{B - 50} 921,{B - 38} 938,{B - 50} 938,{B - 38} 954,{B - 38}" fill="{mix("#10192c", "#3a4660", fi)}"/>'
             f'<rect x="944" y="{B - 66}" width="9" height="66" fill="{mix("#0d1424", "#4a5675", fi)}"/><rect x="960" y="{B - 54}" width="9" height="54" fill="{mix("#0d1424", "#4a5675", fi)}"/>'
             + windows(888, B - 32, 66, 24, fi, 23, 1.0))
    if on:
        for i, (cx, cy) in enumerate([(948, B - 68), (964, B - 56), (948, B - 68)]):
            p.append(f'<circle class="smoke" cx="{cx}" cy="{cy}" r="7" fill="#7b8aa5" style="animation-delay:-{i * 1.3:.1f}s"/>')
    if fi < 0.15:
        p.append(f'<text x="921" y="{B - 74}" font-size="9" fill="#f87171" text-anchor="middle" class="pulse" font-weight="700">FACTORY OFFLINE</text>')

    # street lights & traffic
    sl = night > 0.3 and fh > 0.3
    for i in range(16):
        x = 20 + i * 62
        p.append(f'<rect x="{x}" y="{B - 2}" width="1.5" height="5" fill="#3a4a6b"/><circle cx="{x}" cy="{B - 3}" r="2.2" fill="{"#ffe9a8" if sl else "#1a2336"}"/>')
    for i in range(4):
        p.append(f'<g transform="translate(0,{B + 5})"><g class="car" style="animation-duration:{9 + i * 4}s;animation-delay:-{i * 3.1:.1f}s">'
                 f'<rect x="0" y="0" width="14" height="5" rx="2" fill="{"#5b6b8c" if (fh + fd) > 0.2 else "#2a3550"}"/>'
                 f'<circle cx="13" cy="2.5" r="1.4" fill="{"#fff3b0" if night > 0.3 else "#5b6b8c"}"/></g></g>')

    # district labels
    for x, name, f in ((363, "HOMES", fh), (614, "DOWNTOWN", fd), (768, "HOSPITAL", fho), (843, "SCHOOL", fs), (921, "INDUSTRY", fi)):
        col = "#b6c4dd" if f > 0.99 else "#fbbf24" if f > 0.5 else "#f87171"
        p.append(label(x, B + 28, name, 10.5, col))
        if f < 0.99:
            p.append(label(x, B + 40, f"⚠ {f * 100:.0f}% powered" if name != "HOSPITAL" else "backup generators", 9, col, "600"))

    # darkness when the grid fails
    dark = clamp(r["unserved"] / max(r["demand"], 1) * 3.0, 0, 0.5)
    if dark > 0:
        p.append(f'<rect x="0" y="100" width="1000" height="{B - 100}" fill="#000" opacity="{dark:.2f}"/>')

    # ---------- foreground: generation facilities ----------
    # solar farm
    n_p = min(24, max(4, int(cap["solar"] / 8)))
    pc = mix("#16294d", "#f6c453", r["solar_cf"] * 1.1)
    p.append(label(115, 391, f'☀️ SOLAR · {r["solar"]:.0f} MW', 10.5))
    for i in range(n_p):
        x, y = 25 + (i % 8) * 22, 397 + (i // 8) * 14
        p.append(f'<polygon points="{x},{y + 9} {x + 4},{y} {x + 22},{y} {x + 18},{y + 9}" fill="{pc}" stroke="#0a1224" stroke-width="0.8"/>')
    # battery
    cells = 10
    p.append(label(281, 391, f'🔋 BATTERY · {r["soc_frac"] * 100:.0f}%', 10.5))
    sc = "#34d399" if r["soc_frac"] > 0.5 else "#fbbf24" if r["soc_frac"] > 0.2 else "#f87171"
    for i in range(cells):
        x, y = 234 + (i % 5) * 19, 398 + (i // 5) * 17
        filled = (i + 0.5) / cells <= r["soc_frac"] + 0.05
        p.append(f'<rect x="{x}" y="{y}" width="17" height="13" rx="2" fill="#0f1a30" stroke="#33466d"/>'
                 + (f'<rect x="{x + 2}" y="{y + 2}" width="13" height="9" rx="1" fill="{sc}"/>' if filled else ""))
    # hydro dam on the Bow River
    p.append(label(475, 400, f'💧 HYDRO · {r["hydro"]:.0f} MW', 10.5))
    p.append(f'<rect x="446" y="408" width="50" height="26" fill="#1c2a47"/><polygon points="442,408 471,396 500,408" fill="#26375c"/>'
             + "".join(f'<rect x="{452 + i * 14}" y="414" width="8" height="14" rx="4" fill="{"#7dd3fc" if r["hydro"] > 1 else "#121a2d"}"/>' for i in range(3))
             + '<polygon points="436,434 508,434 512,446 432,446" fill="#2a3a5c"/>')
    # river
    p.append('<path d="M0,462 Q125,448 250,460 T500,458 T750,462 T1000,456 L1000,470 L0,470 Z" fill="#0f3b66" opacity="0.9"/>'
             '<path class="flow" d="M0,462 Q125,448 250,460 T500,458 T750,462 T1000,456" fill="none" stroke="#38bdf8" stroke-width="1.5" opacity="0.5"/>')
    if r["hydro"] > 1:
        for i in range(5):
            p.append(f'<line class="flow" x1="{440 + i * 15}" y1="436" x2="{440 + i * 15}" y2="446" stroke="#bae6fd" stroke-width="2" opacity="0.8"/>')
    # nuclear
    queued_n = [q for q in S["queue"] if q["kind"] == "nuclear"]
    if cap["nuclear"] > 0:
        p.append(label(715, 381, f'⚛️ NUCLEAR · {r["nuclear"]:.0f} MW', 10.5))
        ncol = mix("#1f2a4a", "#6d5bd0", r["nuc_cf"])
        p.append(f'<path d="M664,432 A24,24 0 0 1 712,432 Z" fill="{ncol}" stroke="#a78bfa" stroke-width="1"/>'
                 f'<path d="M722,432 C727,414 727,404 722,390 L750,390 C745,404 745,414 750,432 Z" fill="{ncol}" stroke="#7c6cd0" stroke-width="0.8"/>'
                 f'<path d="M756,432 C761,414 761,404 756,390 L784,390 C779,404 779,414 784,432 Z" fill="{ncol}" stroke="#7c6cd0" stroke-width="0.8"/>')
        if r["nuclear"] > 1:
            for i, cx in enumerate((736, 770)):
                p.append(f'<ellipse class="smoke" cx="{cx}" cy="388" rx="12" ry="6" fill="#cbd5e1" style="animation-delay:-{i * 1.7:.1f}s"/>')
    elif queued_n:
        left = max(0, (queued_n[0]["eta"] - S["t"]) // 24)
        p.append(f'<rect x="660" y="392" width="120" height="40" fill="none" stroke="#fbbf24" stroke-width="1.5" stroke-dasharray="5 4"/>'
                 f'<line x1="700" y1="392" x2="700" y2="372" stroke="#fbbf24" stroke-width="2"/><line x1="690" y1="374" x2="740" y2="374" stroke="#fbbf24" stroke-width="2"/>'
                 + label(720, 413, f"🚧 NUCLEAR · {left}d to go", 10.5, "#fbbf24"))
    else:
        unlocked = S["mode"] == "sandbox" or S["level"] >= BUILDS["nuclear"]["unlock"]
        p.append(f'<rect x="660" y="392" width="120" height="40" fill="none" stroke="#3b4a6b" stroke-width="1.2" stroke-dasharray="5 4"/>'
                 + label(720, 410, "Reserved site · ⚛️ Nuclear", 10, "#6b7fa3", "600")
                 + label(720, 424, "available to build" if unlocked else "🔒 unlocks at Senior Analyst", 9, "#6b7fa3", "500"))

    p.append(infra_svg(S, r))
    # bus line & feeders
    bus_col = scol if status != "Stable" else "#34d399"
    p.append('<line x1="20" y1="446" x2="980" y2="446" stroke="#243456" stroke-width="5" stroke-linecap="round"/>'
             f'<line class="flow" x1="20" y1="446" x2="980" y2="446" stroke="{bus_col}" stroke-width="2" opacity="0.8"/>')
    p.append(feeder(115, 434, 115, 446, "#fbbf24", r["solar"] > 0.5))
    p.append(feeder(215, 292, 215, 446, "#34d399", r["wind"] > 0.5))
    if r["charge"] > 0.5:
        p.append(feeder(281, 446, 281, 430, "#f472b6", True))
    else:
        p.append(feeder(281, 430, 281, 446, "#f472b6", r["battery"] > 0.5))
    p.append(feeder(470, 432, 470, 446, "#38bdf8", r["hydro"] > 0.5))
    if cap["nuclear"] > 0:
        p.append(feeder(700, 432, 700, 446, "#a78bfa", r["nuclear"] > 0.5))
    for x, f in ((385, fh), (605, fd), (790, fho), (840, fs), (935, fi)):
        col = "#34d399" if f > 0.99 else "#fbbf24" if f > 0.4 else "#ef4444"
        p.append(feeder(x, 446, x, 384, col, f > 0.05))
    p.append(f'<text x="14" y="318" font-size="10.5" font-weight="700" fill="#b6c4dd">🌬️ WIND · {r["wind"]:.0f} MW</text>')
    p.append(f'<text x="990" y="458" font-size="10" fill="#7f93b5" text-anchor="end">Bow River</text>')

    # HUD: event effects + banner, weather & time, alerts
    p.append(fx_front(S, r, B, dayf, night, evk))
    wicon = "☁️" if cover > 0.7 else ("🌙" if night > 0.5 else "☀️")
    p.append(f'<rect x="690" y="12" width="298" height="25" rx="12" fill="#000" opacity="0.5"/>'
             f'<text x="839" y="29" font-size="12.5" fill="#fff" text-anchor="middle" font-weight="700">{wicon} Day {r["day"] + 1} · {fmt_hour(r["hour"])} · {r["temp"]:.0f}°C · wind {r["wind_ms"]:.0f} m/s</text>')
    if status == "BLACKOUT":
        p.append(f'<rect x="330" y="50" width="340" height="30" rx="8" fill="#7f1d1d" opacity="0.92" class="pulse"/>'
                 f'<text x="500" y="71" font-size="14" fill="#fff" text-anchor="middle" font-weight="800">⚠ ROLLING BLACKOUTS · {r["unserved"]:.0f} MW SHORT</text>')
    elif status in ("Critical", "Tight"):
        p.append(f'<rect x="400" y="50" width="200" height="26" rx="8" fill="#78350f" opacity="0.85"/>'
                 f'<text x="500" y="68" font-size="12.5" fill="#fde68a" text-anchor="middle" font-weight="700">⚠ GRID {status.upper()}</text>')
    p.append("</svg>")
    return "".join(p)


# Locks every looping animation to the wall clock, so when the picture refreshes
# (once per game hour) clouds, turbines, rain, etc. carry on smoothly instead of restarting.
SYNC_JS = """<script>
(function(){
  var now = Date.now();
  try{ document.getAnimations().forEach(function(a){
    try{ var t = a.effect.getComputedTiming();
         if (t.iterations === Infinity && isFinite(t.duration) && t.duration > 0){ a.currentTime = now; } }catch(e){}
  }); }catch(e){}
  try{ var svg = document.querySelector('svg'); if (svg && svg.setCurrentTime){ svg.setCurrentTime((now % 600000) / 1000); } }catch(e){}
})();
</script>"""


def render_city(S, height=560):
    doc = (f"<!doctype html><html><head><meta charset='utf-8'><style>html{{background:#0b1220}}{SVG_CSS}</style></head>"
           f"<body>{city_svg(S)}{SYNC_JS}</body></html>")
    try:
        if hasattr(st, "iframe"):          # newer Streamlit (components.html is being retired)
            st.iframe(doc, height=height)
        else:                              # older Streamlit
            components.html(doc, height=height)
    except Exception:
        st.markdown(city_svg(S), unsafe_allow_html=True)


# =============================================================================
# 7. DASHBOARD PIECES
# =============================================================================
def control_room_html(S):
    r, cap = S["last"], S["cap"]
    rows = [("⚛️ Nuclear", "nuclear", cap["nuclear"], "#a78bfa"), ("💧 Hydro", "hydro", cap["hydro"], "#38bdf8"),
            ("🌬️ Wind", "wind", cap["wind"], "#34d399"), ("☀️ Solar", "solar", cap["solar"], "#fbbf24"),
            ("🔥 Natural gas", "gas", r["gas_av"], "#94a3b8"), ("🔌 Imports", "imports", r["imp_cap"], "#fb923c")]
    out = ['<div class="panel"><h4>🖥️ Control Room · Generation Right Now</h4><div class="muted">MW being produced / MW available</div>']
    for lab, k, c, col in rows:
        if k == "nuclear" and c <= 0:
            out.append(f'<div class="kv"><span>{lab}</span><span class="muted">not built</span></div>')
            continue
        val = r[k]
        out.append(f'<div class="kv"><span>{lab}</span><span><b>{val:,.0f}</b> / {c:,.0f} MW</span></div>'
                   f'<div class="bar"><div style="width:{clamp(val / max(c, 1), 0, 1) * 100:.0f}%;background:{col}"></div></div>')
    mode = f'charging +{r["charge"]:,.0f} MW' if r["charge"] > 0.5 else (f'discharging {r["battery"]:,.0f} MW' if r["battery"] > 0.5 else "idle")
    sc = "#34d399" if r["soc_frac"] > 0.5 else "#fbbf24" if r["soc_frac"] > 0.2 else "#f87171"
    out.append(f'<div class="kv"><span>🔋 Battery · {mode}</span><span><b>{r["soc"]:,.0f}</b> / {cap["battery_mwh"]:,.0f} MWh</span></div>'
               f'<div class="bar"><div style="width:{r["soc_frac"] * 100:.0f}%;background:{sc}"></div></div>')
    if r["curtailed"] > 0.5:
        out.append(f'<div class="kv"><span style="color:#fbbf24">♻️ Clean power wasted</span><span><b>{r["curtailed"]:,.0f} MW</b></span></div>')
    price = "🔥 999 (cap)" if r["price"] >= 999 else f"{r['price']:,.0f}"
    clean_pct = r["clean"] / max(r["served"], 1) * 100
    fcol = "#f87171" if r["freq"] < 59.9 else "#e2e8f0"
    out.append('<hr style="border:none;border-top:1px solid #1f2c47;margin:12px 0 2px 0">')
    out.append(f'<div class="kv"><span>Grid frequency</span><b style="color:{fcol}">{r["freq"]:.2f} Hz</b></div>'
               f'<div class="kv"><span>Reserve margin</span><b>{r["reserve"] * 100:+.0f}%</b></div>'
               f'<div class="kv"><span>Pool price</span><b>&#36;{price} /MWh</b></div>'
               f'<div class="kv"><span>Clean share now</span><b>{clean_pct:.0f}%</b></div>'
               f'<div class="kv"><span>Carbon intensity</span><b>{r["ci"]:.0f} g/kWh</b></div>'
               f'<div class="kv"><span>Population</span><b>{S["pop"]:.3f} M</b></div>'
               f'<div class="kv"><span>Public satisfaction</span><b>{S["satisfaction"]:.0f}%</b></div>'
               f'<div class="bar"><div style="width:{S["satisfaction"]:.0f}%;background:{score_color(S["satisfaction"])}"></div></div></div>')
    return H("".join(out))


def hist_df(S, hours):
    return pd.DataFrame(S["hist"][-hours:])


def style(ch):
    return (ch.configure(background="#0e1627")
            .configure_axis(labelColor="#9fb0cc", titleColor="#9fb0cc", gridColor="#1b2842", domainColor="#2b3b5e", tickColor="#2b3b5e")
            .configure_legend(labelColor="#cbd5e1", titleColor="#cbd5e1", orient="top")
            .configure_view(stroke=None).configure_title(color="#e2e8f0"))


TAX = alt.Axis(format="%b %d %H:00", labelAngle=-25, title=None)


def chart_demand_supply(df):
    dom = ["City needs", "Delivered to city", "You can make"]
    long = df.melt(id_vars=["time"], value_vars=["demand", "served", "available"], var_name="series", value_name="MW")
    long["series"] = long["series"].map(dict(demand=dom[0], served=dom[1], available=dom[2]))
    lines = alt.Chart(long).mark_line(strokeWidth=2.2).encode(
        x=alt.X("time:T", axis=TAX), y=alt.Y("MW:Q", scale=alt.Scale(zero=False)),
        color=alt.Color("series:N", scale=alt.Scale(domain=dom, range=["#f8fafc", "#34d399", "#38bdf8"]), legend=alt.Legend(title=None)),
        strokeDash=alt.StrokeDash("series:N", scale=alt.Scale(domain=dom, range=[[1, 0], [1, 0], [5, 4]]), legend=None),
        tooltip=[alt.Tooltip("time:T", title="Time", format="%b %d, %H:00"), "series:N", alt.Tooltip("MW:Q", format=",.0f")])
    bo = alt.Chart(df[df["unserved"] > 0.5]).mark_rule(color="#ef4444", opacity=0.35, strokeWidth=5).encode(x="time:T")
    return style((bo + lines).properties(height=290, title="Power the city needs vs power you can make (red = blackout)"))


def chart_mix(df):
    names = [n for _, n, _ in SOURCES]
    long = df.melt(id_vars=["time"], value_vars=[k for k, _, _ in SOURCES], var_name="k", value_name="MW")
    kmap = {k: n for k, n, _ in SOURCES}
    rank = {k: i for i, (k, _, _) in enumerate(SOURCES)}
    long["Source"], long["rank"] = long["k"].map(kmap), long["k"].map(rank)
    area = alt.Chart(long).mark_area(opacity=0.9).encode(
        x=alt.X("time:T", axis=TAX), y=alt.Y("MW:Q", stack="zero"),
        color=alt.Color("Source:N", scale=alt.Scale(domain=names, range=[c for _, _, c in SOURCES]), legend=alt.Legend(title=None)),
        order=alt.Order("rank:Q"), tooltip=["Source:N", alt.Tooltip("MW:Q", format=",.0f")])
    dem = alt.Chart(df).mark_line(color="#ffffff", strokeDash=[4, 3], strokeWidth=1.6).encode(x="time:T", y="demand:Q")
    return style((area + dem).properties(height=280, title="Where your power comes from (dashed line = what the city needs)"))


def chart_donut(df):
    tot = pd.DataFrame([dict(Source=n, MWh=float(df[k].sum())) for k, n, _ in SOURCES])
    ch = alt.Chart(tot).mark_arc(innerRadius=55).encode(
        theta="MWh:Q", color=alt.Color("Source:N", scale=alt.Scale(domain=[n for _, n, _ in SOURCES], range=[c for _, _, c in SOURCES]), legend=None),
        tooltip=["Source:N", alt.Tooltip("MWh:Q", format=",.0f")])
    return style(ch.properties(height=280, title="Share of power by source"))


def window_hours(key):
    return {"Last 24 hours": 24, "Last 3 days": 72, "Last 7 days": 168}[st.session_state.get(key, "Last 3 days")]


# =============================================================================
# 8. SIMPLE-VIEW HELPERS (plain English for new players)
# =============================================================================
EXTRA_CSS = """
<style>
.coach{border:2px solid;border-radius:16px;padding:14px 20px;margin:4px 0 10px 0}
.coach-step{font-size:.8rem;font-weight:800;letter-spacing:.14em}
.coach-main{font-size:1.55rem;font-weight:800;color:#f8fafc;line-height:1.25;margin-top:2px}
.coach-sub{font-size:1.02rem;color:#cbd5e1;margin-top:4px}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin:2px 0 6px 0}
.chip{padding:5px 14px;border-radius:999px;border:1px solid #2b3b5e;color:#7f93b5;font-size:.88rem;font-weight:600}
.chip.on{background:#38bdf8;border-color:#38bdf8;color:#06121f;font-weight:800}
.meter{position:relative;height:30px;background:#16213a;border-radius:15px;margin:34px 0 6px 0;border:1px solid #243456}
.meter .fill{position:absolute;left:0;top:0;bottom:0;border-radius:15px;transition:width .5s}
.meter .mark{position:absolute;top:-8px;bottom:-8px;width:4px;background:#fff;border-radius:2px;box-shadow:0 0 8px #fff}
.meter .mlabel{position:absolute;top:-28px;transform:translateX(-50%);font-size:.85rem;font-weight:800;color:#fff;white-space:nowrap}
.meter-legend{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;font-size:.84rem;color:#8aa0c0;margin-bottom:6px}
.evcard{border:2px solid;border-radius:16px;padding:12px 18px;margin:8px 0}
.evcard h3{margin:0 0 4px 0;font-size:1.35rem}
.evrow{margin-top:5px;font-size:1.02rem;color:#e2e8f0}
.evrow b{color:#fff}
.src{display:inline-flex;align-items:center;gap:6px;background:#111a30;border:1px solid #1f2c47;border-radius:10px;padding:4px 10px;margin:3px 6px 3px 0;font-size:.88rem;color:#cbd5e1}
.src b{color:#f8fafc}
.src i{width:10px;height:10px;border-radius:50%;display:inline-block}
.step-card{background:linear-gradient(180deg,#131d36,#0f1729);border:1px solid #1f2c47;border-radius:16px;padding:16px 18px;height:100%}
.step-card .n{font-size:.8rem;font-weight:800;letter-spacing:.12em;color:#38bdf8}
.step-card .t{font-size:1.25rem;font-weight:800;color:#f8fafc;margin:2px 0}
.step-card .d{font-size:.98rem;color:#b6c4dd}
[data-testid="stBaseButton-primary"],button[kind="primary"]{background:#16a34a!important;border-color:#16a34a!important;color:#fff!important;font-size:1.2rem!important;font-weight:800!important;padding:.65rem 1rem!important}
[data-testid="stBaseButton-primary"]:hover,button[kind="primary"]:hover{background:#15803d!important;border-color:#15803d!important}
</style>
"""

FRIENDLY = {"Stable": ("💡", "LIGHTS ON"), "Tight": ("⚠️", "GETTING TIGHT"),
            "Critical": ("🔶", "DANGER"), "BLACKOUT": ("🔴", "BLACKOUT")}

SIMPLE = {
    "solar": ("Free power from the sun", "✅ Cheap and clean · ❌ Nothing at night", "+50 MW in daytime"),
    "wind": ("Power from the wind", "✅ Clean · ❌ Stops when the wind stops", "+50 MW when windy"),
    "battery": ("Saves extra power for later", "✅ Fixes the night problem · ❌ Costly", "Holds about 4 hours of power"),
    "nuclear": ("Strong, steady power 24/7", "✅ Always on, no CO₂ · ❌ Costly and slow", "+150 MW any time"),
}


def status_html(r):
    name, _, col = grid_status(r)
    ic, txt = FRIENDLY[name]
    if r["unserved"] > 0.5:
        sub = f'The city is {r["unserved"]:,.0f} MW short. Lights are going out!'
    elif r["reserve"] >= 0:
        sub = f'You can make {r["reserve"] * 100:.0f}% more power than the city needs'
    else:
        sub = "Almost out of spare power"
    return H(f'<div class="status" style="background:{col}22;border:1px solid {col};color:{col}">'
             f'<span style="font-size:1.35rem">{ic} {txt}</span><span style="font-size:.95rem;font-weight:600">{sub}</span></div>')


def meter_html(r):
    top = max(r["available"], r["demand"]) * 1.15
    av = clamp(r["available"] / top * 100, 0, 100)
    dm = clamp(r["demand"] / top * 100, 0, 100)
    ok = r["available"] >= r["demand"]
    fill = "linear-gradient(90deg,#0ea5e9,#38bdf8)" if ok else "linear-gradient(90deg,#b91c1c,#ef4444)"
    verdict = "✅ Blue is past the white line: safe" if ok else "❌ Blue is short of the white line: blackout"
    return H(f'<div class="meter"><div class="fill" style="width:{av:.1f}%;background:{fill}"></div>'
             f'<div class="mark" style="left:{dm:.1f}%"></div><div class="mlabel" style="left:{dm:.1f}%">⚡ City needs {r["demand"]:,.0f} MW</div></div>'
             f'<div class="meter-legend"><span>🔵 Blue bar = power you can make right now ({r["available"]:,.0f} MW)</span><span>{verdict}</span></div>')


def sources_html(r):
    items = [("☀️ Solar", r["solar"], "#fbbf24"), ("🌬️ Wind", r["wind"], "#34d399"), ("💧 Hydro", r["hydro"], "#38bdf8"),
             ("⚛️ Nuclear", r["nuclear"], "#a78bfa"), ("🔥 Gas", r["gas"], "#94a3b8"), ("🔌 Imports", r["imports"], "#fb923c")]
    out = [f'<span class="src"><i style="background:{c}"></i>{n} <b>{v:,.0f} MW</b></span>' for n, v, c in items if v > 0.5 or n.endswith("Solar")]
    if r["battery"] > 0.5:
        out.append(f'<span class="src"><i style="background:#f472b6"></i>🔋 Battery giving <b>{r["battery"]:,.0f} MW</b></span>')
    elif r["charge"] > 0.5:
        out.append(f'<span class="src"><i style="background:#f472b6"></i>🔋 Battery filling <b>{r["charge"]:,.0f} MW</b></span>')
    out.append(f'<span class="src"><i style="background:#f472b6"></i>🔋 Battery <b>{r["soc_frac"] * 100:.0f}% full</b></span>')
    if r["curtailed"] > 0.5:
        out.append(f'<span class="src" style="border-color:#fbbf24"><i style="background:#fbbf24"></i>♻️ Wasted clean power <b>{r["curtailed"]:,.0f} MW</b></span>')
    return H('<div>' + "".join(out) + '</div>')


def coach(S, r):
    """One plain-English instruction for what the player should do right now."""
    started = S["t"] > 1 or S["running"]
    if S["over"]:
        return 3, "#ef4444", "GAME OVER", "The city lost confidence in you.", "Press ↺ New game to try again. Every blackout teaches you something!"
    if not started:
        return 1, "#38bdf8", "STEP 1", "Press the big green ▶ START button", "The clock is paused. Nothing happens until you press it. Take a look around the city first!"
    if r["unserved"] > 0.5:
        return 3, "#ef4444", "FIX IT NOW", "Not enough power. The lights are going out!", \
            "Use a quick fix in “Your Move” below: buy power from neighbours, ask people to save power, or pay factories to cut back."
    if S["events"]:
        ev = max(S["events"], key=lambda e: e["start"])
        pl = EVENT_PLAIN[ev["key"]]
        col = "#34d399" if pl["good"] else "#fb923c"
        return 3, col, "EVENT", f'{EVENTS[ev["key"]]["icon"]} {pl["title"]}: {pl["what"]}', "Read the box below, then pick a fix in “Your Move”."
    if r["reserve"] < 0.12:
        return 2, "#fbbf24", "WATCH OUT", "Spare power is running low.", "Keep the blue bar well past the white line. Build more power, or get a battery ready."
    if not S["running"]:
        return 2, "#38bdf8", "PAUSED", "Press ▶ CONTINUE to keep going.", "Or build something first. Things take days to finish, so plan ahead."
    if not S.get("builds"):
        return 2, "#34d399", "STEP 2", "Keep the blue bar past the white line.", \
            "That means you make more power than the city needs. When you're ready, build something below. Try a 🔋 battery!"
    if r["curtailed"] > 1:
        return 2, "#34d399", "ALL GOOD", "You're making extra clean power, and some is being wasted.", "That's the storage problem! A 🔋 battery could save it for tonight."
    return 2, "#34d399", "ALL GOOD", "The lights are on. Get ready for the next problem.", "Weather events can hit any time. Batteries and spare power keep you safe."


def coach_html(S, r):
    n, col, step, head, sub = coach(S, r)
    labels = ["1 ▶ Press START", "2 👀 Keep power high enough", "3 🛠️ Fix problems"]
    chips = '<div class="chips">' + "".join(f'<span class="chip{" on" if i + 1 == n else ""}">{t}</span>' for i, t in enumerate(labels)) + '</div>'
    box = (f'<div class="coach" style="border-color:{col};background:{col}1f"><div class="coach-step" style="color:{col}">{step}</div>'
           f'<div class="coach-main">{head}</div><div class="coach-sub">{sub}</div></div>')
    return H(chips + box)


def event_cards_html(S):
    out = []
    for ev in sorted(S["events"], key=lambda e: -e["start"]):
        pl = EVENT_PLAIN[ev["key"]]
        col = "#34d399" if pl["good"] else "#fb923c"
        loss = f'{ev["loss"]:.0f}'
        left = max(0, ev["end"] - S["t"])
        out.append(H(f'<div class="evcard" style="border-color:{col};background:{col}1a">'
                     f'<h3>{EVENTS[ev["key"]]["icon"]} {pl["title"]} <span class="muted" style="font-weight:600">· {left} hours left</span></h3>'
                     f'<div class="evrow"><b>What is happening:</b> {pl["what"]}</div>'
                     f'<div class="evrow"><b>What it does:</b> {pl["does"].format(loss=loss)}</div>'
                     f'<div class="evrow"><b>What you can do:</b> {pl["fix"]}</div></div>'))
    return "".join(out)


def dr_button(kind, text):
    spec = DR[kind]
    S = G()
    active = S["dr_until"][kind] > S["t"]
    wait = S["dr_ready"][kind] - S["t"]
    if active:
        lab, dis = f"{text} · working ({S['dr_until'][kind] - S['t']}h left)", True
    elif wait > 0:
        lab, dis = f"{text} · ready in {wait}h", True
    else:
        lab, dis = text + (f" · ${spec['cost']}M" if spec["cost"] else " · free"), S["cash"] < spec["cost"]
    btn(lab, f"dr_{kind}", on_click=cb_dr, args=(kind,), disabled=dis or bool(S["over"]))


MODEL_TEXT = """
- **Demand:** a daily shape (morning ramp, evening peak) × population, plus **heating** below 15 °C (5.5 MW per °C) and **cooling** above 21 °C (8 MW per °C).
- **Solar:** real sun geometry for 51° N (day length and sun angle by season) × random daily cloudiness × events.
- **Wind:** a persistent random wind speed (windier in winter) through a turbine power curve (starts at 2.5 m/s, full power near 10 m/s, shuts off at 25 m/s).
- **Hydro:** run-of-river, seasonal. **Nuclear:** 95% availability. **Gas:** dispatchable, weaker in extreme heat or cold.
- **Dispatch order:** clean power first, then charge batteries with any surplus (waste the rest), then gas, batteries (per your strategy), imports, and finally blackouts.
- **Batteries:** 94% efficient each way (88% round trip), limited by both power (MW) and energy (MWh).
- **Blackouts:** the least critical districts are cut first (industry, homes, downtown, schools). The hospital has backup generators.
- **Money:** revenue per MWh delivered. Costs for gas fuel, carbon (\\$80 per tonne), imports, upkeep and blackout penalties. Build costs are scaled down for gameplay.

**Simplifications:** Calgary is one region with a simplified fleet. No transmission congestion, reactive power or unit-commitment detail.
"""


# =============================================================================
# 9. TITLE SCREEN
# =============================================================================
def splash():
    if "_preview" not in st.session_state:
        st.session_state["_preview"] = new_game(list(START_OPTIONS)[0], "Career mode")
    st.markdown(H('<div style="text-align:center;padding:6px 0 0 0"><div class="muted" style="letter-spacing:.25em">AN INTERACTIVE POWER GRID GAME</div>'
                  '<div class="big-title">⚡ Can You Power Calgary?</div>'
                  '<div class="hero-sub">You run the power grid for 1.4 million people. <b>Keep the lights on.</b></div></div>'),
                unsafe_allow_html=True)
    render_city(st.session_state["_preview"], height=520)
    c1, c2, c3 = st.columns(3)
    c1.markdown(H('<div class="step-card"><div class="n">STEP 1</div><div class="t">▶ Press START</div><div class="d">The city is paused until you are ready.</div></div>'), unsafe_allow_html=True)
    c2.markdown(H('<div class="step-card"><div class="n">STEP 2</div><div class="t">👀 Keep the lights on</div><div class="d">Make more power than the city needs. Watch the blue bar.</div></div>'), unsafe_allow_html=True)
    c3.markdown(H('<div class="step-card"><div class="n">STEP 3</div><div class="t">🛠️ Fix problems</div><div class="d">Storms and heat waves hit. Build power, buy power, or ask people to save it.</div></div>'), unsafe_allow_html=True)
    st.write("")
    a, b = st.columns([1, 1])
    with a:
        st.radio("Game mode", ["Career mode", "Sandbox (everything unlocked, big budget)"], key="setup_mode",
                 help="Career: start as a Junior Grid Operator and earn promotions. Sandbox: try anything.")
        with st.expander("Choose the season (optional)"):
            st.selectbox("When does your shift start?", list(START_OPTIONS), key="setup_season")
    with b:
        st.write("")
        btn("▶  Start Simulation", "start_btn", on_click=cb_start, type="primary")
    st.info("**The big idea:** making clean energy is easy. **Storing it** until people need it is the hard part. Play to find out why.")


# =============================================================================
# 10. MAIN APP
# =============================================================================
st.markdown(CSS + EXTRA_CSS, unsafe_allow_html=True)
S = G()
if S is None:
    splash()
    st.stop()
sync_settings()
over = bool(S["over"])

# ---------------------------- settings (hidden sidebar) ----------------------
with st.sidebar:
    st.markdown("### ⚙️ Settings")
    st.toggle("Random events", value=True, key="opt_events", help="Turn off to practise without surprises.")
    st.toggle("Pause when an event hits", value=True, key="opt_autopause", help="The game stops so you can react.")
    a, b = st.columns(2)
    with a:
        btn("+1 hour", "s1", on_click=cb_step, args=(1,), disabled=over)
    with b:
        btn("+1 week", "s168", on_click=cb_step, args=(168,), disabled=over)
    st.divider()
    st.markdown("**🎬 Show an event**")
    st.selectbox("Event", list(EVENTS), key="test_event", format_func=lambda k: f"{EVENTS[k]['icon']} {EVENT_PLAIN[k]['title'].title()}")
    btn("Trigger it now", "ev_btn", on_click=cb_event, disabled=over)
    st.divider()
    with st.expander("📖 Words you might see"):
        st.markdown("**MW (megawatt)** = how much power. 1 MW powers about 1,000 homes.\n\n**Demand** = power the city is using.\n\n"
                    "**Spare power** = how much extra you can make above demand.\n\n**Frequency** = the grid's heartbeat (60 Hz). It drops when you are short.\n\n"
                    "**Wasted clean power** = wind/solar power nobody could use or store.")

# ---------------------------- title + controls -------------------------------
# (These sit OUTSIDE the live area, so pressing them restarts the game clock properly.)
st.markdown(H('<div class="hero-title">⚡ Can You Power Calgary?</div>'
              '<div class="hero-sub">Keep the power you can make <b>higher</b> than what the city needs.</div>'), unsafe_allow_html=True)

speed_names = list(SPEEDS)
ctl = st.columns([1.5, 1.3, 1.1, 1])
with ctl[0]:
    if S["running"]:
        run_label = "⏸ PAUSE"
    else:
        run_label = "▶ START" if S["t"] <= 1 else "▶ CONTINUE"
    btn(run_label, "run_btn", on_click=cb_toggle_run, type="primary", disabled=over)
with ctl[1]:
    st.selectbox("Speed", speed_names, index=0, key="speed_choice", label_visibility="collapsed")
with ctl[2]:
    btn("⏭ Skip 1 day", "s24", on_click=cb_step, args=(24,), disabled=over)
with ctl[3]:
    btn("↺ New game", "restart_btn", on_click=cb_restart)

speed_at_def = st.session_state.get("speed_choice", speed_names[0])
hrs, secs = SPEEDS.get(speed_at_def, SPEEDS[speed_names[0]])
st.caption(f"⏱ In the game, 1 hour takes {secs:g} seconds of real time, so one full day takes about {24 * secs:.0f} seconds. Use the speed menu to change it.")
run_every = secs if (S["running"] and not S["over"]) else None


# ---------------------------- the live game area -----------------------------
# Only this area refreshes while the clock runs, so the rest of the page never flickers.
@st.fragment(run_every=run_every)
def live():
    S = G()
    if S is None:
        st.rerun()
        return
    sync_settings()
    if run_every is not None:
        if st.session_state.get("speed_choice", speed_names[0]) != speed_at_def:
            st.rerun()                              # speed changed: restart the timer at the new pace
        if S["running"] and not S["over"]:
            advance(S, hrs, stop_on_alert=S["autopause"])
        if not S["running"] or S["over"]:
            st.rerun()                              # paused / event / game over: stop the timer
    r = S["last"]
    over = bool(S["over"])
    L = LEVELS[S["level"]]
    st_name, st_icon, st_col = grid_status(r)

    # ---- who you are + goal ----
    g1, g2 = st.columns([1.1, 2])
    with g1:
        who = "Sandbox mode" if S["mode"] == "sandbox" else f'{L["badge"]} Level {S["level"]} · {L["title"]}'
        st.markdown(H(f'<div class="panel" style="padding:8px 14px"><div class="muted">YOU ARE</div><div style="font-weight:800">{who}</div></div>'), unsafe_allow_html=True)
    with g2:
        if S["mode"] == "career":
            for lab, cur, tgt, hi in goal_status(S)[0]:
                frac = clamp(cur / tgt, 0, 1) if hi else (clamp(tgt / max(cur, 1), 0, 1) if cur > 0 else 0.0)
                st.progress(frac, text=f"🎯 Goal: {lab}: {cur:.0f} / {'≥' if hi else '≤'} {tgt:.0f}")

    # ---- banners ----
    if S["over"]:
        why = ("City council lost confidence in you after repeated blackouts." if S["over"] == "fired" else "The city budget ran out.")
        st.error(f"🛑 **GAME OVER.** {why} You lasted {r['day'] + 1} days with {S['blackout_hours']} blackout hours.")
    if S["won"]:
        st.success("🏆 **MISSION COMPLETE!** Calgary has a reliable, cleaner grid because of you.")
        if not S["balloons"]:
            S["balloons"] = True
            st.balloons()
    if S["promo"]:
        st.success(md_safe(S["promo"]))

    # ---- what to do now ----
    st.markdown(coach_html(S, r), unsafe_allow_html=True)

    # ---- the four numbers that matter ----
    cols = st.columns(4)
    cols[0].markdown(card("⚡ City needs", f'{r["demand"]:,.0f} MW', "power the city is using"), unsafe_allow_html=True)
    cols[1].markdown(card("🏭 You can make", f'{r["available"]:,.0f} MW', "power ready to use", st_col, st_col if st_name != "Stable" else None), unsafe_allow_html=True)
    cols[2].markdown(card("💰 Money", f'&#36;{S["cash"]:,.0f}M', f'{"+" if S["month_net"] >= 0 else "−"}&#36;{abs(S["month_net"]):.1f}M this month',
                          "#34d399" if S["cash"] > 40 else "#fbbf24" if S["cash"] > 0 else "#f87171"), unsafe_allow_html=True)
    cols[3].markdown(card("😊 Happiness", f'{S["satisfaction"]:.0f}%', "of Calgarians", score_color(S["satisfaction"])), unsafe_allow_html=True)
    st.markdown(meter_html(r), unsafe_allow_html=True)
    st.markdown(status_html(r), unsafe_allow_html=True)

    if S["events"]:
        st.markdown(event_cards_html(S), unsafe_allow_html=True)

    # ---- the city ----
    render_city(S)
    st.markdown(sources_html(r), unsafe_allow_html=True)
    st.caption("MW = megawatts, a way to measure power. 1 MW is enough for about 1,000 homes.")

    # ---- your move ----
    st.markdown("## 🛠️ Your Move")
    st.markdown("**Build power plants.** They take days to finish, so plan ahead!")
    build_cols = st.columns(4)
    for col, kind in zip(build_cols, ["solar", "wind", "battery", "nuclear"]):
        spec = BUILDS[kind]
        tagline, goodbad, size = SIMPLE[kind]
        locked = S["mode"] == "career" and S["level"] < spec["unlock"]
        in_q = sum(1 for q in S["queue"] if q["kind"] == kind)
        with col:
            with st.container(border=True):
                st.markdown(f"#### {spec['icon']} {spec['label']}")
                st.markdown(f"**{tagline}**  \n{goodbad}")
                st.caption(f"{size} · takes {spec['days']} days" + (f" · 🚧 {in_q} being built" if in_q else ""))
                if locked:
                    btn("🔒 Unlocks at Level 2", f"b_{kind}", disabled=True)
                else:
                    btn(f"Build · ${spec['cost']}M", f"b_{kind}", on_click=cb_build, args=(kind,), disabled=S["cash"] < spec["cost"] or over)

    st.markdown("**Quick fixes.** These work right away.")
    q = st.columns(3)
    with q[0]:
        with st.container(border=True):
            st.markdown("#### 🔌 Buy power")
            st.slider("From neighbours (MW)", 0, 300, key="import_slider")
            eff = min(S["import_limit"], S["import_max"])
            st.caption(f"Costs extra money. You can buy up to {eff} MW right now.")
    with q[1]:
        with st.container(border=True):
            st.markdown("#### 📣 Ask people to save power")
            dr_button("appeal", "📣 Make an appeal")
            st.caption("City needs about 3% less for 6 hours.")
    with q[2]:
        with st.container(border=True):
            st.markdown("#### 🏭 Pay factories to cut back")
            dr_button("dr", "🏭 Cut back")
            st.caption("City needs about 8% less for 4 hours.")

    with st.expander("More options (for experts)"):
        x1, x2 = st.columns(2)
        with x1:
            st.markdown("**🔋 How should the battery be used?**")
            st.radio("Battery use", ["Peak shaving: save charge for the crunch", "Use ASAP: maximise clean energy"], key="batt_strategy", label_visibility="collapsed")
            st.toggle("Charge batteries from the gas grid when there is spare capacity", key="grid_charge",
                      help="Lets storage fill up overnight with no solar or wind surplus, but burns gas.")
            st.caption(f"Battery: {S['cap']['battery_mw']:.0f} MW / {S['cap']['battery_mwh']:.0f} MWh · {S['soc']:.0f} MWh stored")
        with x2:
            st.markdown("**🔌 Bigger power line to neighbours**")
            pend = any(item["kind"] == "intertie" for item in S["queue"])
            btn(f"Upgrade the line (+50 MW) · ${BUILDS['intertie']['cost']}M" + (" · 🚧 building" if pend else ""), "b_intertie",
                on_click=cb_build, args=("intertie",), disabled=S["cash"] < BUILDS["intertie"]["cost"] or pend or over)
            st.caption(f"The line can carry up to {S['import_max']} MW.")

    # ---- graphs (hidden until asked for, to keep the screen simple) ----
    st.toggle("📈 Show graphs and analyst numbers", key="show_graphs")
    if st.session_state.get("show_graphs"):
        tabs = st.tabs(["📈 Today's grid", "🔋 Why storage matters", "🔬 Analyst view"])

        with tabs[0]:
            st.radio("Time window", ["Last 24 hours", "Last 3 days", "Last 7 days"], index=0, horizontal=True, key="win_live")
            df = hist_df(S, window_hours("win_live"))
            show_chart(chart_demand_supply(df))
            st.caption("When the white line goes above the blue dashed line, the city needs more power than you can make. That's a blackout (red).")
            c1, c2 = st.columns([2, 1])
            with c1:
                show_chart(chart_mix(df))
            with c2:
                show_chart(chart_donut(df))

        with tabs[1]:
            st.markdown("### 🔋 Why storage is the hard part")
            st.markdown("Solar and wind make power at the **wrong times**: lots at noon, none at night, almost none when it's calm. "
                        "**Batteries save extra power for later.**")
            df = hist_df(S, 168)
            wasted, shifted = float(df["curtailed"].sum()), float(df["battery"].sum())
            low_hours = df[df["variable"] < 0.1 * df["demand"]]
            gas_low = float(low_hours["gas"].sum())
            m1, m2, m3 = st.columns(3)
            m1.metric("Clean power wasted (7 days)", f"{wasted:,.0f} MWh", help="Wind and solar power that nobody could use or store.")
            m2.metric("Gas burned when sun and wind were low", f"{gas_low:,.0f} MWh")
            m3.metric("Power moved by batteries", f"{shifted:,.0f} MWh")
            if wasted > 20 and gas_low > 20:
                st.success(f"**That's the storage gap.** You threw away **{wasted:,.0f} MWh** of clean power, then burned **{gas_low:,.0f} MWh** of gas "
                           f"(about **{gas_low * GAS_CO2:,.0f} tonnes of CO₂**) when the sun and wind weren't working. A battery bridges those two moments.")
            elif wasted > 20:
                st.info(f"You wasted **{wasted:,.0f} MWh** of clean power this week. More batteries would let you use it later.")
            else:
                st.info("Build more solar and wind, then watch for times when you make more clean power than the city needs. That's wasted power waiting for a battery.")
            df3 = hist_df(S, 72)
            dl = df3.melt(id_vars=["time"], value_vars=["demand", "variable"], var_name="k", value_name="MW")
            dl["Series"] = dl["k"].map(dict(demand="City needs", variable="Wind + solar making"))
            ch = alt.Chart(dl).mark_line(strokeWidth=2.2).encode(
                x=alt.X("time:T", axis=TAX), y=alt.Y("MW:Q"),
                color=alt.Color("Series:N", scale=alt.Scale(domain=["City needs", "Wind + solar making"], range=["#f8fafc", "#34d399"]), legend=alt.Legend(title=None)))
            show_chart(style(ch.properties(height=270, title="The power the city needs doesn't match the power wind and solar make")))
            st.markdown("> **The hard problem isn't finding energy. It's storing and delivering it when people need it.**")

        with tabs[2]:
            st.caption("The real numbers a grid analyst watches.")
            k = st.columns(5)
            k[0].markdown(card("🛡️ Grid stability", f'{r["stability"]:.0f}/100', "100 = rock solid", score_color(r["stability"])), unsafe_allow_html=True)
            k[1].markdown(card("🌍 Emissions score", f'{r["em_score"]:.0f}/100', f'{r["ci"]:.0f} g CO₂ per kWh', score_color(r["em_score"])), unsafe_allow_html=True)
            k[2].markdown(card("〰️ Frequency", f'{r["freq"]:.2f} Hz', "should stay at 60", "#f87171" if r["freq"] < 59.9 else "#e2e8f0"), unsafe_allow_html=True)
            k[3].markdown(card("🧮 Reserve margin", f'{r["reserve"] * 100:+.0f}%', "spare power vs demand"), unsafe_allow_html=True)
            k[4].markdown(card("💵 Pool price", "&#36;999 cap" if r["price"] >= 999 else f'&#36;{r["price"]:,.0f}/MWh', "wholesale power price"), unsafe_allow_html=True)
            st.write("")
            an1, an2 = st.columns([1, 1.6])
            with an1:
                st.markdown(control_room_html(S), unsafe_allow_html=True)
            with an2:
                st.radio("Time window", ["Last 24 hours", "Last 3 days", "Last 7 days"], index=1, horizontal=True, key="win_em")
                dfa = hist_df(S, window_hours("win_em")).copy()
                dfa["ci_roll"] = dfa["ci"].rolling(6, min_periods=1).mean()
                for c in ("cost_fuel", "cost_imp", "cost_om", "cost_pen", "revenue"):
                    dfa[c + "_k"] = dfa[c] / 1000.0
                base = alt.Chart(dfa).mark_area(color="#94a3b8", opacity=0.25, line={"color": "#fb923c"}).encode(
                    x=alt.X("time:T", axis=TAX), y=alt.Y("ci_roll:Q", title="g CO₂ per kWh"),
                    tooltip=[alt.Tooltip("time:T", format="%b %d, %H:00"), alt.Tooltip("ci_roll:Q", format=".0f")])
                rule = alt.Chart(pd.DataFrame({"y": [250]})).mark_rule(color="#34d399", strokeDash=[5, 4]).encode(y="y:Q")
                show_chart(style((base + rule).properties(height=250, title="Emissions over time (green line = Level 3 target)")))
                st.caption(f"Total CO₂ so far: **{S['co2_total']:,.0f} tonnes**.")
                cost_long = dfa.melt(id_vars=["time"], value_vars=["cost_fuel_k", "cost_imp_k", "cost_om_k", "cost_pen_k"], var_name="k", value_name="kh")
                nm = dict(cost_fuel_k="Gas fuel + carbon", cost_imp_k="Imported power", cost_om_k="Plant upkeep", cost_pen_k="Blackout penalties")
                cost_long["Cost"] = cost_long["k"].map(nm)
                area = alt.Chart(cost_long).mark_area(opacity=0.85).encode(
                    x=alt.X("time:T", axis=TAX), y=alt.Y("kh:Q", stack="zero", title="Thousand $ per hour"),
                    color=alt.Color("Cost:N", scale=alt.Scale(domain=list(nm.values()), range=["#94a3b8", "#fb923c", "#38bdf8", "#ef4444"]), legend=alt.Legend(title=None)))
                rev = alt.Chart(dfa).mark_line(color="#34d399", strokeWidth=2).encode(x="time:T", y="revenue_k:Q")
                show_chart(style((area + rev).properties(height=250, title="Cost over time (green line = revenue)")))
            cap = S["cap"]
            fleet = pd.DataFrame([
                dict(Source="🔥 Natural gas", Capacity=f"{cap['gas']:.0f} MW", Now=f"{r['gas']:.0f} MW", Note="Can be turned up and down, but emits CO₂"),
                dict(Source="💧 Hydro (Bow River)", Capacity=f"{cap['hydro']:.0f} MW", Now=f"{r['hydro']:.0f} MW", Note="Strongest in spring and summer"),
                dict(Source="🌬️ Wind", Capacity=f"{cap['wind']:.0f} MW", Now=f"{r['wind']:.0f} MW", Note="Follows the wind, not demand"),
                dict(Source="☀️ Solar", Capacity=f"{cap['solar']:.0f} MW", Now=f"{r['solar']:.0f} MW", Note="Daylight only; weak in winter"),
                dict(Source="⚛️ Nuclear", Capacity=f"{cap['nuclear']:.0f} MW", Now=f"{r['nuclear']:.0f} MW", Note="Always on (95%), weather-proof"),
                dict(Source="🔋 Battery", Capacity=f"{cap['battery_mw']:.0f} MW / {cap['battery_mwh']:.0f} MWh", Now=f"{r['battery'] - r['charge']:+.0f} MW", Note="+ discharging, − charging"),
                dict(Source="🔌 Imports", Capacity=f"{S['import_max']} MW line", Now=f"{r['imports']:.0f} MW", Note="Expensive; fails in a transmission outage"),
            ])
            try:
                st.dataframe(fleet, hide_index=True, width="stretch")
            except TypeError:
                st.dataframe(fleet, hide_index=True, use_container_width=True)
            st.markdown("**🚧 Under construction**")
            if not S["queue"]:
                st.caption("Nothing is being built right now.")
            for item in S["queue"]:
                spec = BUILDS[item["kind"]]
                total = max(1, item["eta"] - item["start"])
                done = clamp((S["t"] - item["start"]) / total, 0, 1)
                left = max(0, item["eta"] - S["t"])
                st.progress(done, text=f"{spec['icon']} {spec['label']}: {left // 24}d {left % 24}h to go")
            g1, g2, g3, g4 = st.columns(4)
            g1.metric("Days survived", r["day"] + 1)
            g2.metric("Blackout events", S["blackouts"])
            g3.metric("Blackout hours", S["blackout_hours"])
            g4.metric("No-blackout streak", f"{S['streak']} days")
            with st.expander("📰 Event log"):
                for n in reversed(S["news"][-40:]):
                    icon = {"bad": "🔴", "good": "🟢"}.get(n["kind"], "🔹")
                    st.markdown(f"{icon} `{n['label']}` — {md_safe(n['text'])}")
            with st.expander("🧪 How the model works"):
                st.markdown(MODEL_TEXT)


live()   # draw the game (and, while the clock runs, refresh just this area on a timer)
