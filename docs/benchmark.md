# Benchmark: verdict quality on 39 closed PRs with known outcome

Date: 2026-09-21 · model `typesafe/jev-1.13` · n = 39 · full data: [`benchmark-data.json`](benchmark-data.json)

## What was measured

Whether `jev-gate` verdicts correlate with the actual outcome of a PR (merged = ready / closed-unmerged = blocked), judged from a timeline snapshot taken **before** the final action. This is a disillusionment check, not a demo: not "does the verdict look right on a live PR" but "does the verdict track reality on a sample where the answer is known in advance".

## Methodology

**Sample.** 39 closed PRs from 4 public repos: `microsoft/winget-pkgs` (18: 9 merged + 9 rejected), `python/cpython` (9), `vercel/next.js` (7), `ruslanlap/CmdPal-Definition` (5). Non-draft, human authors.

**Ground truth.** `ready` ⇔ PR was merged. `blocked` ⇔ closed unmerged **AND** the rejection cause is visible in the pre-final timeline (failed winget validation, moderator close, rejecting review). If no cause is visible, the PR is excluded — the label is not guessed.

**Leakage removal (the key part).** For merged PRs the current REST state contains `merged=true`, and `build_state` would hand that to the model — the answer would sit in the prompt. So for every PR a strict pre-final snapshot was built:

- events (reviews/comments) with `submitted_at`/`created_at` ≥ `merged_at`/`closed_at` were dropped — the final comment often announces the close/merge itself (on average ~2 events dropped per PR);
- the PR payload was scrubbed: `state='open'`, `merged=false`, `mergeable=None`;
- labels and the final state never enter the prompt.

The prompt for each PR is the same `state` string `jev_gate.build_state` produces, just with the truncated timeline.

**Run.** `jev_gate.ask_jev(state)` + `validate()` — the exact CLI path. Threshold: `merge_ready.noul ≥ 0.5`.

**Exclusions.** Of 67 fetched PRs, 28 were excluded: 8 closed-unmerged with no visible rejection cause (the label would be guesswork), the rest — sample balancing (25→9 blocked in winget) and PRs with no events at all.

## Results

| metric | value |
|---|---|
| accuracy (ready vs blocked) | **16/39 = 41.0%** |
| baseline (always "blocked") | 22/39 = 56.4% |
| precision (ready) | 3/3 = 100% |
| recall (ready) | 3/26 = 11.5% |
| mean confidence on correct vs wrong | 0.88 / 0.79 |
| latency p50 / p90 | 0.38 s / 0.47 s |
| cost | $0.002159 total · $0.000055 per PR |

### Confusion matrix

| | pred READY | pred BLOCKED |
|---|---|---|
| **actually ready (merged)** | TP = 3 | FN = 23 |
| **actually blocked (rejected)** | FP = 0 | TN = 13 |

### Per-repo (correct)

| repo | correct |
|---|---|
| microsoft/winget-pkgs | 9/18 |
| vercel/next.js | 4/7 |
| python/cpython | 3/9 |
| ruslanlap/CmdPal-Definition | 0/5 |

## What it means

Read as a **readiness predictor** on pre-final snapshots, the tool fails: accuracy 41% is below the always-blocked baseline. Read as a **gate**, the profile is the useful half: FP = 0 — across 39 PRs it never once called a rejected PR merge-ready. It behaves as a conservative filter: "NOT READY" is the default answer for anything short of visible completeness.

The cause is visible in the data: a merged PR's pre-final snapshot mostly contains no positive signal yet — checks still running, review not posted, moderator approval not arrived. The model sees an incomplete timeline and honestly answers "not ready". Several FN cases are literally correct statements about the snapshot moment ("changes_requested review standing" — merged only after the author addressed it days later). That is a limit of judging by events rather than diffs, not leakage and not a validation bug.

**Use accordingly:** valuable as a cheap "is anything visibly blocking this PR right now" check and a never-green-lights-a-bad-PR gate; not valuable as a merge oracle.

## What this does NOT prove

- **Small sample.** 39 PRs, 95% CI for accuracy ≈ ±15 pp. The 41% vs 56% gap is statistically fragile at this n.
- **Label from final state, not a moderator diagnosis.** "Merged" ≠ "was ready at snapshot time": most PRs merge hours after the snapshot, when green checks arrive. Part of the FN mass is "correctly blocked at snapshot, ready later".
- **Mixed sample.** 18/39 are winget-pkgs with its bot noise; CmdPal-Definition is a personal repo where all 5 PRs were misjudged. Results do not transfer to "an arbitrary PR".
- **Exclusions bias the sample.** The 8 closed-unmerged PRs without a visible cause were removed — exactly the ones where the model would have had to guess hardest.
- **Not tested:** robustness on long timelines, run-to-run stability (temperature unknown), behavior on open PRs.

## Reproduce

```sh
python3 scripts/bench_sample.py     # collect closed PRs (GitHub API)
python3 scripts/bench_snapshots.py  # leakage-free snapshots
python3 scripts/bench_run.py        # run through jev_gate.ask_jev
```

Needs `GH_TOKEN` and `OPENROUTER_API_KEY`. Full run costs ≈ $0.002.
