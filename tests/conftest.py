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
