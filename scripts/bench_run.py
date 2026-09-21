#!/usr/bin/env python3
"""Run jev-gate inference on leakage-free snapshots. Writes results.json.

Cost/latency from API usage; verdict ready = merge_ready.noul >= 0.5 (same
threshold as jev_gate.report).
"""
import json
import sys

sys.path.insert(0, "/opt/data/jev-gate")
import jev_gate

sample = json.load(open("/opt/data/jev-gate/scripts/final_sample.json"))
out_path = "/opt/data/jev-gate/scripts/results.json"
done = {(r["url"]) for r in (json.load(open(out_path)) if __import__("os").path.exists(out_path) else [])}
results = list(done and json.load(open(out_path)) or [])

for i, e in enumerate(sample):
    if e["url"] in done:
        continue
    rec = {"url": e["url"], "repo": e["repo"], "number": e["number"], "label": e["label"]}
    try:
        res = jev_gate.ask_jev(e["state"])
        answers = jev_gate.validate(res.get("answers", {}))
        rec.update({
            "pred_ready": answers["merge_ready"]["noul"] >= 0.5,
            "p": answers["merge_ready"]["noul"],
            "blocker": answers["primary_blocker"]["choice"],
            "blocker_conf": answers["primary_blocker"]["confidence"],
            "readiness_score": answers["readiness"]["score"],
            "next": answers["next_action"]["choice"],
            "latency_s": res.get("_latency_s"),
            "cost": res.get("usage", {}).get("cost", 0),
            "error": None,
        })
    except Exception as ex:
        rec.update({"pred_ready": None, "error": str(ex)[:200]})
    results.append(rec)
    json.dump(results, open(out_path, "w"), indent=1)
    print(f"{i+1}/{len(sample)} {e['repo']}#{e['number']} pred={rec.get('pred_ready')} label={e['label']}", flush=True)

ok = [r for r in results if r.get("error") is None]
print(f"\nran {len(results)}, ok {len(ok)}, cost ${sum(r['cost'] for r in ok):.6f}")
