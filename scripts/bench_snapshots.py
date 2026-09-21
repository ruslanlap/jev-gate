#!/usr/bin/env python3
"""Fetch full timelines for the sample, build leakage-free pre-final snapshots.

For each PR in sample.json:
  1. Fetch PR detail + reviews + issue comments.
  2. Freeze ground truth: merged / closed-unmerged (+ closure signals).
  3. Snapshot = timeline strictly BEFORE final action:
     - cutoff = min(merged_at, closed_at); drop review/comment events with
       submitted_at/created_at >= cutoff (the last event is often the closure
       itself: 'Closing', merge announcement, final rejection).
     - PR payload scrubbed: state='open', merged=False, mergeable=None.
  4. Honesty exclusion: closed-unmerged PRs get label 'blocked' only if the
     timeline shows a rejection cause; else EXCLUDED (would force the model
     to guess intent). Merged PRs are 'ready' by definition.
  5. Store the exact state string that will be sent to the model.

Writes snapshots.json: [{url, repo, number, label, final_state, cutoff_event_count, excluded, exclude_reason, state}]
"""
import json
import re
import time
import urllib.request

TOKEN = open("/opt/data/.github_token").read().strip()
HDR = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json", "User-Agent": "jev-bench"}
API = "https://api.github.com"

REJECT_PAT = re.compile(
    r"(validation (bot: )?(failed|found)| automated test| step: |"
    r"incompatible| licence| license| hash mismatch| hash failed| failed hash|"
    " doesn't match| silver| bom_tool| wingetbot| closing| closed| removed from|"
    r"duplicate| spam| abuse| not accepted| policy)",
    re.I,
)
OK_PAT = re.compile(r"(manifest validation succeeded| no errors| autoclose disabled)", re.I)


def gh(url):
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def snapshot_pr(entry):
    repo, num = entry["repo"], entry["number"]
    base = f"{API}/repos/{repo}"
    pr = gh(f"{base}/pulls/{num}")
    reviews = gh(f"{base}/pulls/{num}/reviews?per_page=100")
    comments = gh(f"{base}/issues/{num}/comments?per_page=100")
    final_at = pr.get("merged_at") or pr["closed_at"]
    merged = bool(pr.get("merged_at"))

    events = []
    for r in reviews:
        events.append((r["submitted_at"], f"review by {r['user']['login']} [{r.get('author_association','')}]: state={r['state']}"
                      + (f" — {r['body'][:400]}" if r.get("body") else "")))
    for c in comments:
        events.append((c["created_at"], f"comment by {c['user']['login']} [{c.get('author_association','')}]: {c['body'][:400]}"))
    kept = [e for e in sorted(events) if e[0] < final_at]
    dropped = len(events) - len(kept)

    pr2 = dict(pr)
    pr2["state"] = "open"
    pr2["merged"] = False
    pr2["mergeable"] = None

    import jev_gate
    label, state = jev_gate.build_state(pr2, [], [])
    # rebuild via build_state with filtered pseudo-events is messy; do it directly:
    body = (pr.get("body") or "")[:1500]
    parts = [
        f"PR: {label}",
        f"Title: {pr['title']}",
        f"Author: {pr['user']['login']}",
        "State: open mergeable=None",
    ]
    if pr.get("additions") is not None:
        parts.insert(3, f"Changes: +{pr['additions']}/-{pr['deletions']} in {pr['changed_files']} files")
    parts += ["", "Timeline:"] + [e[1] for e in kept] + ["", "PR body (first 1500 chars):", body]
    state = "\n".join(parts)[: jev_gate.MAX_STATE_CHARS]

    # honesty label for closed-unmerged
    excluded, reason = False, ""
    if not merged:
        tail = " \n ".join(e[1] for e in kept)
        if not REJECT_PAT.search(tail):
            excluded = True
            reason = "no rejection cause visible in pre-final timeline"

    return {
        "url": entry["url"], "repo": repo, "number": num,
        "label": "ready" if merged else "blocked",
        "final_state": "merged" if merged else "closed_unmerged",
        "events_total": len(events), "events_kept": len(kept), "events_dropped": dropped,
        "excluded": excluded, "exclude_reason": reason,
        "state": state,
    }


def main():
    sample = json.load(open("/opt/data/jev-gate/scripts/sample.json"))
    out = []
    for i, e in enumerate(sample):
        for attempt in range(3):
            try:
                out.append(snapshot_pr(e))
                break
            except Exception as ex:
                if attempt == 2:
                    out.append({**e, "excluded": True, "exclude_reason": f"fetch error: {ex}", "state": None, "label": None})
                else:
                    time.sleep(3)
        print(f"{i+1}/{len(sample)} {e['repo']}#{e['number']} done", flush=True)
    json.dump(out, open("/opt/data/jev-gate/scripts/snapshots.json", "w"), indent=1)
    kept = [o for o in out if not o["excluded"]]
    from collections import Counter
    print(f"snapshots: {len(out)} total, {len(kept)} usable", dict(Counter(o['label'] for o in kept)))


if __name__ == "__main__":
    main()
