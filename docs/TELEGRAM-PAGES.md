# Telegram and GitHub Pages operations

100Ways now exposes the same public-safe synchronization state through two surfaces. Telegram is the operational channel: it sends the current commit stream status, displays a persistent reply keyboard, answers status questions during the scheduled Actions window, and reports errors, remaining gates, and review-PR state. GitHub Pages is a read-only public evidence surface: it presents the sanitized status payload, verified refs, threshold progress, gate matrix, audit history, and documentation.

## Telegram commands

The reply keyboard provides `Status`, `Progress`, `Errors`, `Remaining`, `PR status`, and `Help`. Free-form questions may be answered by Ollama Cloud using `gemma4:31b-cloud` when `OLLAMA_API_KEY` is available. AI responses are informational only and cannot authorize or execute a merge, tag, release, deployment, or gate bypass.

Conversation memory is bounded to the latest 40 redacted events and is stored in a short-lived Actions cache. The update cursor is persisted so a later scheduled window does not repeatedly answer the same Telegram update. The bot only accepts messages from the configured `TELEGRAM_CHAT_ID`.

## Public Pages boundary

The Pages payload uses schema `100ways.public-status.v1`. It contains abbreviated commit refs, public run links, sanitized gate labels, status history, and explicit publication boundaries. It must never contain tokens, chat IDs, raw logs, private artifacts, or credentials. Pages never becomes a write or approval surface.

## Deployment protection

Configure GitHub Pages to use the custom Actions workflow and protect the `github-pages` environment so only the default branch can deploy. Pull-request runs can validate the site artifact but must not publish it. Telegram remains the only operational notification channel.
