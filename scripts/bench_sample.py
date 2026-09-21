#!/usr/bin/env python3
"""Collect a sample of closed PRs with merged/unmerged ground truth.

Sources (mixed repos, merged + rejected):
  - microsoft/winget-pkgs  merged + closed-unmerged (bot-rejected manifests)
  - ruslanlap/CmdPal-Definition merged
  - python/cpython, vercel/next.js small doc PRs (merged + closed-unmerged)

Writes sample.json: [{url, repo, number, merged_at, closed_at, label}]
"""
import json
import urllib.parse
import urllib.request

TOKEN = open("/opt/data/.github_token").read().strip()
HDR = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json", "User-Agent": "jev-bench"}
API = "https://api.github.com"


def gh(url):
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def pulls(repo, n, state="closed", sort="updated"):
    out, page = [], 1
    while len(out) < n:
        items = gh(f"{API}/repos/{repo}/pulls?state={state}&sort={sort}&direction=desc&per_page=100&page={page}")
        if not items:
            break
        out.extend(items)
        page += 1
    return out[:n]


def take(repo, items, want_merged, need, acc):
    for it in items:
        if len([a for a in acc if a["repo"] == repo and a["merged"] == want_merged]) >= need:
            return
        if it["draft"] or "bot" in it["user"]["login"].lower():
            continue
        merged = bool(it.get("merged_at"))
        if merged != want_merged:
            continue
        acc.append({
            "url": f"https://github.com/{repo}/pull/{it['number']}",
            "repo": repo,
            "number": it["number"],
            "merged": merged,
            "merged_at": it.get("merged_at"),
            "closed_at": it.get("closed_at"),
            "title": it["title"][:80],
        })


def main():
    acc = []
    # winget-pkgs: high-volume, both outcomes, ~equal split
    wl = pulls("microsoft/winget-pkgs", 220)
    take("microsoft/winget-pkgs", wl, True, 9, acc)
    take("microsoft/winget-pkgs", wl, False, 9, acc)
    # CmdPal-Definition: merged only (small repo)
    cl = pulls("ruslanlap/CmdPal-Definition", 40)
    take("ruslanlap/CmdPal-Definition", cl, True, 5, acc)
    take("ruslanlap/CmdPal-Definition", cl, False, 4, acc)
    # cpython doc PRs: merged and unmerged, small diffs
    py = pulls("python/cpython", 150)
    take("python/cpython", py, True, 7, acc)
    take("python/cpython", py, False, 7, acc)
    # next.js
    nx = pulls("vercel/next.js", 120)
    take("vercel/next.js", nx, True, 5, acc)
    take("vercel/next.js", nx, False, 5, acc)
    # labels
    for a in acc:
        a["label"] = "ready" if a["merged"] else "blocked"
    # dedupe by (repo, number)
    seen, dedup = set(), []
    for a in acc:
        k = (a["repo"], a["number"])
        if k not in seen:
            seen.add(k)
            dedup.append(a)
    json.dump(dedup, open("/opt/data/jev-gate/scripts/sample.json", "w"), indent=1)
    from collections import Counter
    print(f"sample: {len(dedup)} PRs", dict(Counter((a['repo'], a['merged']) for a in dedup)))


if __name__ == "__main__":
    main()
