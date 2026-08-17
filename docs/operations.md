# 100Ways operations

How to run, monitor, and recover 100Ways in production.

## Triggering a sync

### Event-driven (the only way)

100Ways no longer runs on a schedule. The operator triggers a sync manually
via **Actions → CI → Run workflow**. Inputs:

| input | type | default | purpose |
|---|---|---|---|
| `run_full_validation` | bool | false | Run the 19-stage candidate pipeline |
| `publish_candidate_pr` | bool | false | Permit #344 to open/update a review PR after every gate passes |
| `acknowledge_inherited_case_collisions` | bool | false | Acknowledge that the candidate carries inherited source-path collisions |
| `bootstrap` | bool | false | Accept the first large upstream jump with `review_required=True` |
| `threshold_override` | int | 0 (=100) | Lower the commit-stream threshold for one run |

The default commit-stream threshold is 100 upstream commits. A sync will
not start until the upstream has at least 100 commits since the last
merged NasTech baseline.

### Why no schedule

100Ways has 1 operator and 0 production users. A scheduled cron burns CI
minutes every 30 minutes for no benefit. Event-driven means a sync only
happens when you want one.

## Reading the Telegram alerts

The bot replies to messages from the configured `TELEGRAM_CHAT_ID` only.
Reply keyboard:

```
[Status]  [Progress]
[Errors]  [Remaining]
[PR status]
[Help]
```

Free-form questions are answered by Ollama Cloud (`gemma4:31b-cloud`)
when `OLLAMA_API_KEY` is set. AI answers are advisory only — they can
never authorize a merge, tag, release, deployment, or gate bypass.

## Reading the GitHub Pages status

`https://nastechresearch.github.io/100Ways/` — schema
`100ways.public-status.v1`. Sections:

- **STATE** — `CURRENT`, `WARMING`, `THRESHOLD REACHED`, `AWAITING REVIEW`, or `DEGRADED`
- **SOURCE** — abbreviated commit refs and the live Actions run link
- **PROGRESS** — pending commits vs. the configured threshold
- **GATES** — the latest gate matrix
- **HISTORY** — last 12 events

The Pages surface is informational only. It never becomes a control
plane for merging, tagging, releasing, or deploying.

## Common failure modes

### Pages build red with HTTP 429

The Pages workflow fetches the upstream directly via `scripts/fetch_public_repo.sh`
which has bounded exponential backoff. If it still fails after 4
attempts, the Pages payload switches to a `DEGRADED` state and still
deploys.

The main pipeline (`ci.yml`) handles rate limits via
`_recover_complete_history`'s own backoff (10s/40s, 3 attempts).

### Hardened weekly gate FAIL

```
Run hardened weekly decision gate — exit 1
```

Means one of the 50+ weekly-sync capabilities found an issue. The
analyzer step writes a structured finding to
`$RUNNER_TEMP/weekly-gate-analysis.json`. The summary lists the first
few findings.

For a *fresh project with a large backlog*, run with
`bootstrap=true` and `publish_candidate_pr=true` — the gate accepts one
jump and emits `review_required=true`.

### dependabot bumps break CI

`actions/cache` is pinned to v4.2.0 because v5/v6 changed cache-key
semantics (#187). The dependabot config in `.github/dependabot.yml`
explicitly ignores `actions/cache` 5.x and 6.x; if a bump slips through,
revert the dependabot PR and pin the SHA.

## Rolling back a published PR

100Ways cannot open a PR it didn't open. To roll back:

1. Close the PR in `nastech-agent`
2. (optional) Revert the merge commit in `nastech-agent`'s `main`
3. The next scheduled `ci.yml` run will rebuild the candidate from the
   new HEAD — no manual clean-up needed

## Resetting the upstream ledger

If the ledger becomes corrupted (or you want to re-bootstrap from a
specific SHA):

1. SSH to the runner or run `ci.yml` with `workflow_dispatch`
2. Delete the ledger at `$RUNNER_TEMP/100ways-state/upstream-ledger.json`
3. Pass `bootstrap=true` and the desired upstream SHA via `expected_upstream_sha`
   (not exposed as an input today — would require a 2-line change)

## State of the public bot

The Telegram bot is a stateless wrapper over `hundredways/telegram_agent.py`.
It only ever reads the latest 40 events from the Actions cache and
responds with formatted strings. There is no persistent database.

## Where to find logs

- **Pages** run logs: Actions → 100Ways Pages
- **Main CI** run logs: Actions → CI
- **Plan-only** when triggered on `workflow_dispatch` without `run_full_validation`
