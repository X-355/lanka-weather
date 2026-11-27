"""Paths and configuration loading."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "config.yaml"

DATA = ROOT / "data"
RAW = DATA / "raw"
ERA5_DIR = RAW / "era5"
NWP_DIR = RAW / "nwp"
POWER_DIR = RAW / "power"
LIVE_DIR = RAW / "live"
MODELS = ROOT / "models"
REPORTS = ROOT / "reports"
PREDICTIONS = ROOT / "predictions"
LEDGER = PREDICTIONS / "ledger.csv"
SITE = ROOT / "site"
SITE_DATA = SITE / "data"


@dataclass(frozen=True)
class Town:
    key: str
    name: str
    lat: float
    lon: float
    elevation: float
    zone: str
    region: str


@lru_cache(maxsize=1)
def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def towns() -> list[Town]:
    cfg = load_config()
    return [Town(key=k, **v) for k, v in cfg["towns"].items()]


def town_keys() -> list[str]:
    return list(load_config()["towns"].keys())
