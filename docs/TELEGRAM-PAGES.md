# Telegram and GitHub Pages operations

100Ways now exposes the same public-safe synchronization state through two surfaces. Telegram is the operational channel: it sends the current commit stream status, displays a persistent reply keyboard, answers status questions during the scheduled Actions window, and reports errors, remaining gates, and review-PR state. GitHub Pages is a read-only public evidence surface: it presents the sanitized status payload, verified refs, threshold progress, gate matrix, audit history, and documentation.

## Telegram commands

Commands come from one registry (`hundredways/telegram_commands.py`) that declares each name, its aliases, its argument budget and the trust level it requires, so the menu a commander sees is generated from the same rows the dispatcher answers from. The reply keyboard provides `Status`, `Progress`, `Errors`, `Remaining`, `Gates`, `PR status`, `Help` and `Whoami`. Aliases such as `/PR_Status@BotName`, `what-is receipt` and `reload` resolve to the same canonical command, and a malformed command gets its usage line instead of an AI guess.

Three levels exist and each is named by Telegram ids in Actions secrets: `read` is the configured chat, widened by `TELEGRAM_ALLOW_CHATS` (`TELEGRAM_ALLOWED_CHAT_IDS` is accepted as the same knob); `TELEGRAM_OPERATORS` adds the command audit and re-read verbs; `TELEGRAM_ADMINS` additionally holds the level the write verbs require. An unset list grants nothing, so a deployment that configures no allowlist is read-only. `Whoami` reports the chat id, the user id and the level the bot actually granted, which is how an operator finds the id to allowlist. Messages from a chat that is not allowlisted are not answered at all.

Free-form questions may be answered by Ollama Cloud using `gemma4:31b-cloud` when `OLLAMA_API_KEY` is available. AI responses are informational only and cannot authorize or execute a merge, tag, release, deployment, or gate bypass. No command merges, tags, releases or deploys; the strongest verb available can only open work for a human to review.

Notifications and answers are delivered as plain text. Telegram rejects unbalanced Markdown entities, and a commit subject containing `*` or `_` is ordinary on a real run, so markup would silently drop the whole message.

Conversation memory is bounded to the latest 40 redacted events and is stored in a short-lived Actions cache. Each line records who asked, the level they held and the command they used. The update cursor is persisted so a later scheduled window does not repeatedly answer the same Telegram update, and a scheduled status message is skipped when nothing the chat cares about changed since the last delivery.

## Public Pages boundary

The Pages payload uses schema `100ways.public-status.v1`. It contains abbreviated commit refs, public run links, sanitized gate labels, status history, and explicit publication boundaries. It must never contain tokens, chat IDs, raw logs, private artifacts, or credentials. Pages never becomes a write or approval surface.

## Deployment protection

Configure GitHub Pages to use the custom Actions workflow and protect the `github-pages` environment so only the default branch can deploy. Pull-request runs can validate the site artifact but must not publish it. Telegram remains the only operational notification channel.
