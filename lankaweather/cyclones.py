"""Tropical cyclones near Sri Lanka from GDACS, for the Weather tab's cyclone watch.

Kept free of numpy/pandas so the light 3-hourly site job can run it too.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .config import SITE_DATA

log = logging.getLogger(__name__)


def _write(name: str, obj) -> None:
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    (SITE_DATA / name).write_text(json.dumps(obj, separators=(",", ":")))


def build() -> None:
    """Tropical cyclones near Sri Lanka from GDACS (which aggregates JTWC/NOAA/agency advisories). Best effort.

    GDACS sometimes times out. Each run tries three times, waiting longer each time; if every try fails, the
    last successful list is kept and marked with when it was last checked, instead of being wiped.
    """
    import time
    import requests
    from datetime import date, timedelta

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = SITE_DATA / "cyclones.json"
    try:
        prev = json.loads(path.read_text()) if path.exists() else None
    except ValueError:
        prev = None
    today = date.today()
    last_err = None
    for attempt, timeout in enumerate((30, 60, 90)):
        try:
            r = requests.get("https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH",
                             params={"eventlist": "TC", "fromdate": (today - timedelta(days=14)).isoformat(),
                                     "todate": today.isoformat(), "alertlevel": "green;orange;red"}, timeout=timeout)
            r.raise_for_status()
            events = []
            for f in r.json().get("features", []):
                p = f.get("properties", {})
                lon, lat = (f.get("geometry", {}).get("coordinates") or [None, None])[:2]
                box = f.get("bbox") or [lon, lat, lon, lat]
                # North Indian Ocean around Sri Lanka: 50-100 E, 10 S - 25 N
                if box[0] is None or box[2] < 50 or box[0] > 100 or box[3] < -10 or box[1] > 25:
                    continue
                events.append({
                    "name": p.get("name"), "alert": p.get("alertlevel"), "from": p.get("fromdate"), "to": p.get("todate"),
                    "countries": p.get("country"), "lat": lat, "lon": lon,
                    "severity": (p.get("severitydata") or {}).get("severitytext"),
                    "url": (p.get("url") or {}).get("report"),
                    "ongoing": str(p.get("iscurrent")).lower() == "true",
                })
            _write("cyclones.json", {"fetched_at": now, "last_ok_at": now, "source": "GDACS", "ok": True, "events": events})
            return
        except Exception as e:  # never break a build over this
            last_err = e
            log.warning("GDACS cyclone fetch failed (try %d): %s", attempt + 1, e)
            time.sleep(10 * (attempt + 1))
    prev = prev or {}
    last_ok = prev.get("last_ok_at") or (prev.get("fetched_at") if prev.get("ok") else None)
    _write("cyclones.json", {"fetched_at": now, "source": "GDACS", "ok": False, "error": str(last_err)[:200],
                             "last_ok_at": last_ok, "events": prev.get("events", []) if last_ok else []})


