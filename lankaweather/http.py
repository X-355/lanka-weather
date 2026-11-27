"""Polite HTTP client for Open-Meteo / NASA POWER with retries and a call-weight budget.

Open-Meteo's free tier limits requests per minute/hour/day and counts a request that asks
for many variables or a long period as several "calls". We estimate that weight up front,
refuse to exceed the per-run budget, and back off on HTTP 429.
"""
from __future__ import annotations

import logging
import math
import time

import requests

log = logging.getLogger(__name__)

USER_AGENT = "lanka-weather/0.1 (+https://github.com/X-355/lanka-weather)"


class BudgetExhausted(RuntimeError):
    """Raised when this run has spent its API budget; the caller should stop and resume later."""


class RateLimited(RuntimeError):
    """Raised on an hourly/daily Open-Meteo limit, which is not worth waiting out inside CI."""


def openmeteo_weight(n_vars: int, n_days: int) -> float:
    """Conservative estimate of Open-Meteo call weight (variables/10 x days/14, min 1 each)."""
    return max(1.0, n_vars / 10.0) * max(1.0, math.ceil(n_days / 14.0))


class Client:
    def __init__(self, budget: float = float("inf"), min_interval: float = 0.5):
        self.budget = budget
        self.spent = 0.0
        self.min_interval = min_interval
        self._last = 0.0
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def charge(self, weight: float) -> None:
        if self.spent + weight > self.budget:
            raise BudgetExhausted(f"budget {self.budget:.0f} would be exceeded (spent {self.spent:.0f}, next {weight:.0f})")
        self.spent += weight

    def get_json(self, url: str, params: dict, weight: float = 1.0, retries: int = 5) -> dict:
        self.charge(weight)
        for attempt in range(retries):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self.session.get(url, params=params, timeout=120)
            except requests.RequestException as e:
                log.warning("request error %s (attempt %d): %s", url, attempt + 1, e)
                time.sleep(5 * 2**attempt)
                continue
            if r.status_code == 200:
                return r.json()
            reason = ""
            try:
                reason = r.json().get("reason", "")
            except ValueError:
                reason = r.text[:200]
            if r.status_code == 429:
                if "inute" in reason:  # "Minutely API request limit exceeded"
                    log.warning("minutely limit hit, sleeping 65s")
                    time.sleep(65)
                    continue
                raise RateLimited(reason or "rate limited")
            if r.status_code == 400:
                raise ValueError(f"400 from {url}: {reason}")
            log.warning("HTTP %s from %s: %s (attempt %d)", r.status_code, url, reason, attempt + 1)
            time.sleep(5 * 2**attempt)
        raise RuntimeError(f"giving up on {url} after {retries} attempts")
