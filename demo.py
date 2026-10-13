"""One jobsite day: an agent plan that forgot the wind limit and battery planning.
-> out/report.html, out/report.json, printed feedback

    python demo.py
"""
import html
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from sitegate.gate import check  # noqa: E402
from sitegate.planner import plan  # noqa: E402
from sitegate.sim import run  # noqa: E402
from sitegate.site import DAY_END, hhmm, make_site, mins  # noqa: E402

e = html.escape
FORGET = {"wind", "battery"}


def pick():
    for seed in range(1, 300):
        s = make_site(seed)
        p, truth = plan(s, forget=FORGET)
        if {"WIND", "BATTERY"} <= {v["code"] for v in truth} and run(s, p).get("stranded"):
            return seed, s, p, truth


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    seed, site, p, truth = pick()
    g = check(site, p)
    good, _ = plan(site)
    drop = {o["task"] for o in g["tasks"] if o["verdict"] != "OK"}
    sims = {"unchecked": run(site, p), "flagged held": run(site, p, drop=drop), "re-planned from feedback": run(site, good)}
    print(f"\n== Jobsite day (synthetic seed {seed}): 3 Cosmic-1 robots, 5 blocks, {len(p['tasks'])} tasks; agent forgot: {', '.join(sorted(FORGET))}")
    print(f"   gate verdict: {g['verdict']}\n")
    for f in g["feedback"]:
        print("   " + f)
    print("\n   simulated day              " + "  ".join(f"{k:>24}" for k in sims))
    for k, lab in (("installed", "modules installed"), ("robot_min_lost", "robot-minutes lost"), ("stranded", "robots stranded")):
        print(f"   {lab:<26} " + "  ".join(f"{sims[n].get(k, 0):>24.0f}" for n in sims))
    ev = {w: json.loads((HERE / f"out/eval_{w}.json").read_text()) for w in ("dev", "heldout") if (HERE / f"out/eval_{w}.json").exists()}
    (HERE / "out/report.json").write_text(json.dumps({"seed": seed, "forgot": sorted(FORGET), "plan": p, "gate": g, "sim": sims}, indent=2))
    (HERE / "out/report.html").write_text(render(site, p, g, sims, ev, seed), encoding="utf-8")
    print("\nwrote out/report.html, out/report.json")


def gantt(site, p, g):
    W, X0, H = 1000, 90, 34
    sx = lambda m: X0 + m / DAY_END * W
    lanes = [r["id"] for r in site["robots"]] + ["Telehandler"]
    verdict = {o["task"]: o["verdict"] for o in g["tasks"]}
    out = []
    slot, lim = site["forecast"]["slot_min"], site["limits"]["gust_mph"]
    for i, gst in enumerate(site["forecast"]["gust_mph"]):
        if gst > lim:
            out.append(f'<rect x="{sx(i * slot):.1f}" y="0" width="{sx(slot) - X0:.1f}" height="{len(lanes) * H + 6}" fill="#f3d6c9"/>')
    for s, e_ in site["forecast"]["lightning"]:
        out.append(f'<rect x="{sx(s):.1f}" y="0" width="{sx(e_ + 30) - sx(s):.1f}" height="{len(lanes) * H + 6}" fill="#e3dcf5"/>')
    col = {"install": "#6f9fd1", "travel": "#b7b7ad", "charge": "#7bb88f", "deliver": "#d6b25e"}
    for t in p["tasks"]:
        lane = lanes.index(t.get("robot", "Telehandler"))
        a, b = mins(t["start"]), mins(t["end"])
        v = verdict[t["id"]]
        stroke = ' stroke="#c0392b" stroke-width="2.5"' if v == "BLOCK" else ' stroke="#c98a12" stroke-width="2"' if v == "FIX" else ""
        out.append(f'<rect x="{sx(a):.1f}" y="{lane * H + 6}" width="{max(2, sx(b) - sx(a) - 1):.1f}" height="{H - 10}" rx="3" fill="{col[t["type"]]}"{stroke}>'
                   f'<title>{e(t["id"])} {e(t["type"])} {t["start"]}-{t["end"]}</title></rect>')
    for i, l in enumerate(lanes):
        out.append(f'<text x="{X0 - 8}" y="{i * H + 26}" text-anchor="end" class="lab">{l}</text>')
    for h in range(0, DAY_END + 1, 60):
        out.append(f'<text x="{sx(h):.1f}" y="{len(lanes) * H + 22}" text-anchor="middle" class="tick">{hhmm(h)}</text>')
    return f'<svg viewBox="0 0 1110 {len(lanes) * H + 30}" width="100%">{"".join(out)}</svg>'


