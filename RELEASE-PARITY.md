# Hermes-to-NasTech Release Parity

## Policy

Every official Hermes release tag receives a matching **NasTech-branded** release cycle. The matching NasTech tag uses the same calendar version name, but points to the verified branded NasTech `main` commit rather than Hermes' commit SHA.

> 100Ways detects release parity gaps, verifies exact tagged source, writes evidence, and prepares a NasTech review PR. It never merges, creates tags, creates releases, or deploys automatically.

## Independent synchronization streams

| Stream | Trigger | Source | Automatic result | Human-approved result |
|---|---|---|---|---|
| Hermes release parity | A Hermes GitHub release/tag | Exact peeled Hermes release-tag SHA | Release-parity evidence and a branded NasTech review PR | Same-name NasTech tag and branded GitHub Release |
| Latest-main synchronization | 50 pending Hermes main commits | Fresh direct Hermes `main` clone | Branded NasTech review PR | Optional later NasTech release under a separately selected version |

## Release promotion contract

The `NasTech Release Promotion` workflow is `workflow_dispatch` only. It requires a protected `nastech-release` environment, a typed `PUBLISH <tag>` confirmation, the exact Hermes tag target SHA, the current branded NasTech main SHA, and a 100Ways gate-receipt digest. It fails closed if the source does not match, the candidate is no longer current main, or the NasTech tag already exists.

The release title is `Nastech Agent <tag>`. Release notes preserve the approved NasTech identity, record exact verification evidence, and include **Powered by NousResearch** plus a link to the matching upstream release.

## Deployment contract

The `NasTech Release Deployment` workflow is separate and manual. It requires a protected `nastech-deployment` environment and the same typed `PUBLISH <tag>` confirmation. It verifies that the matching NasTech release already exists, then requests the documentation deployment only. Container and application deployment remain separate production approvals.

## Required repository configuration

Before a human runs either manual workflow, configure the `nastech-release` and `nastech-deployment` environments with required reviewers. Add a narrowly scoped `NAS_TECH_RELEASE_TOKEN` secret that can create tags/releases and request the existing NasTech deployment workflow. Do not expose this token to the scheduled monitor, #344, pull-request workflows, or untrusted code.
