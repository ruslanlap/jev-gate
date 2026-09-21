#!/usr/bin/env python3
"""jev-gate — triage a GitHub PR with a typed decision model (TypeSafe Jev via OpenRouter).

One API call, five typed questions, sub-second, ~$0.0001 per PR.
Zero dependencies beyond Python stdlib.

Usage:
  jev-gate <PR-URL>            # e.g. jev-gate https://github.com/owner/repo/pull/123
  jev-gate --markdown <URL>    # one-line verdict for comments/CI
  jev-gate --batch <URL> ...   # triage many PRs, sorted table
  jev-gate --self-check        # offline sanity check of validation logic

Exit codes: 0 = merge-ready, 1 = blocked, 2 = error.
Keys: OPENROUTER_API_KEY (required), GH_TOKEN / GITHUB_TOKEN (optional, higher rate limit).
"""
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.13"
MAX_STATE_CHARS = 28000  # Jev context is 32K tokens; compact timeline keeps us far under

# The model may only ever return identifiers from these sets — never free text
# that could be executed or interpolated. Enforced by validate().
QUESTIONS = {
    "merge_ready": {
        "type": "noul",
        "instructions": "Is this PR safe to merge right now, with no unresolved blockers?",
        "criteria": {
            "true": "all blockers resolved, checks and reviews green",
            "false": "something still blocks merging",
        },
    },
    "primary_blocker": {
        "type": "choice",
        "instructions": "What is the single primary blocker for merging this PR right now?",
        "criteria": {
            "none": "no blocker, mergeable",
            "cla": "CLA not signed",
            "certificate": "installer signature/certificate invalid or untrusted",
            "validation": "manifest validation or CI checks failing",
            "changes_requested": "reviewer requested changes not yet addressed",
            "other": "some other issue",
        },
    },
    "cla_blocking": {
        "type": "noul",
        "instructions": "Is an unsigned CLA currently blocking this PR?",
        "criteria": {"true": "CLA is still blocking", "false": "CLA signed or not required"},
    },
    "readiness": {
        "type": "score",
        "instructions": "How close is this PR to being merge-ready?",
        "criteria": ["blocked", "needs work", "nearly ready", "merge ready"],
    },
    "next_action": {
        "type": "choice",
        "instructions": "What is the single most useful next action for the PR author?",
        "criteria": {
            "merge": "merge it now",
            "wait_ci": "wait for checks to finish",
            "resign_installer": "re-sign or fix installer certificate/signature",
            "fix_manifest": "fix manifest fields per validation output",
            "address_review": "address reviewer's requested changes",
            "comment": "ask reviewers/moderators a clarifying question",
            "close": "close or resubmit the PR",
        },
    },
}
READINESS_IDS = ("blocked", "needs work", "nearly ready", "merge ready")


# ---------------------------------------------------------------- GitHub API

def gh_api(url):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "jev-gate"}
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GitHub API {e.code} for {url}") from e


def parse_pr_url(url):
    m = re.match(r"^https://github\.com/([^/]+)/([^/]+)/pull/(\d+)", url.strip().rstrip("/"))
    if not m:
        raise ValueError(f"not a GitHub PR URL: {url}")
    owner, repo, num = m.groups()
    return owner, repo, num, f"{owner}/{repo}#{num}"


def fetch_pr(url):
    """Fetch PR + reviews + issue comments via the GitHub REST API."""
    owner, repo, num, _ = parse_pr_url(url)
    base = f"https://api.github.com/repos/{owner}/{repo}"
    pr = gh_api(f"{base}/pulls/{num}")
    reviews = gh_api(f"{base}/pulls/{num}/reviews")
    comments = gh_api(f"{base}/issues/{num}/comments?per_page=100")
    return build_state(pr, reviews, comments)


def build_state(pr, reviews, comments):
    """Compress REST payloads into a compact timeline the model can read."""
    label = f"{pr['base']['repo']['full_name']}#{pr['number']}"
    lines = [
        f"PR: {label}",
        f"Title: {pr['title']}",
        f"Author: {pr['user']['login']}",
        f"State: {pr['state']} mergeable={pr.get('mergeable')}",
    ]
    if pr.get("additions") is not None:
        lines.insert(-1, f"Changes: +{pr['additions']}/-{pr['deletions']} in {pr['changed_files']} files")
    lines += ["", "Timeline:"]
    events = []
    for r in reviews:
        body = f" — {r['body'][:400]}" if r.get("body") else ""
        events.append(
            (r["submitted_at"], f"review by {r['user']['login']} [{r.get('author_association', '')}]: "
                                f"state={r['state']}{body}")
        )
    for c in comments:
        events.append(
            (c["created_at"], f"comment by {c['user']['login']} [{c.get('author_association', '')}]: "
                              f"{c['body'][:400]}")
        )
    lines.extend(e[1] for e in sorted(events))
    lines += ["", "PR body (first 1500 chars):", (pr.get("body") or "")[:1500]]
    return label, "\n".join(lines)[:MAX_STATE_CHARS]


