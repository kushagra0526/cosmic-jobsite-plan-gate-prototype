"""White-box gate for an orchestrator's day plan, run before anything is dispatched.

Plan = list of tasks the agent emits:
  install  {robot, crew, block, row, start, end, modules}
  travel   {robot, to, start, end}
  charge   {robot, start, end}                 (at the yard)
  deliver  {block, row, pallets, start, end}   (telehandler)
Times are "HH:MM". Every issue says what's wrong, which task, and a concrete fix the agent can
re-plan from. BLOCK = unsafe or strands a robot; FIX = the plan won't execute as written.
"""
from collections import defaultdict

from .site import DAY_END, DAY_START, gust_max, hhmm, lightning_hit, mins, row, travel_min

SEVERITY = {"WIND": "BLOCK", "LIGHTNING": "BLOCK", "SEPARATION": "BLOCK", "BATTERY": "BLOCK",
            "TUBE_NOT_READY": "FIX", "NO_MODULES_STAGED": "FIX", "ROW_FULL": "FIX", "RATE": "FIX",
            "NOT_THERE": "FIX", "TRAVEL_TOO_SHORT": "FIX", "DOUBLE_BOOKED": "FIX", "NO_CREW": "FIX",
            "CREW_HOURS": "FIX", "OUTSIDE_SHIFT": "FIX", "TELEHANDLER": "FIX", "CHARGE_AWAY": "FIX"}


def _t(task):
    return mins(task["start"]), mins(task["end"])


def next_clear(site, a, dur, limit):
    for s in range(a, DAY_END - dur + 1, site["forecast"]["slot_min"]):
        if gust_max(site, s, s + dur) <= limit and not lightning_hit(site, s, s + dur):
            return s
    return None


