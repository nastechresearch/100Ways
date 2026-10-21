# Strict upstream parity

100Ways supports a strict mode for producing a branded Nastech candidate from Hermes without carrying fork-local implementation code forward.

## Command

```bash
100ways --repo /path/to/nastech-agent update \
  --hermes-url https://github.com/NousResearch/hermes-agent.git \
  --fork-root /path/to/nastech-agent \
  --owned-assets-root /path/to/100ways/config/owned-assets \
  --upstream-only
```

Strict mode has three guarantees:

1. Every product file comes from the selected Hermes source tree and is transformed by the canonical branding rules.
2. Fork-local files are not preserved, including fork-added Python, TypeScript, plugins, skills, and configuration.
3. Custom reconcile patches are disabled. This prevents 100Ways from silently adding implementation code that is not present upstream.

The permitted output differences are limited to:

- Canonical branding substitutions, including product names and approved URL/domain substitutions.
- Collision-safe path renames required by branding.
- Nastech-owned binary assets declared in the 100Ways registry.
- Generated review metadata outside the product tree.

The engine registry itself remains in 100Ways and is never copied into the candidate:

```text
100Ways/config/owned-assets/       # source registry; not shipped
nastech-agent/<declared targets>   # materialized branded assets
```

The generated `manifest.json` records the policy under `policy.upstream_only`. Future gates can reject a candidate unless this field is true.

## Rust accelerator

The optional `rust/brandfast` binary accelerates only mechanical filesystem work:

- deterministic file enumeration;
- deterministic tree hashing;
- streaming SHA-256 calculation.

Branding rules, URL transformations, file classification, policy, and verification remain in Python. If the binary is not built, the Python implementation is used automatically.

Build it with:

```bash
cargo build --manifest-path rust/brandfast/Cargo.toml --release
export HUNDREDWAYS_BRANDFAST="$PWD/rust/brandfast/target/release/brandfast"
```

This separation is intentional: moving the branding policy into a second implementation would create semantic drift and could produce a candidate that no longer matches Hermes exactly.
