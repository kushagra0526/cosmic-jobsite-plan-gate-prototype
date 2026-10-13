"""A deterministic day planner (stand-in for the orchestrator agent).

plan(site) respects every constraint. plan(site, forget={...}) behaves like an agent that forgot a
rule; whenever forgetting it actually leads to a broken task, the planner logs the violation itself
(the ground truth for the benchmark, computed here, never by the gate).

forget keys -> violation code: wind WIND, lightning LIGHTNING, tube TUBE_NOT_READY,
supply NO_MODULES_STAGED, battery BATTERY, separation SEPARATION, crew_hours CREW_HOURS,
travel NOT_THERE
"""
from collections import defaultdict

from .site import DAY_END, gust_max, hhmm, lightning_hit, travel_min

CODE = {"wind": "WIND", "lightning": "LIGHTNING", "tube": "TUBE_NOT_READY", "supply": "NO_MODULES_STAGED",
        "battery": "BATTERY", "separation": "SEPARATION", "crew_hours": "CREW_HOURS", "travel": "NOT_THERE"}
CHUNK = 90


def plan(site, forget=frozenset()):
    L = site["limits"]
    slot = site["forecast"]["slot_min"]
    tasks, truth = [], []
    planned = defaultdict(int)
    staged = {(r["block"], r["row"]): r["staged"] for r in site["rows"]}
    tele_free = 0
    crew_free = {c["id"]: 0 for c in site["crews"]}
    crew_h = {c["id"]: c["hours_before"] for c in site["crews"]}
    robots = {r["id"]: {"loc": r["at"], "soc": float(r["soc"]), "clock": 0} for r in site["robots"]}
    owner = {}

    def add(t):
        t["id"] = f"t{len(tasks) + 1}"
        tasks.append(t)
        return t

    def remaining(b, r, at):
        rw = next(x for x in site["rows"] if x["block"] == b and x["row"] == r)
        ready = rw["tube_ready"] is not None and rw["tube_ready"] <= at
        if "tube" in forget:
            ready = True   # forgets to check torque-tube status
        return (L["row_modules"] - rw["installed"] - planned[(b, r)]) if ready else 0

    def ready_rows(b, at):
        rows = [r for r in range(1, 25) if remaining(b, r, at) > 0]
        if "tube" in forget:   # goes where the pallets are, without checking the tubes
            rows.sort(key=lambda r: (-staged[(b, r)], r))
        return rows

    def soc_needed(loc, minutes):
        return L["drain_install"] * minutes / 60 + L["drain_move"] * travel_min(site, loc, "yard") / 60 + L["reserve"]

    # blocks: one robot per block unless separation is forgotten
    for rid in robots:
        free = sorted((b for b in site["blocks"] if b not in owner.values() or "separation" in forget),
                      key=lambda b: -sum(remaining(b, r, 120) for r in range(1, 25)))
        if "separation" in forget and owner:
            free = [list(owner.values())[0]] + free     # sends a second robot into the busiest block
        if free:
            owner[rid] = free[0]
    used_rows = defaultdict(list)   # block -> [(a, b, row, robot)]

    for _ in range(400):
        rid = min((r for r in robots if robots[r]["clock"] < DAY_END - 30), key=lambda r: robots[r]["clock"], default=None)
        if rid is None:
            break
        R = robots[rid]
        b = owner.get(rid)
        now = R["clock"]
        if b is None:
            R["clock"] = DAY_END
            continue
        # battery: go charge if a 60-minute install could not be finished with reserve
        if R["soc"] < soc_needed(b, CHUNK) + 3 and "battery" not in forget:
            if R["loc"] != "yard":
                d = travel_min(site, R["loc"], "yard")
                add({"type": "travel", "robot": rid, "to": "yard", "start": hhmm(now), "end": hhmm(now + d)})
                R["soc"] -= L["drain_move"] * d / 60
                now += d
                R["loc"] = "yard"
            dur = min(120, -(-int((95 - R["soc"]) / L["charge_per_h"] * 60) // slot) * slot)
            dur = max(dur, slot)
            add({"type": "charge", "robot": rid, "start": hhmm(now), "end": hhmm(min(now + dur, DAY_END))})
            R["soc"] = min(100.0, R["soc"] + L["charge_per_h"] * dur / 60)
            R["clock"] = now + dur
            continue
        # move to the block
        arrive = now
        if R["loc"] != b:
            d = travel_min(site, R["loc"], b)
            if "travel" in forget:
                R["teleported"] = True
            else:
                add({"type": "travel", "robot": rid, "to": b, "start": hhmm(now), "end": hhmm(now + d)})
                arrive = now + d
                R["soc"] -= L["drain_move"] * d / 60
            R["loc"] = b
        start = -(-arrive // slot) * slot
        rows = [r for r in ready_rows(b, start)
                if "separation" in forget or all(not (a < start + CHUNK and start < e) or abs(r - rr) >= L["row_sep"]
                                                 for a, e, rr, who in used_rows[b] if who != rid)]
        if not rows:
            later = [x["tube_ready"] for x in site["rows"] if x["block"] == b and x["tube_ready"] is not None and x["tube_ready"] > start]
            R["clock"] = min(later) if later else DAY_END
            continue
        r = rows[0]
        # window: cut at weather and at the chunk length
        end = start
        while end < min(start + CHUNK, DAY_END):
            nxt = end + slot
            if "wind" not in forget and gust_max(site, end, nxt) > L["gust_mph"]:
                break
            if "lightning" not in forget and lightning_hit(site, end, nxt):
                break
            end = nxt
        if end == start:
            R["clock"] = start + slot
            R["soc"] -= L["drain_idle"] * (start + slot - now) / 60
            continue
        n = min(remaining(b, r, start), int(L["rate_per_h"] * (end - start) / 60))
        if n <= 0:
            R["clock"] = end
            continue
        end = start + -(-int(n * 60 / L["rate_per_h"]) // 5) * 5
        # modules on site
        have = staged[(b, r)]
        if have < planned[(b, r)] + n and "supply" not in forget:
            pallets = -(-(planned[(b, r)] + n - have) // L["pallet"])
            dmin = pallets * L["deliver_min_per_pallet"]
            ds = max(tele_free, start - dmin - 5, 0)
            add({"type": "deliver", "block": b, "row": r, "pallets": pallets, "start": hhmm(ds), "end": hhmm(ds + dmin)})
            tele_free = ds + dmin
            staged[(b, r)] += pallets * L["pallet"]
            if ds + dmin > start:   # robot waits for the pallets, then re-plans its window
                R["soc"] -= L["drain_idle"] * (ds + dmin - now) / 60
                R["clock"] = ds + dmin
                continue
        # crew
        crew = min((c for c in crew_free if crew_free[c] <= start and
                    (crew_h[c] + (end - start) / 60 <= L["crew_max_h"] or "crew_hours" in forget)),
                   key=lambda c: (crew_h[c] if "crew_hours" not in forget else -crew_h[c], c), default=None)
        if crew is None:
            R["clock"] = min((v for v in crew_free.values() if v > start), default=start + slot)
            continue
        if end > DAY_END:
            continue_end = DAY_END
            n = int(L["rate_per_h"] * (continue_end - start) / 60)
            end = continue_end
            if n <= 0:
                R["clock"] = DAY_END
                continue
        t = add({"type": "install", "robot": rid, "crew": crew, "block": b, "row": r,
                 "start": hhmm(start), "end": hhmm(end), "modules": n})
        if R.pop("teleported", False):
            truth.append({"code": "NOT_THERE", "task": t["id"]})
        # log what a forgotten rule actually broke (ground truth)
        if "wind" in forget and gust_max(site, start, end) > L["gust_mph"]:
            truth.append({"code": "WIND", "task": t["id"]})
        if "lightning" in forget and lightning_hit(site, start, end):
            truth.append({"code": "LIGHTNING", "task": t["id"]})
        rw = next(x for x in site["rows"] if x["block"] == b and x["row"] == r)
        if "tube" in forget and (rw["tube_ready"] is None or rw["tube_ready"] > start):
            truth.append({"code": "TUBE_NOT_READY", "task": t["id"]})
        if "supply" in forget and staged[(b, r)] < planned[(b, r)] + n:
            truth.append({"code": "NO_MODULES_STAGED", "task": t["id"]})
        if "separation" in forget and any(a < end and start < e and abs(r - rr) < L["row_sep"] and who != rid for a, e, rr, who in used_rows[b]):
            truth.append({"code": "SEPARATION", "task": t["id"]})
        crew_h[crew] += (end - start) / 60
        if "crew_hours" in forget and crew_h[crew] > L["crew_max_h"] + 1e-9:
            truth.append({"code": "CREW_HOURS", "task": t["id"]})
        crew_free[crew] = end
        planned[(b, r)] += n
        used_rows[b].append((start, end, r, rid))
        R["soc"] -= L["drain_install"] * (end - start) / 60 + L["drain_idle"] * max(0, start - arrive) / 60
        if "battery" in forget and R["soc"] - L["drain_move"] * travel_min(site, b, "yard") / 60 < L["reserve"]:
            truth.append({"code": "BATTERY", "task": t["id"]})
        R["clock"] = end
    tasks.sort(key=lambda t: (t["start"], t["id"]))
    return {"tasks": tasks}, truth