def check(site, plan):
    L = site["limits"]
    tasks = sorted(enumerate(plan["tasks"]), key=lambda it: (mins(it[1]["start"]), it[0]))
    issues = defaultdict(list)

    def bad(i, code, msg, fix=""):
        issues[i].append({"code": code, "severity": SEVERITY[code], "msg": msg, "fix": fix})

    # shift + per-resource overlaps
    by = defaultdict(list)
    for i, t in tasks:
        a, b = _t(t)
        if a < DAY_START or b > DAY_END or b <= a:
            bad(i, "OUTSIDE_SHIFT", f"{t['start']}-{t['end']} is outside 07:00-17:30 or empty")
        for key in ("robot", "crew"):
            if t.get(key):
                by[(key, t[key])].append((a, b, i))
        if t["type"] == "deliver":
            by[("telehandler", "T1")].append((a, b, i))
    for (kind, who), spans in by.items():
        spans.sort()
        for (a1, b1, i1), (a2, b2, i2) in zip(spans, spans[1:]):
            if a2 < b1:
                code = "TELEHANDLER" if kind == "telehandler" else "DOUBLE_BOOKED"
                bad(i2, code, f"{who} is already busy until {hhmm(b1)} (task {plan['tasks'][i1]['id']})",
                    f"start at {hhmm(b1)} or later")
    # crew hours
    worked = defaultdict(float)
    for i, t in tasks:
        if t["type"] == "install":
            if not t.get("crew"):
                bad(i, "NO_CREW", f"{t['robot']} installs with no fastening crew", "assign a free crew")
                continue
            a, b = _t(t)
            worked[t["crew"]] += (b - a) / 60
            before = next((c["hours_before"] for c in site["crews"] if c["id"] == t["crew"]), 0)
            if worked[t["crew"]] + before > L["crew_max_h"] + 1e-9:
                bad(i, "CREW_HOURS", f"{t['crew']} reaches {worked[t['crew']] + before:.1f} h (max {L['crew_max_h']:.0f})",
                    "hand this task to another crew or shorten it")
    # rows, supply, weather, prerequisites
    planned = defaultdict(int)
    deliveries = defaultdict(list)
    for i, t in tasks:
        if t["type"] == "deliver":
            a, b = _t(t)
            if b - a < t["pallets"] * L["deliver_min_per_pallet"]:
                bad(i, "RATE", f"{t['pallets']} pallets need {t['pallets'] * L['deliver_min_per_pallet']} min, plan gives {b - a}",
                    f"end at {hhmm(a + t['pallets'] * L['deliver_min_per_pallet'])}")
            deliveries[(t["block"], t["row"])].append((b, t["pallets"] * L["pallet"]))
    installs = [(i, t) for i, t in tasks if t["type"] == "install"]
    for i, t in installs:
        a, b = _t(t)
        rw = row(site, t["block"], t["row"])
        key = (t["block"], t["row"])
        if rw["tube_ready"] is None or rw["tube_ready"] > a:
            when = "not today" if rw["tube_ready"] is None else f"at {hhmm(rw['tube_ready'])}"
            ready = sorted(r["row"] for r in site["rows"] if r["block"] == t["block"] and r["tube_ready"] is not None
                           and r["tube_ready"] <= a and r["installed"] + planned[(t["block"], r["row"])] < L["row_modules"])
            bad(i, "TUBE_NOT_READY", f"{t['block']} row {t['row']} torque tube is ready {when}",
                f"rows ready in {t['block']} then: {ready[:6]}" if ready else f"no ready rows in {t['block']} at {t['start']}")
        cap = L["row_modules"] - rw["installed"] - planned[key]
        if t["modules"] > cap:
            bad(i, "ROW_FULL", f"{t['block']} row {t['row']} only has {max(cap, 0)} modules left", f"set modules to {max(cap, 0)}")
        can = int(L["rate_per_h"] * (b - a) / 60 + 0.5)
        if t["modules"] > can:
            bad(i, "RATE", f"{t['modules']} modules in {b - a} min (max ~{can})", f"end at {hhmm(a + int(t['modules'] * 60 / L['rate_per_h']) + 1)}")
        avail = rw["staged"] + sum(n for e, n in deliveries[key] if e <= b)
        need = planned[key] + t["modules"]
        if need > avail:
            short = need - avail
            bad(i, "NO_MODULES_STAGED", f"needs {need} modules at {t['block']} row {t['row']} by {t['end']}, {avail} staged/delivered",
                f"deliver {-(-short // L['pallet'])} pallet(s) to {t['block']} row {t['row']} before {t['start']}")
        planned[key] += t["modules"]
        g = gust_max(site, a, b)
        if g > L["gust_mph"]:
            nc = next_clear(site, a, b - a, L["gust_mph"])
            bad(i, "WIND", f"gusts to {g:.0f} mph during {t['start']}-{t['end']} (limit {L['gust_mph']} mph for lifting modules)",
                f"earliest clear window {hhmm(nc)}" if nc is not None else "no clear window left today; charge instead")
        lh = lightning_hit(site, a, b)
        if lh:
            bad(i, "LIGHTNING", f"lightning stop {hhmm(lh[0])}-{hhmm(lh[1])} overlaps", f"resume after {hhmm(lh[1])}")
    # separation between robots in one block
    for x in range(len(installs)):
        for y in range(x + 1, len(installs)):
            (i, t), (j, u) = installs[x], installs[y]
            if t["robot"] == u["robot"] or t["block"] != u["block"]:
                continue
            (a1, b1), (a2, b2) = _t(t), _t(u)
            if a2 < b1 and a1 < b2 and abs(t["row"] - u["row"]) < site["limits"]["row_sep"]:
                bad(i, "SEPARATION", f"{t['robot']} on {t['block']} row {t['row']} while {u['robot']} works row {u['row']} ({hhmm(max(a1, a2))}-{hhmm(min(b1, b2))})",
                    f"use a row >= {site['limits']['row_sep']} away from row {u['row']}")
                bad(j, "SEPARATION", f"{u['robot']} on {u['block']} row {u['row']} while {t['robot']} works row {t['row']} ({hhmm(max(a1, a2))}-{hhmm(min(b1, b2))})",
                    f"use a row >= {site['limits']['row_sep']} away from row {t['row']}")
    # robot location + battery, walked per robot
    for rb in site["robots"]:
        mine = [(i, t) for i, t in tasks if t.get("robot") == rb["id"]]
        loc, soc, clock = rb["at"], float(rb["soc"]), DAY_START
        for i, t in mine:
            a, b = _t(t)
            soc -= L["drain_idle"] * max(0, a - clock) / 60
            if t["type"] == "travel":
                need = travel_min(site, loc, t["to"])
                if b - a < need:
                    bad(i, "TRAVEL_TOO_SHORT", f"{loc} → {t['to']} takes {need} min, plan gives {b - a}", f"end at {hhmm(a + need)}")
                soc -= L["drain_move"] * need / 60
                loc = t["to"]
            elif t["type"] == "charge":
                if loc != "yard":
                    bad(i, "CHARGE_AWAY", f"{rb['id']} is at {loc}, chargers are at the yard", f"travel to yard first ({travel_min(site, loc, 'yard')} min)")
                else:
                    soc = min(100.0, soc + L["charge_per_h"] * (b - a) / 60)
            elif t["type"] == "install":
                if loc != t["block"]:
                    bad(i, "NOT_THERE", f"{rb['id']} is at {loc}, task is in {t['block']}",
                        f"add travel {loc} → {t['block']} ({travel_min(site, loc, t['block'])} min) before {t['start']}")
                    loc = t["block"]
                soc -= L["drain_install"] * (b - a) / 60
            home = L["drain_move"] * travel_min(site, loc, "yard") / 60
            if soc - home < L["reserve"]:
                need_min = int((L["reserve"] + home - soc) / L["charge_per_h"] * 60) + 15
                bad(i, "BATTERY", f"{rb['id']} at {max(soc, 0):.1f}% after this task, needs {home + L['reserve']:.1f}% to get back to the yard",
                    f"charge ~{need_min} min at the yard before this task")
                soc = max(soc, 0)
            clock = b
    out = []
    for i, t in enumerate(plan["tasks"]):
        iss = issues.get(i, [])
        v = "BLOCK" if any(x["severity"] == "BLOCK" for x in iss) else "FIX" if iss else "OK"
        out.append({"task": t["id"], "verdict": v, "issues": iss})
    verdict = "BLOCK" if any(o["verdict"] == "BLOCK" for o in out) else "FIX" if any(o["verdict"] == "FIX" for o in out) else "DISPATCH"
    return {"verdict": verdict, "tasks": out, "feedback": feedback(plan, out)}


def feedback(plan, out):
    lines = []
    tmap = {t["id"]: t for t in plan["tasks"]}
    for o in out:
        t = tmap[o["task"]]
        for x in o["issues"]:
            where = f"{t.get('robot', 'T1')} {t['type']}" + (f" {t['block']} row {t['row']}" if t.get("row") else f" → {t['to']}" if t.get("to") else "")
            lines.append(f"[{x['severity']}] {o['task']} ({where}, {t['start']}-{t['end']}): {x['code']}: {x['msg']}." + (f" Fix: {x['fix']}." if x["fix"] else ""))
    return lines
