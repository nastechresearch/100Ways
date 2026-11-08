"""Generated route ledgers must remain canonical after token branding."""

import json

import pytest

from hundredways.updates import _reconcile_serve_handler_ledger, reconcile_tree

REL = "tests/nastech_cli/serve_handler_ledger.json"


def document(handlers):
    return {
        "generated_by": "scripts/gen_serve_handler_ledger.py",
        "refs": "#145171 (EP-1)",
        "decisions": ["authority", "keep-host-wide", "delete", "undecided"],
        "counts": {"http": {"authority": 3}, "rpc": {"undecided": 1}},
        "handlers": handlers,
    }


def row(handler, kind="http"):
    return {
        "handler": handler,
        "kind": kind,
        "decision": "authority",
        "ref": "",
        "current_path": "authority",
    }


def render(doc):
    """The committed generator's layout; generic JSON indenting is not equivalent."""
    header = json.dumps({k: v for k, v in doc.items() if k != "handlers"}, indent=1, sort_keys=True)
    return (
        header[:-2]
        + ',\n "handlers": [\n'
        + ",\n".join("  " + json.dumps(r, sort_keys=True) for r in doc["handlers"])
        + "\n ]\n}\n"
    )


def write(root, doc):
    p = root / REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render(doc), encoding="utf-8")
    return p


def test_reorders_branded_route_names_without_changing_rows_or_metadata(tmp_path):
    # /api/hermes/update sorted before host; /api/nastech/update sorts after it.
    rows = [
        row("GET /api/nastech/update/check"),
        row("GET /api/host/identity"),
        row("GET /api/local-models/status"),
        row("session.list", "rpc"),
    ]
    doc = document(rows)
    p = write(tmp_path, doc)
    assert _reconcile_serve_handler_ledger(str(tmp_path))
    expected = {**doc, "handlers": [rows[1], rows[2], rows[0], rows[3]]}
    assert p.read_text() == render(expected)
    assert json.loads(p.read_text()) == expected
    assert not _reconcile_serve_handler_ledger(str(tmp_path))


def test_matches_generator_http_websocket_mount_then_rpc_order(tmp_path):
    rows = [
        row("session.title", "rpc"),
        row("WS /api/ws", "websocket"),
        row("* /api/plugins/{name}/{path}", "mount"),
        row("GET /api/nastech/update/check"),
        row("connectors.connect", "rpc"),
    ]
    p = write(tmp_path, document(rows))
    assert _reconcile_serve_handler_ledger(str(tmp_path))
    assert [r["handler"] for r in json.loads(p.read_text())["handlers"]] == [
        rows[i]["handler"] for i in [2, 3, 1, 4, 0]
    ]


@pytest.mark.parametrize(
    "rows", [[], [row("GET /api/health")], [row("GET /api/health"), row("session.list", "rpc")]]
)
def test_already_canonical_and_empty_tables_are_untouched(tmp_path, rows):
    p = write(tmp_path, document(rows))
    original = p.read_bytes()
    assert not _reconcile_serve_handler_ledger(str(tmp_path))
    assert p.read_bytes() == original


def test_missing_ledger_is_not_created(tmp_path):
    assert not _reconcile_serve_handler_ledger(str(tmp_path))
    assert not (tmp_path / REL).exists()


@pytest.mark.parametrize(
    "payload",
    ["{", "[]", '{"handlers": []}', '{"generated_by": "another-generator", "handlers": []}'],
)
def test_damaged_or_unknown_schema_fails_closed(tmp_path, payload):
    p = tmp_path / REL
    p.parent.mkdir(parents=True)
    p.write_text(payload)
    with pytest.raises(ValueError, match="ledger"):
        _reconcile_serve_handler_ledger(str(tmp_path))
    assert p.read_text() == payload


@pytest.mark.parametrize(
    "rows",
    [
        [row("GET /api/health"), row("GET /api/health")],
        [{"handler": "GET /api/health"}],
        [row(17)],
        [row("")],
    ],
)
def test_invalid_or_duplicate_rows_are_not_silently_repaired(tmp_path, rows):
    p = write(tmp_path, document(rows))
    original = p.read_bytes()
    with pytest.raises(ValueError, match="handler"):
        _reconcile_serve_handler_ledger(str(tmp_path))
    assert p.read_bytes() == original


def test_full_reconciler_records_the_generated_path(tmp_path):
    write(tmp_path, document([row("GET /api/nastech/update/check"), row("GET /api/host/identity")]))
    result = reconcile_tree(str(tmp_path))
    assert REL in result.fixed
    assert result.total >= 1
    assert REL not in reconcile_tree(str(tmp_path)).fixed