# ------------------------------------------------------------------ Jev call

def ask_jev(state, questions=QUESTIONS):
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set (https://openrouter.ai/keys)")
    body = {"model": MODEL, "state": state, "questions": questions}
    req = urllib.request.Request(
        DECISIONS_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        data=json.dumps(body).encode(),
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"decisions API HTTP {e.code}: {e.read().decode()[:200]}") from e
    out["_latency_s"] = round(time.time() - t0, 2)
    return out


# ------------------------------------------------- answer validation (strict)
# A decision is only ever acted on if it is structurally sound; otherwise we
# refuse with a clear error rather than guess (pattern: jev-ultrafast).

def _finite01(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and 0 <= x <= 1


def _check_probs(probs, ids):
    return (
        isinstance(probs, dict)
        and set(probs) == set(ids)
        and all(_finite01(v) for v in probs.values())
        and abs(sum(probs.values()) - 1) < 0.02
    )


def validate(answers):
    """Validate every answer against its declared question schema.

    Returns a dict {name: answer} on success; raises ValueError naming the
    broken field otherwise. Validates ALL questions — no partial trust.
    """
    if not isinstance(answers, dict):
        raise ValueError("answers: not an object")
    for name, q in QUESTIONS.items():
        a = answers.get(name)
        if not isinstance(a, dict):
            raise ValueError(f"{name}: missing answer")
        t = q["type"]
        if t == "noul":
            if not _finite01(a.get("noul")):
                raise ValueError(f"{name}: noul {a.get('noul')!r} not in [0,1]")
        elif t == "choice":
            ids = set(q["criteria"])
            probs = a.get("probabilities")
            if a.get("choice") not in ids:
                raise ValueError(f"{name}: choice {a.get('choice')!r} not in {sorted(ids)}")
            if not _check_probs(probs, ids):
                raise ValueError(f"{name}: probabilities invalid")
            if not _finite01(a.get("confidence")):
                raise ValueError(f"{name}: confidence not in [0,1]")
            if probs[a["choice"]] < max(probs.values()) - 1e-6:
                raise ValueError(f"{name}: chosen option is not the argmax")
        elif t == "score":
            n = len(q["criteria"])
            legend = a.get("legend")
            if not (isinstance(legend, dict) and {int(k) for k in legend if str(k).isdigit()} == set(range(n))):
                raise ValueError(f"{name}: legend does not match criteria")
            score = a.get("score")
            if not (isinstance(score, (int, float)) and 0 <= score <= n - 1):
                raise ValueError(f"{name}: score {score!r} not in [0,{n - 1}]")
            if "probabilities" in a and not _check_probs(a["probabilities"], [str(i) for i in range(n)]):
                raise ValueError(f"{name}: probabilities invalid")
    return answers


# ---------------------------------------------------------------------- main

def report(label, answers, usage, latency, model):
    ready = answers["merge_ready"]["noul"] >= 0.5
    r = answers["readiness"]
    idx = min(int(r["score"] + 0.5), len(READINESS_IDS) - 1)
    out = [
        f"PR       {label}",
        f"verdict  {'MERGE-READY' if ready else 'NOT READY'} "
        f"(readiness: {READINESS_IDS[idx]}, score {r['score']:.2f}/{len(READINESS_IDS) - 1}, "
        f"p={ready:.2f})",
        f"blocker  {answers['primary_blocker']['choice']} "
        f"(confidence {answers['primary_blocker']['confidence']:.2f})",
        f"cla      blocking p={answers['cla_blocking']['noul']:.2f}",
        f"next     {answers['next_action']['choice']} "
        f"(confidence {answers['next_action']['confidence']:.2f})",
        f"         cost ${usage.get('cost', 0):.6f} · {latency}s · model {model}",
    ]
    return "\n".join(out), ready


def to_markdown(label, answers, usage, latency, model):
    """One-line verdict for comments/CI summaries."""
    ready = answers["merge_ready"]["noul"] >= 0.5
    b, n = answers["primary_blocker"], answers["next_action"]
    emoji = "✅" if ready else "🚫"
    return (
        f"**{emoji} jev-gate: {'MERGE-READY' if ready else 'NOT READY'}** "
        f"(readiness {answers['readiness']['score']:.1f}/3 · blocker `{b['choice']}` conf {b['confidence']:.2f} · "
        f"next `{n['choice']}` conf {n['confidence']:.2f}) · {latency}s · ${usage.get('cost', 0):.6f} · `{model}`"
    )


def triage(url):
    """fetch → ask → validate → report. Returns (label, answers, res)."""
    label, state = fetch_pr(url)
    res = ask_jev(state)
    answers = validate(res.get("answers", {}))
    return label, answers, res


def run_batch(urls):
    """Triage many PRs; print a sorted table. Exit 0 if all parse, else 2."""
    rows, errors = [], []
    for u in urls:
        try:
            label, answers, res = triage(u)
        except (RuntimeError, ValueError) as e:
            errors.append((u, str(e)))
            continue
        rows.append({
            "pr": label,
            "ready": answers["merge_ready"]["noul"] >= 0.5,
            "blocker": answers["primary_blocker"]["choice"],
            "conf": answers["primary_blocker"]["confidence"],
            "next": answers["next_action"]["choice"],
            "score": answers["readiness"]["score"],
        })
    rows.sort(key=lambda r: (r["ready"], -r["conf"]))
    print(f"{'PR':44} {'VERDICT':10} {'BLOCKER':18} {'CONF':>4}  NEXT")
    for r in rows:
        print(f"{r['pr']:44} {'READY' if r['ready'] else 'BLOCKED':10} {r['blocker']:18} "
              f"{r['conf']:4.2f}  {r['next']}")
    for u, e in errors:
        print(f"error  {u}: {e}", file=sys.stderr)
    return 0 if not errors else 2


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    if argv[0] == "--self-check":
        return self_check()
    if argv[0] == "--markdown":
        try:
            label, answers, res = triage(argv[1])
            print(to_markdown(label, answers, res.get("usage", {}), res.get("_latency_s", "?"), res.get("model", MODEL)))
            return 0
        except (RuntimeError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    if argv[0] == "--batch":
        if len(argv) < 2:
            print("usage: jev-gate --batch <url> [url ...]", file=sys.stderr)
            return 2
        return run_batch(argv[1:])
    try:
        label, answers, res = triage(argv[0])
        text, ready = report(label, answers, res.get("usage", {}), res.get("_latency_s", "?"), res.get("model", MODEL))
        print(text)
        return 0 if ready else 1
    except (RuntimeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


def self_check():
    """Offline sanity check of validate()."""
    ok = {
        "merge_ready": {"noul": 0.02},
        "primary_blocker": {"choice": "certificate", "confidence": 0.99,
                            "probabilities": {"none": 0, "cla": 0, "certificate": 1, "validation": 0,
                                              "changes_requested": 0, "other": 0}},
        "cla_blocking": {"noul": 0.05},
        "readiness": {"score": 0.41, "legend": {"0": "blocked", "1": "needs work", "2": "nearly ready", "3": "merge ready"},
                      "probabilities": {"0": 0.61, "1": 0.38, "2": 0.01, "3": 0}},
        "next_action": {"choice": "resign_installer", "confidence": 0.98,
                        "probabilities": {"merge": 0, "wait_ci": 0, "resign_installer": 0.98,
                                          "fix_manifest": 0.01, "address_review": 0.01, "comment": 0, "close": 0}},
    }
    validate(ok)
    for bad, mut in [
        ("unknown choice", lambda a: a["primary_blocker"].update(choice="rm -rf")),
        ("probs don't sum to 1", lambda a: a["next_action"]["probabilities"].update(merge=0.5)),
        ("probs key mismatch", lambda a: a["primary_blocker"]["probabilities"].pop("none")),
        ("noul out of range", lambda a: a["merge_ready"].update(noul=1.5)),
        ("missing answer", lambda a: a.pop("cla_blocking")),
        ("score out of range", lambda a: a["readiness"].update(score=7)),
    ]:
        broken = json.loads(json.dumps(ok))
        mut(broken)
        try:
            validate(broken)
        except ValueError:
            pass
        else:
            print(f"self-check FAILED: {bad} not rejected")
            return 1
    print("self-check OK")
    return 0


def main_cli():
    """Console-script entry point (pipx)."""
    return main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