def render(site, p, g, sims, ev, seed):
    fb = "".join(f'<li class="{"b" if f.startswith("[BLOCK]") else "f"}">{e(f)}</li>' for f in g["feedback"])
    simrows = "".join(f"<tr><td>{lab}</td>" + "".join(f"<td>{sims[n].get(k, 0):,.0f}</td>" for n in sims) + "</tr>"
                      for k, lab in (("installed", "modules installed"), ("robot_min_lost", "robot-minutes lost"), ("stranded", "robots stranded"),
                                     ("separation_min", "minutes robots too close")))
    evrows = ""
    for k, v in ev.items():
        t = v["totals"]
        evrows += (f"<tr><td>{'held-out (frozen in git first)' if k == 'heldout' else 'dev'}</td><td>{t['days']}</td><td>{t['valid_flagged']}/{t['days']}</td>"
                   f"<td>{t['bad_days_stopped']}/{t['bad_days']}</td><td>{t['caught']}/{t['planted']}</td>"
                   f"<td>{v['sim_unchecked'].get('stranded', 0):.0f} → {v['sim_replanned'].get('stranded', 0):.0f}</td>"
                   f"<td>{v['sim_unchecked'].get('separation_min', 0):,.0f} → {v['sim_replanned'].get('separation_min', 0):,.0f}</td>"
                   f"<td>{v['sim_unchecked'].get('installed', 0):,.0f} → {v['sim_replanned'].get('installed', 0):,.0f}</td></tr>")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Jobsite plan gate</title><style>
:root{{--bg:#f4f3ee;--fg:#1d1f1b;--mut:#666a61;--card:#fff;--line:#e2e0d7}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1120px;margin:0 auto;padding:26px 16px 60px}}h1{{font-size:26px;margin:0 0 4px}}h2{{font-size:18px;margin:28px 0 10px}}.sub,.mut{{color:var(--mut)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}}.lab{{font-size:13px;fill:var(--fg)}}.tick{{font-size:11px;fill:var(--mut)}}
ul{{margin:0;padding-left:18px}}li{{margin:5px 0;font-size:14px}}li.b{{color:#b0392b}}li.f{{color:#8a5f0d}}
table{{width:100%;border-collapse:collapse;font-size:14px}}td,th{{border-top:1px solid var(--line);padding:6px;text-align:left}}.key span{{display:inline-block;width:12px;height:12px;border-radius:2px;margin:0 4px 0 12px;vertical-align:-1px}}
</style></head><body><main>
<h1>Check the orchestrator's plan before the robots move</h1>
<div class="sub">One synthetic solar jobsite day (seed {seed}): 3 robots, 5 blocks, {len(p['tasks'])} tasks from an agent that forgot the gust limit and battery planning. Unofficial prototype by Kushagra Pratap Singh, not affiliated with Cosmic Robotics.</div>
<h2>Agent's plan · gate verdict: {g['verdict']}</h2><div class="card">{gantt(site, p, g)}
<div class="key mut"><span style="background:#6f9fd1"></span>install<span style="background:#b7b7ad"></span>travel<span style="background:#7bb88f"></span>charge<span style="background:#d6b25e"></span>pallet delivery<span style="background:#f3d6c9"></span>gusts over {site['limits']['gust_mph']} mph<span style="background:#e3dcf5"></span>lightning stop · red outline = BLOCK, amber = FIX</div></div>
<h2>Feedback the agent gets back</h2><div class="card"><ul>{fb}</ul></div>
<h2>The same day, simulated</h2><div class="card"><table><tr><th></th>{''.join(f'<th>{n}</th>' for n in sims)}</tr>{simrows}</table>
<div class="mut" style="margin-top:6px">"Re-planned" = the plan with the forgotten rules restored, a deterministic stand-in for the agent acting on the feedback.</div></div>
<h2>Benchmark: agent plans that forgot one rule</h2><div class="card" style="overflow-x:auto"><table><tr><th>set</th><th>days</th><th>valid plans flagged</th><th>bad plans stopped</th><th>broken tasks caught</th><th>robots stranded</th><th>minutes too close</th><th>modules installed</th></tr>{evrows}</table>
<div class="mut" style="margin-top:6px">Last three columns: unchecked plan → re-planned from feedback.</div></div>
</main></body></html>"""


if __name__ == "__main__":
    main()
