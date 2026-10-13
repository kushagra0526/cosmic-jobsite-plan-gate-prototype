"""Benchmark: agent day-plans that forgot a rule, through the gate and the simulator.

    python evaluate.py dev        # seeds 1-120
    python evaluate.py heldout    # seeds in eval/heldout.json, committed before the first held-out run

Per seed the planner (agent stand-in) forgets 1 rule at random. The planner itself logs each task
where forgetting it actually broke something (truth). We measure:
  caught      - truth (task, code) pairs the gate flagged on that task with that code
  valid plans - the same day planned with every rule, flagged by the gate (should be 0)
  simulation  - what the unchecked plan does on the day vs. the plan with flagged tasks held back
"""
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from sitegate.gate import check  # noqa: E402
from sitegate.planner import CODE, plan  # noqa: E402
from sitegate.sim import run  # noqa: E402
from sitegate.site import make_site  # noqa: E402


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    which = sys.argv[1] if len(sys.argv) > 1 else "dev"
    seeds = range(1, 121) if which == "dev" else json.loads((HERE / "eval/heldout.json").read_text())["seeds"]
    by = defaultdict(Counter)
    tot = Counter()
    sim_raw, sim_gated, sim_re = Counter(), Counter(), Counter()
    for seed in seeds:
        site = make_site(seed)
        good, _ = plan(site)
        g0 = check(site, good)
        tot["days"] += 1
        tot["valid_flagged"] += g0["verdict"] != "DISPATCH"
        forget = random.Random(seed * 7).choice(sorted(CODE))
        p, truth = plan(site, forget={forget})
        by[forget]["days"] += 1
        if not truth:
            by[forget]["no_effect"] += 1
            continue
        g = check(site, p)
        flagged = {(o["task"], x["code"]) for o in g["tasks"] for x in o["issues"]}
        for v in truth:
            by[forget]["planted"] += 1
            by[forget]["caught"] += (v["task"], v["code"]) in flagged
        tot["planted"] += len(truth)
        tot["caught"] += sum((v["task"], v["code"]) in flagged for v in truth)
        tot["bad_days"] += 1
        tot["bad_days_stopped"] += g["verdict"] != "DISPATCH"
        drop = {o["task"] for o in g["tasks"] if o["verdict"] != "OK"}
        a, b, c = run(site, p), run(site, p, drop=drop), run(site, good)
        for k in ("installed", "promised", "robot_min_lost", "stranded", "separation_min", "crew_overtime_min"):
            sim_raw[k] += a.get(k, 0)
            sim_gated[k] += b.get(k, 0)
            sim_re[k] += c.get(k, 0)
    print(f"\n== {which}: {tot['days']} jobsite days")
    print(f"   valid plans (every rule kept) flagged by the gate: {tot['valid_flagged']}/{tot['days']}")
    print(f"   agent plans where a forgotten rule broke something: {tot['bad_days']}  ·  stopped before dispatch: {tot['bad_days_stopped']}/{tot['bad_days']}")
    print(f"   broken tasks caught (right task, right code): {tot['caught']}/{tot['planted']}")
    print(f"\n   {'forgot':<12}{'days':>5}{'no effect':>10}{'broken':>8}{'caught':>8}")
    for k in sorted(by):
        c = by[k]
        print(f"   {k:<12}{c['days']:>5}{c['no_effect']:>10}{c['planted']:>8}{c['caught']:>8}")
    print("\n   simulated on the same days      unchecked   flagged held   re-planned from feedback")
    for k, lab in (("promised", "modules promised"), ("installed", "modules installed"), ("robot_min_lost", "robot-minutes lost"),
                   ("stranded", "robots stranded"), ("separation_min", "minutes robots too close"), ("crew_overtime_min", "crew overtime min")):
        print(f"   {lab:<30}{sim_raw[k]:>10.0f}{sim_gated[k]:>15.0f}{sim_re[k]:>20.0f}")
    (HERE / "out").mkdir(exist_ok=True)
    (HERE / f"out/eval_{which}.json").write_text(json.dumps({"totals": dict(tot), "by_rule": {k: dict(v) for k, v in by.items()},
                                                             "sim_unchecked": dict(sim_raw), "sim_gated": dict(sim_gated), "sim_replanned": dict(sim_re)}, indent=2))


if __name__ == "__main__":
    main()
