# 100Ways security model

The security model is the contract between 100Ways and the
`nastech-agent` fork. Read this before changing anything in the
analyzer, notifier, or Pages builder.

## Threat model

100Ways runs as a scheduled CI pipeline. It has:

- read access to `nastechresearch/hermes-agent` (public)
- read access to `nastechresearch/nastech-agent` (public, shallow clone)
- write access to a review PR on `nastech-agent` (only via the `#344`
  job after every gate passes)
- write access to the `github-pages` environment for the public site
- access to three secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
  `OLLAMA_API_KEY`

What 100Ways must never do, under any circumstance:

- merge, tag, release, deploy, or push to a protected branch
- include raw log paths, tokens, or chat IDs in any published artifact
- bypass the hardened weekly gate
- accept input from sources other than the configured `TELEGRAM_CHAT_ID`

## Trust boundaries

```
        +-----------------------+
        |  github.com (public)  |   <-- hermes-agent, nastech-agent
        +-----------+-----------+
                    |
                    v
        +-----------+-----------+
        | 100Ways engine (CI)   |   <-- secrets live HERE
        +-----------+-----------+
                    |
        +-----------+-----------+
        |   Pages / Telegram    |   <-- sanitized output only
        +-----------------------+
```

Anything that crosses the bottom edge must pass through the redaction
boundary. Anything that crosses the right edge into the nastech-agent
fork must pass through the hardened gate.

## Redaction boundary

`hundredways/actions_analyzer.py:redact()` is the single chokepoint for
sanitizing output before it leaves the engine. It strips:

- classic GitHub PATs: `gh[pousr]_[A-Za-z0-9_\-]{20,}`
- fine-grained PATs: `github_pat_[A-Za-z0-9_]{20,}`
- OAuth tokens: `gho_`, `ghs_`, `ghr_` (20+ chars)
- GitLab PATs: `glpat-[A-Za-z0-9_\-]{20,}`
- env-style secret keys: `(?:bot|token|secret|password|api[_-]?key)\s*[:=]\s*[^\s,;]+`
- basic auth in URLs: `https://user:pass@host/...`

Tests in `tests/test_analyzer_redaction.py` pin every pattern.

## Publication boundary

The Pages payload is the only thing 100Ways publishes. `scripts/build_pages_status.py:build()`
is the chokepoint. It enforces:

- `schema = "100ways.public-status.v1"` (versioned for backward compat)
- all SHAs truncated to 12 hex chars
- gates / history capped at 12 entries
- `privacy` and `publication` strings always present
- non-numeric `pending_commits` / `threshold` fall back to defaults
  (no `ValueError` that would crash the build)

Tests in `tests/test_pages_payload.py` pin every property.

## Telegram boundary

`hundredways/telegram_agent.py` is the only module that talks to the
Telegram Bot API. Constraints:

- the bot accepts messages from `TELEGRAM_CHAT_ID` only
- conversation memory is bounded to the latest 40 events
- AI answers (when `OLLAMA_API_KEY` is set) are advisory only — they
  cannot authorize a merge, tag, release, deployment, or gate bypass
- no file is ever uploaded to Telegram; only text messages

## Gate boundary

`hundredways/weekly_sync.py:build_weekly_report()` is the single
chokepoint that decides whether the candidate is allowed to be
published. Constraints:

- the gate runs 50+ capability audits; any `block`-level finding fails
  the gate
- `freshness_ok` requires the snapshot's recorded `upstream_sha` to
  match the live upstream HEAD
- `bootstrap=True` is the only way to bypass `freshness_ok`; it always
  sets `review_required=True` so the PR carries human acknowledgement
- the `update-pr` job in `ci.yml` requires explicit
  `publish_candidate_pr=true` to open a PR, AND `bootstrap=true` if
  the gate emitted `review_required=true`

## What an attacker controls

The threat model assumes:

- GitHub itself is not compromised
- The runner VM is not compromised
- The `OLLAMA_API_KEY` is held by Ollama Cloud (not by us)
- The Telegram bot token is held by Telegram (not by us)

What an attacker might control:

- The upstream `hermes-agent` repo (could push malicious code)
- The `nastech-agent` fork's `main` branch (could add a poisoned manifest.json)
- Their own Telegram account (could send messages, but the bot ignores them
  unless they happen to be in `TELEGRAM_CHAT_ID`)
- The contents of any PR opened against `100Ways` (could try to introduce
  a redaction bypass)

## Reporting vulnerabilities

Email `security@nastechresearch.local` or open a private security
advisory on GitHub. See `SECURITY.md` for the supported versions policy.
