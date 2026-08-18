# Actions failure evidence — 2026-08-17

The latest `100Ways Pages` scheduled run failed in build job `95323013099`, run `32008556252`. The failing step was **Fetch public source refs directly**.

The first two repositories cloned successfully. The direct clone of `https://github.com/NousResearch/hermes-agent.git upstream-agent` failed with:

> error: RPC failed; HTTP 429 curl 22 The requested URL returned error: 429
> fatal: expected flush after ref listing
> Process completed with exit code 128.

The failure is an upstream transport/rate-limit problem, not evidence of a source parity or branding mismatch. The workflow currently performs a full unshallow clone of Hermes, which is unnecessary for the Pages status payload and increases transfer/rate-limit exposure.
