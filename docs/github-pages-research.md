# GitHub Pages research for 100Ways

## Verified capabilities

GitHub Pages is a static hosting service that publishes HTML, CSS, and JavaScript from a repository, optionally through a build process. A project site can be hosted from a folder in the project repository or through a GitHub Actions workflow.

For a custom build, GitHub recommends a GitHub Actions publishing workflow that checks out the repository, builds the static files, uploads a Pages artifact, and deploys it with the official Pages deployment action. The Pages workflow uses the `github-pages` environment, which should be protected so only the default branch can deploy.

## Security and privacy boundaries

Pages sites are publicly available on the internet even when the source repository is private if the plan or organization permits publication. The site must therefore contain only intentionally public, redacted evidence. Secrets, Telegram tokens, chat IDs, GitHub tokens, private logs, raw artifacts, and personal data must never be copied into the published directory.

The public site is informational only. It must not become a control plane for merging, tagging, releasing, or deploying. Telegram remains the operational notification channel, while the Pages site presents verified read-only summaries.

## Usage limits relevant to the design

Published sites are limited to 1 GB, deployments time out after 10 minutes, and there is a soft 100 GB monthly bandwidth limit. Pages also has a soft 10-builds-per-hour limit, although custom Actions publishing is the recommended route for a custom build and is not subject to that soft build limit in the same way.

## Recommended 100Ways route

Use a custom, pinned GitHub Actions Pages workflow. Build a small static site from sanitized JSON and Markdown evidence generated only after successful verification stages. Deploy only from `main` through the `github-pages` environment. Pull-request runs may build and preview the site artifact but must not deploy it.

## Sources

1. https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages
2. https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site
3. https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits
4. https://pages.github.com/
