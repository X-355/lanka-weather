"""Command line: python -m lankaweather <command>

  fetch        ERA5 history + NWP day-ahead archive + NASA POWER (resumable)
  synthetic    write synthetic data with the real schema (CI / offline only)
  train        train, evaluate on unseen years, refit production model
  predict      issue tomorrow's forecast and lock it into the ledger
  score        fill observations into matured ledger rows
  site         build site/data/*.json
  publish-hf   upload model to the Hugging Face model repo
  publish-space upload site/ to a static Hugging Face Space
  daily        fetch-recent + score + predict + site   (what the daily workflow runs)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from .config import load_config


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="lankaweather")
    ap.add_argument("command")
    ap.add_argument("--offline", action="store_true", help="predict from cached ERA5 instead of the live API")
    ap.add_argument("--budget", type=float, default=None, help="Open-Meteo call-weight budget for this run")
    ap.add_argument("--skip-power", action="store_true")
    ap.add_argument("--skip-nwp", action="store_true")
    ap.add_argument("--force", action="store_true", help="issue a forecast even outside the evening window")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()

    def client():
        from .http import Client
        return Client(budget=a.budget or cfg["data"]["max_weight_per_run"])

    c = a.command
    if c == "fetch":
        from . import fetch
        cl = client()
        res = {}
        if not a.skip_nwp:  # small, so first: problems with it surface early
            res["nwp"] = fetch.fetch_nwp(cl)
        res["history"] = fetch.fetch_history(cl)
        if not a.skip_power:
            res["power"] = fetch.fetch_power(cl)
        res["weight_spent"] = cl.spent
        print(json.dumps(res, indent=1))
    elif c == "synthetic":
        from .synthetic import generate
        generate()
    elif c == "train":
        from .train import run
        full = run()
        print(json.dumps(full["metrics"]["rain"], indent=1))
    elif c == "predict":
        from .predict import run
        run(offline=a.offline, force=a.force)
    elif c == "score":
        from .predict import score_ledger
        print(score_ledger())
    elif c == "site":
        from .build_site import run
        run()
    elif c == "publish-hf":
        from .publish_hf import publish_model
        publish_model()
    elif c == "publish-space":
        from .publish_hf import publish_space
        publish_space()
    elif c == "check":
        # exit 0 when every town has history through the training/validation split, else 3
        from .config import ERA5_DIR, town_keys
        need = int(cfg["split"]["valid_end"][:4]) + 1
        start = int(cfg["data"]["start_date"][:4])
        missing = [f"{t}/{y}" for t in town_keys() for y in range(start, need + 1)
                   if not (ERA5_DIR / t / f"{y}.parquet").exists()]
        print(f"history complete" if not missing else f"{len(missing)} town-years missing, e.g. {missing[:5]}")
        return 0 if not missing else 3
    elif c == "daily":
        from . import build_site, fetch, predict
        from .config import MODELS
        cl = client()
        # Fills any gaps (so the first days also finish the history bootstrap) and refreshes this year.
        print(fetch.fetch_history(cl))
        if not a.skip_nwp:
            print(fetch.fetch_nwp(cl))
        if not (MODELS / "metadata.json").exists():
            print("no trained model yet — run the 'Train model' workflow once history is complete")
            return 0
        print(predict.score_ledger())
        predict.run(offline=a.offline, force=a.force)
        build_site.run()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
