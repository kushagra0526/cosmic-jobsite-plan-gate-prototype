import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sitegate.gate import check  # noqa: E402
from sitegate.planner import plan  # noqa: E402
from sitegate.sim import run  # noqa: E402
from sitegate.site import make_site, row  # noqa: E402


def codes(g):
    return {x["code"] for o in g["tasks"] for x in o["issues"]}


def site_with(seed=5, **over):
    s = make_site(seed)
    s.update(over)
    return s


def test_valid_plans_dispatch():
    for seed in range(1, 15):
        s = make_site(seed)
        p, _ = plan(s)
        assert check(s, p)["verdict"] == "DISPATCH", seed


def test_wind_window_is_blocked_with_next_clear_time():
    s = make_site(3)
    s["forecast"]["gust_mph"] = [12.0] * len(s["forecast"]["gust_mph"])
    for i in range(20, 26):          # 12:00-13:30 gusts 33 mph
        s["forecast"]["gust_mph"][i] = 33.0
    s["forecast"]["lightning"] = []
    r = next(x for x in s["rows"] if x["tube_ready"] == -1 and x["installed"] == 0)
    r["staged"] = 93
    s["robots"][0]["at"] = r["block"]
    p = {"tasks": [{"id": "a", "type": "install", "robot": "R1", "crew": "C1", "block": r["block"], "row": r["row"],
                    "start": "12:15", "end": "13:15", "modules": 70}]}
    g = check(s, p)
    iss = g["tasks"][0]["issues"]
    assert g["verdict"] == "BLOCK" and iss[0]["code"] == "WIND" and "13:30" in iss[0]["fix"]


def test_tube_supply_travel_and_battery():
    s = make_site(7)
    r = next(x for x in s["rows"] if x["tube_ready"] is None)
    s["robots"][0]["soc"] = 15
    p = {"tasks": [{"id": "a", "type": "install", "robot": "R1", "crew": "C1", "block": r["block"], "row": r["row"],
                    "start": "08:00", "end": "09:30", "modules": 130}]}
    c = codes(check(s, p))
    assert {"TUBE_NOT_READY", "NO_MODULES_STAGED", "NOT_THERE", "BATTERY", "RATE", "ROW_FULL"} <= c


def test_separation_flags_both_robots():
    s = make_site(9)
    b = s["blocks"][0]
    rs = [x for x in s["rows"] if x["block"] == b and x["tube_ready"] == -1 and x["installed"] == 0][:2]
    for x in rs:
        x["staged"] = 93
    for rb in s["robots"]:
        rb["at"], rb["soc"] = b, 100
    s["forecast"]["gust_mph"] = [10.0] * len(s["forecast"]["gust_mph"])
    s["forecast"]["lightning"] = []
    p = {"tasks": [{"id": f"x{i}", "type": "install", "robot": f"R{i + 1}", "crew": f"C{i + 1}", "block": b,
                    "row": rs[i]["row"], "start": "08:00", "end": "09:00", "modules": 70} for i in range(2)]}
    g = check(s, p)
    if abs(rs[0]["row"] - rs[1]["row"]) < 2:
        assert all(any(x["code"] == "SEPARATION" for x in o["issues"]) for o in g["tasks"])


def test_forgotten_rules_are_caught_on_the_right_task():
    for seed, forget in [(11, "battery"), (12, "supply"), (13, "travel"), (14, "tube")]:
        s = make_site(seed)
        p, truth = plan(s, forget={forget})
        g = check(s, p)
        flagged = {(o["task"], x["code"]) for o in g["tasks"] for x in o["issues"]}
        assert truth and all((v["task"], v["code"]) in flagged for v in truth), (seed, forget)


def test_unchecked_battery_plan_strands_robot_in_sim():
    for seed in range(1, 40):
        s = make_site(seed)
        p, truth = plan(s, forget={"battery"})
        if truth and run(s, p).get("stranded"):
            good, _ = plan(s)
            assert not run(s, good).get("stranded")
            return
    raise AssertionError("no stranding found")


def test_row_lookup():
    s = make_site(2)
    assert row(s, "B1", 1)["row"] == 1
