#!/usr/bin/env python3
"""Top up blocked (closed-unmerged) PRs from winget-pkgs, sort=created."""
import json
import sys
from collections import Counter

sys.path.insert(0, "/opt/data/jev-gate/scripts")
sys.path.insert(0, "/opt/data/jev-gate")
from bench_sample import pulls, take
from bench_snapshots import snapshot_pr

acc = []
wl = pulls("microsoft/winget-pkgs", 400, sort="created")
take("microsoft/winget-pkgs", wl, False, 25, acc)
snap = json.load(open("/opt/data/jev-gate/scripts/snapshots.json"))
have = {(o["repo"], o["number"]) for o in snap}
new = [a for a in acc if (a["repo"], a["number"]) not in have]
for i, e in enumerate(new):
    try:
        snap.append(snapshot_pr(e))
    except Exception as ex:
        snap.append({**e, "excluded": True, "exclude_reason": f"fetch error: {ex}", "state": None, "label": None})
    print(i + 1, e["number"], flush=True)
json.dump(snap, open("/opt/data/jev-gate/scripts/snapshots.json", "w"), indent=1)
kept = [o for o in snap if not o["excluded"]]
print("total:", len(snap), "usable:", len(kept), Counter(o["label"] for o in kept))
