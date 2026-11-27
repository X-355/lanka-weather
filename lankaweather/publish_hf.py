"""Publish the model to a Hugging Face model repo, and (optionally) the site to a static Space."""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path

from .config import ERA5_DIR, MODELS, REPORTS, ROOT, SITE

log = logging.getLogger(__name__)

DEFAULT_MODEL_REPO = "X-355/lanka-weather"
DEFAULT_SPACE_REPO = "X-355/lanka-weather"


def _api():
    from huggingface_hub import HfApi

    token = os.environ.get("HF_TOKEN")
    if not token:
        raise SystemExit("HF_TOKEN is not set — add it as a GitHub Actions secret")
    return HfApi(token=token)


def _guard() -> dict:
    if (ERA5_DIR / "SYNTHETIC").exists():
        raise SystemExit("refusing to publish: data/raw/era5 is synthetic")
    meta = json.loads((MODELS / "metadata.json").read_text())
    if meta.get("data_source") == "synthetic":
        raise SystemExit("refusing to publish a model trained on synthetic data")
    return meta


def model_card(meta: dict, metrics: dict) -> str:
    rain = metrics["metrics"]["rain"]
    comp = metrics["metrics"].get("nwp_comparison")

    def pct(x):
        return "–" if x is None else f"{100 * x:.1f}%"

    rows = "\n".join(f"| {k} | {v['brier']:.3f} | {pct(v.get('bss'))} | {v['auc'] if v['auc'] is None else round(v['auc'], 3)} |"
                     for k, v in rain.items())
    nwp = ""
    if comp:
        nwp = "\n### Versus day-ahead NWP ({} → {}, same rows)\n\n| Forecast | Brier | Skill vs clim | CSI |\n|---|---|---|---|\n".format(*comp["period"])
        nwp += "\n".join(f"| {e['label']} | {e['brier']:.3f} | {pct(e.get('bss'))} | {e['csi'] if e['csi'] is None else round(e['csi'], 3)} |"
                         for e in comp["entries"].values())
    return f"""---
license: mit
tags: [weather, tabular-classification, lightgbm, sri-lanka, precipitation]
library_name: lightgbm
---

# Sri Lanka Weather Lab — next-day rain

Gradient-boosted trees (LightGBM) predicting the probability of **≥ {meta['targets']['rain_mm']} mm rain tomorrow**
for {len(meta['towns'])} Sri Lankan towns, plus a research-only heavy-rain (≥ {meta['targets']['heavy_mm']} mm) model.

- Version `{meta['version']}`, trained {meta['trained_at']}
- Data: {meta['data_source']}, {meta['data_range'][0]} → {meta['data_range'][1]}
- Honest split by date — train {meta['split']['train'][0]}…{meta['split']['train'][1]},
  validation (early stopping only) {meta['split']['valid'][0]}…{meta['split']['valid'][1]},
  **test {meta['split']['test'][0]}…{meta['split']['test'][1]}**
- Files: `rain.txt`, `heavy.txt` (production refit on all data), `eval/` (the frozen models the scores below come from),
  `metadata.json` (feature list), baseline tables, `metrics.json`, `REPORT.md`.

## Test-period scores (next-day rain)

| Forecast | Brier ↓ | Skill vs climatology | AUC |
|---|---|---|---|
{rows}
{nwp}

## Limitations

Truth is ERA5 reanalysis (~25 km grid), not rain gauges. Not an official forecast; the heavy-rain model is for research
only. For warnings, use the Department of Meteorology Sri Lanka and the Disaster Management Centre.

Code: https://github.com/X-355/lanka-weather
"""


def publish_model() -> None:
    meta = _guard()
    metrics = json.loads((REPORTS / "metrics.json").read_text())
    repo = os.environ.get("HF_MODEL_REPO") or DEFAULT_MODEL_REPO
    api = _api()
    api.create_repo(repo, repo_type="model", exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        shutil.copytree(MODELS, t, dirs_exist_ok=True)
        for f in ["metrics.json", "REPORT.md"]:
            shutil.copy(REPORTS / f, t / f)
        shutil.copy(ROOT / "config" / "config.yaml", t / "config.yaml")
        (t / "README.md").write_text(model_card(meta, metrics))
        api.upload_folder(repo_id=repo, repo_type="model", folder_path=tmp,
                          commit_message=f"model {meta['version']}")
    log.info("published model to https://huggingface.co/%s", repo)


def publish_space() -> None:
    _guard()
    repo = os.environ.get("HF_SPACE_REPO") or DEFAULT_SPACE_REPO
    api = _api()
    api.create_repo(repo, repo_type="space", space_sdk="static", exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(SITE, tmp, dirs_exist_ok=True)
        (Path(tmp) / "README.md").write_text(
            "---\ntitle: Sri Lanka Weather Lab\nemoji: 🌧️\ncolorFrom: blue\ncolorTo: green\nsdk: static\n"
            "app_file: index.html\npinned: false\n---\n\nResults site for https://github.com/X-355/lanka-weather\n")
        api.upload_folder(repo_id=repo, repo_type="space", folder_path=tmp, commit_message="update site")
    log.info("published site to https://huggingface.co/spaces/%s", repo)
