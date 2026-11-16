"""Shared pytest fixtures."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from orchid.core.config import load_config

REPO_ROOT = Path(__file__).resolve().parents[1]
TPE = timezone(timedelta(hours=8))


@pytest.fixture(scope="session")
def cfg():
    return load_config(REPO_ROOT / "config" / "platform.yaml")


@pytest.fixture()
def site_cfg(cfg):
    return cfg.sites["site01"]


@pytest.fixture()
def t0():
    return datetime(2026, 11, 17, 0, 0, 0, tzinfo=TPE)


def telemetry_bytes(**overrides) -> bytes:
    base = {
        "schema_version": "1.0",
        "site_id": "site01",
        "device_id": "temp01",
        "device_ts": "2026-11-17T00:00:00+08:00",
        "seq": 1,
        "metric": "temperature",
        "value": 21.4,
        "unit": "C",
    }
    base.update(overrides)
    return json.dumps(base).encode()


# ---------------------------------------------------------------- stack test plumbing
import os  # noqa: E402

STACK = os.environ.get("ORCHID_STACK") == "1"


def pytest_collection_modifyitems(config, items):
    if STACK:
        return
    skip = pytest.mark.skip(reason="stack test: run via `python scripts/verify_all.py` (ORCHID_STACK=1)")
    for item in items:
        if "stack" in item.keywords:
            item.add_marker(skip)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


@pytest.fixture()
def evidence(request):
    from tests import lab

    marker = request.node.get_closest_marker("gate")
    gate, title = (marker.args[0], marker.args[1]) if marker else (request.node.name, request.node.name)
    ev = lab.Evidence(f"{gate}-{request.node.name}"[:80] if marker is None else gate, title)
    lab.set_current(ev)
    yield ev
    rep = getattr(request.node, "rep_call", None)
    result = "PASS" if rep is not None and rep.passed else ("FAIL" if rep is not None else "INCONCLUSIVE")
    if rep is not None and rep.failed:
        ev.note(str(rep.longrepr)[-1500:])
    ev.write(result)
    lab.set_current(None)
