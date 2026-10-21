# Telegram and GitHub Pages operations

100Ways now exposes the same public-safe synchronization state through two surfaces. Telegram is the operational channel: it sends the current commit stream status, displays a persistent reply keyboard, answers status questions during the scheduled Actions window, and reports errors, remaining gates, and review-PR state. GitHub Pages is a read-only public evidence surface: it presents the sanitized status payload, verified refs, threshold progress, gate matrix, audit history, and documentation.

## Telegram commands

Commands come from one registry (`hundredways/telegram_commands.py`) that declares each name, its aliases, its argument budget and the trust level it requires, so the menu a commander sees is generated from the same rows the dispatcher answers from. The reply keyboard provides `Status`, `Progress`, `Errors`, `Remaining`, `Gates`, `PR status`, `Help` and `Whoami`. Aliases such as `/PR_Status@BotName`, `what-is receipt` and `reload` resolve to the same canonical command, and a malformed command gets its usage line instead of an AI guess.

Three levels exist and each is named by Telegram ids in Actions secrets: `read` is the configured chat, widened by `TELEGRAM_ALLOW_CHATS` (`TELEGRAM_ALLOWED_CHAT_IDS` is accepted as the same knob); `TELEGRAM_OPERATORS` adds the command audit and re-read verbs; `TELEGRAM_ADMINS` additionally holds the level the write verbs require. An unset list grants nothing, so a deployment that configures no allowlist is read-only. `Whoami` reports the chat id, the user id and the level the bot actually granted, which is how an operator finds the id to allowlist. Messages from a chat that is not allowlisted are not answered at all.

Free-form questions may be answered by Ollama Cloud using `gemma4:31b-cloud` when `OLLAMA_API_KEY` is available. AI responses are informational only and cannot authorize or execute a merge, tag, release, deployment, or gate bypass. No command merges, tags, releases or deploys; the strongest verb available can only open work for a human to review.

Notifications and answers are delivered as plain text. Telegram rejects unbalanced Markdown entities, and a commit subject containing `*` or `_` is ordinary on a real run, so markup would silently drop the whole message.

Conversation memory is bounded to the latest 40 redacted events and is stored in a short-lived Actions cache. Each line records who asked, the level they held and the command they used. The update cursor is persisted so a later scheduled window does not repeatedly answer the same Telegram update, and a scheduled status message is skipped when nothing the chat cares about changed since the last delivery.

## Windows that renew themselves

The scheduled monitor can only answer inside its own run, so a question asked ten minutes later would get silence. `stage-telegram-window.yml` is a `workflow_dispatch` job that opens a bounded poll window and, when the chat is actually using it, queues the next window one hop further along. `hundredways/telegram_transport.py::plan_renewal` owns that decision: a quiet window closes the chain, `stay` asks for a longer one, and the chain stops at `MAX_CHAIN_HOPS` (8) or `MAX_CHAIN_SECONDS` (90 minutes) whatever the chat says. The job is serialized under `concurrency: telegram-window` and never cancelled mid-window, because two pollers sharing one bot consume the same `getUpdates` offset and the second would silently swallow the first operator's messages.

A window does not re-derive the commit stream. Upstream's history is about 1.3 GB, so `stage-commit-stream.yml` publishes its decision and context into `telegram-state/` before saving the cache, and a window answers from the numbers the last scheduled run reported. Dispatching the next window needs `actions: write`; without it the agent says nothing about windows rather than failing the job.

## Editing engine code from the chat

`edit`, `apply`, `cancel` and `pending` are the Edit level: an admin proposes a literal SEARCH/REPLACE block against a file under `hundredways/`, and the strongest outcome available is a pull request against the dispatched ref. The path is refused before the file is read or the model is asked; the modules that judge the pipeline (`ci_policy`, `integrity`, `operation_safety`, `prepublish`, `prompt_gate`, `receipts`, `verify`) cannot be edited from chat at all. A block must match exactly once, the result must compile, must stay under `MAX_EDIT_BYTES`, must not be secret-shaped, and an argument carrying `merge`, `rebase`, `--admin`, `--force` or `--no-verify` is rejected before anything runs.

A proposal is only landed by its own author, and landing means: write the file, run the suite, and only if it is green create a branch, push that branch and open the PR. Any failing step restores the file and pushes nothing. The generated PR body states that opening it does not authorize a merge, tag, release or deployment. Two limits are inherited from GitHub and worth knowing before trusting the surface: a pull request opened with `GITHUB_TOKEN` does not start workflows, so a chat-landed PR shows no checks until a human comments or pushes to it; and the job's own static guard is `ci_policy.audit_workflow_security`, which lists the two paths allowed to push and open PRs, pins each to the events that may start it, and refuses either if it contains a merge.

## Public Pages boundary

The Pages payload uses schema `100ways.public-status.v1`. It contains abbreviated commit refs, public run links, sanitized gate labels, status history, and explicit publication boundaries. It must never contain tokens, chat IDs, raw logs, private artifacts, or credentials. Pages never becomes a write or approval surface.

## Deployment protection

Configure GitHub Pages to use the custom Actions workflow and protect the `github-pages` environment so only the default branch can deploy. Pull-request runs can validate the site artifact but must not publish it. Telegram remains the only operational notification channel.
