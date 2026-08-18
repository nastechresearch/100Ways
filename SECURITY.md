# Security

100Ways is a fork-sync engine that publishes a sanitized status payload
to GitHub Pages and sends operational alerts to Telegram. The security
model is documented in [`docs/security-model.md`](docs/security-model.md).

## Supported versions

| version | supported |
|---|---|
| latest `main` branch | yes |
| any tagged release | yes, for 90 days after the next release |
| anything else | no |

## Reporting a vulnerability

Open a [GitHub Security Advisory](https://github.com/nastechresearch/100Ways/security/advisories/new)
(private disclosure) — do not open a public issue.

We will:

- acknowledge within 72 hours
- investigate within 7 days
- publish a fix or document the accepted risk within 30 days

## Non-security bug reports

For non-security bugs, open a public GitHub issue using the
[bug report template](.github/ISSUE_TEMPLATE/bug_report.md).
