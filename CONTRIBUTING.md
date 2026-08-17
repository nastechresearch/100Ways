# Contributing

Thanks for your interest in 100Ways. This document covers the workflow
for contributing changes.

## Code of conduct

This project follows the [Contributor Covenant](https://www.contributor-covenant.org/).
Be respectful. Be patient. Help others learn.

## Reporting bugs

Open a public GitHub issue using the
[bug report template](.github/ISSUE_TEMPLATE/bug_report.md).
For security issues, follow [SECURITY.md](SECURITY.md) instead.

## Submitting changes

1. **Fork** the repo and create a branch from `main`.
2. **Make your change.** Run `make test` and `make lint` locally before
   committing. New modules need tests (see the test directories).
3. **One logical change per PR.** If your fix touches two unrelated
   things, open two PRs.
4. **Commit message format:** use
   [Conventional Commits](https://www.conventionalcommits.org/):
   `fix:`, `feat:`, `chore:`, `docs:`, `refactor:`, `test:`.
5. **Open a PR** against `nastechresearch/100Ways:main`. The PR template
   will guide you through the description.
6. **Wait for CI.** The CI runs lint, tests, and coverage. Coverage must
   stay at or above 70% (current: 73%).
7. **Address review feedback.** Maintainers may ask for changes; respond
   with new commits on the same branch.

## Coding style

- Python 3.10+. Type hints preferred for new code.
- 100-char line length (ruff default).
- Imports sorted by ruff (E/F/I rule set).
- Docstrings on every public function and class.

## Where to start

Look for issues labelled `good first issue` or `help wanted`. The
hardened weekly gate has 50+ capabilities; many could use tighter
test coverage. The Pages site (`site/`) could use visual polish.

## Local development

```sh
make install-dev    # installs the package + dev extras
make test           # full test suite
make lint           # ruff check + format check
make coverage       # pytest with coverage gate
make update         # run the 19-stage update pipeline against a local fixture
make forkcheck      # diff a candidate against a real nastech-agent fork
```

## Architecture

Read [docs/architecture.md](docs/architecture.md) before changing
anything in `hundredways/`. The 19-stage pipeline is a load-bearing
invariant.

## Safety

100Ways has a safety contract documented in
[docs/security-model.md](docs/security-model.md). Any PR that weakens
the redaction boundary, the publication boundary, or the gate boundary
will be rejected without review.
