"""Execute a plan against the day as it actually happens (5-minute steps).

Layer 1 on the robot is assumed to stop lifting in gusts/lightning (so wind costs time, not
safety). Everything else the plan gets wrong shows up as: robot-minutes lost, a stranded robot
(battery at 0 away from the yard, needs recovery), minutes two robots spent closer than the row
separation, crew overtime, and modules promised but not installed.
"""
from collections import defaultdict

from .site import DAY_END, gust_max, lightning_hit, mins, travel_min

STEP = 5


def run(site, plan, drop=frozenset()):
    L = site["limits"]
    tasks = [t for t in plan["tasks"] if t["id"] not in drop]
    rob = {r["id"]: {"loc": r["at"], "soc": float(r["soc"]), "stranded": False} for r in site["robots"]}
    stock = {(r["block"], r["row"]): r["staged"] for r in site["rows"]}
    done = {(r["block"], r["row"]): r["installed"] for r in site["rows"]}
    tube = {(r["block"], r["row"]): r["tube_ready"] for r in site["rows"]}
    m = defaultdict(float)
    crew_min = defaultdict(float)
    for c in site["crews"]:
        crew_min[c["id"]] = c["hours_before"] * 60
    progress = defaultdict(float)
    for t in tasks:
        if t["type"] == "install":
            m["promised"] += t["modules"]
    for now in range(0, DAY_END, STEP):
        active = [t for t in tasks if mins(t["start"]) <= now < mins(t["end"])]
        working = []
        for t in active:
            if t["type"] == "deliver" and now + STEP >= mins(t["end"]):
                stock[(t["block"], t["row"])] += t["pallets"] * L["pallet"]
        for rid, R in rob.items():
            mine = [t for t in active if t.get("robot") == rid]
            if R["stranded"]:
                if mine:
                    m["robot_min_lost"] += STEP
                continue
            if not mine:
                R["soc"] -= L["drain_idle"] * STEP / 60
                continue
            t = mine[0]
            if t["type"] == "travel":
                R["soc"] -= L["drain_move"] * STEP / 60
                if now + STEP >= mins(t["end"]):
                    R["loc"] = t["to"]
            elif t["type"] == "charge":
                if R["loc"] == "yard":
                    R["soc"] = min(100.0, R["soc"] + L["charge_per_h"] * STEP / 60)
                else:
                    m["robot_min_lost"] += STEP
            elif t["type"] == "install":
                key = (t["block"], t["row"])
                ok = R["loc"] == t["block"]
                if not ok and travel_min(site, R["loc"], t["block"]) <= STEP:
                    R["loc"] = t["block"]
                    ok = True
                reason = None
                if not ok:
                    reason = "not_there"
                elif tube[key] is None or tube[key] > now:
                    reason = "no_tube"
                elif gust_max(site, now, now + STEP) > L["gust_mph"] or lightning_hit(site, now, now + STEP):
                    reason = "weather"
                elif stock[key] < 1:
                    reason = "no_modules"
                elif done[key] >= L["row_modules"]:
                    reason = "row_full"
                R["soc"] -= (L["drain_install"] if reason is None else L["drain_idle"]) * STEP / 60
                if reason:
                    m["robot_min_lost"] += STEP
                    m["lost_" + reason] += STEP
                else:
                    if t.get("crew"):
                        crew_min[t["crew"]] += STEP
                    progress[t["id"]] += L["rate_per_h"] * STEP / 60
                    n = int(progress[t["id"]]) - int(progress[t["id"]] - L["rate_per_h"] * STEP / 60)
                    n = min(n, stock[key], L["row_modules"] - done[key], t["modules"] - int(progress[t["id"]] - L["rate_per_h"] * STEP / 60))
                    n = max(n, 0)
                    stock[key] -= n
                    done[key] += n
                    m["installed"] += n
                    working.append((t["block"], t["row"], rid))
            if R["soc"] <= 0 and R["loc"] != "yard":
                R["stranded"] = True
                R["soc"] = 0
                m["stranded"] += 1
        for i in range(len(working)):
            for j in range(i + 1, len(working)):
                a, b = working[i], working[j]
                if a[0] == b[0] and abs(a[1] - b[1]) < L["row_sep"]:
                    m["separation_min"] += STEP
    m["crew_overtime_min"] = sum(max(0.0, v - L["crew_max_h"] * 60) for v in crew_min.values())
    return dict(m)
