"""Synthetic utility-scale solar jobsite for one work day.

All numbers are assumptions for the prototype (configurable in LIMITS), not Cosmic's specs:
blocks of single-axis tracker rows, 90 modules per row, modules come on pallets of 31, a module can
only go on a row whose torque tube is installed, Cosmic-1 places ~75 modules/h with a 2-person
fastening crew, the robot is battery-electric and charges at the laydown yard.

Time is minutes after 07:00. The forecast is in 15-minute slots.
"""
import random

DAY_START, DAY_END, SLOT = 0, 630, 15     # 07:00 - 17:30
LIMITS = {
    "gust_mph": 25,           # stop lifting modules above this gust (assumption)
    "lightning_wait_min": 30,  # 30/30 rule: stop until 30 min after the last strike nearby
    "row_sep": 2,             # two heavy-lift robots must be >= 2 rows apart in one block
    "rate_per_h": 75,         # modules placed per robot-hour
    "drain_install": 11.0,    # battery % per hour while installing
    "drain_move": 18.0,       # battery % per hour while driving
    "drain_idle": 2.0,
    "charge_per_h": 40.0,     # % per hour at the yard charger
    "reserve": 8.0,           # % that must remain on arrival back at the yard
    "crew_max_h": 10.0,
    "pallet": 31,
    "row_modules": 90,
    "deliver_min_per_pallet": 12,
}


def hhmm(m):
    return f"{7 + m // 60:02d}:{m % 60:02d}"


def mins(s):
    h, m = map(int, s.split(":"))
    return (h - 7) * 60 + m


def make_site(seed):
    rng = random.Random(seed)
    blocks = [f"B{i}" for i in range(1, 6)]
    # driving minutes from the yard to each block (blocks lie along an access road)
    yard = {b: 4 + 3 * i + rng.randint(0, 2) for i, b in enumerate(blocks)}
    rows = []
    for bi, b in enumerate(blocks):
        n = 24
        done_upto = rng.randint(0, 10) if bi < 2 else 0      # earlier blocks partly finished
        tube_upto = rng.randint(10, 24) if bi < 3 else rng.randint(0, 12)
        for r in range(1, n + 1):
            installed = LIMITS["row_modules"] if r <= done_upto else 0
            if r <= tube_upto:
                tube = -1                                         # ready before today
            elif r <= tube_upto + 4 and rng.random() < 0.7:
                tube = rng.choice([120, 180, 240, 300, 360])      # tube crew finishes later today
            else:
                tube = None                                       # not today
            staged = 0 if installed else rng.choice([0, 0, 31, 62, 93])
            rows.append({"block": b, "row": r, "installed": installed, "tube_ready": tube, "staged": staged})
    gust = []
    base = rng.uniform(8, 16)
    front = rng.random() < 0.6
    f0 = rng.randrange(150, 420, SLOT)
    f1 = f0 + rng.choice([45, 60, 90, 120])
    for t in range(DAY_START, DAY_END, SLOT):
        g = base + rng.uniform(-3, 5)
        if front and f0 <= t < f1:
            g += rng.uniform(13, 22)
        gust.append(round(g, 1))
    lightning = []
    if rng.random() < 0.3:
        s = rng.randrange(240, 540, SLOT)
        lightning.append([s, s + 15])
    robots = [{"id": f"R{i}", "at": "yard", "soc": rng.choice([70, 85, 100])} for i in range(1, 4)]
    crews = [{"id": f"C{i}", "hours_before": rng.choice([0.0, 0.0, 2.5, 4.0])} for i in range(1, 4)]  # e.g. came off a night pour
    return {"seed": seed, "blocks": blocks, "yard_min": yard, "rows": rows,
            "forecast": {"slot_min": SLOT, "gust_mph": gust, "lightning": lightning},
            "robots": robots, "crews": crews, "limits": dict(LIMITS)}


def travel_min(site, a, b):
    if a == b:
        return 0
    if a == "yard":
        return site["yard_min"][b]
    if b == "yard":
        return site["yard_min"][a]
    return abs(site["yard_min"][a] - site["yard_min"][b]) + 3


def row(site, block, r):
    return next(x for x in site["rows"] if x["block"] == block and x["row"] == r)


def gust_max(site, a, b):
    s = site["forecast"]["slot_min"]
    g = site["forecast"]["gust_mph"]
    return max(g[i] for i in range(a // s, max(a // s + 1, -(-b // s))) if i < len(g)) if b > a else 0


def lightning_hit(site, a, b):
    w = site["limits"]["lightning_wait_min"]
    for s, e in site["forecast"]["lightning"]:
        if a < e + w and b > s:
            return (s, e + w)
    return None
