"""Reduced fork module: the Store check the reconcile makes credential-safe.

Only ``check_from_env`` matters here; the reconcile replaces the credential
lookup and inserts ``_STORE_CREDENTIALS`` directly above it.
"""
from __future__ import annotations

import sys


class StoreError(RuntimeError):
    """A Store submission the pipeline cannot complete."""


def _http_run(*args, **kwargs):
    raise NotImplementedError("the fixture does not talk to Partner Center")


def check(product_id: str, tenant_id: str, client_id: str, client_secret: str,
          run=_http_run) -> str:
    return "verified"


def _notice(text: str) -> None:
    # A GitHub annotation, so the manual step shows on the run's summary page.
    print(f"::warning title=Microsoft Store::{text}")


def check_from_env(env: dict, run=_http_run) -> str:
    """Check the Store submission when the environment configures it."""
    product_id = env.get("MS_STORE_PRODUCT_ID")
    if not product_id:
        print("Store check skipped: MS_STORE_PRODUCT_ID is not configured.",
              file=sys.stderr)
        return "not-configured"
    return check(
        product_id=product_id,
        tenant_id=env["MS_STORE_TENANT_ID"],
        client_id=env["MS_STORE_CLIENT_ID"],
        client_secret=env["MS_STORE_CLIENT_SECRET"],
        run=run,
    )
