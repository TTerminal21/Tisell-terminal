"""FRED - macro and rates series. Official, free, no meaningful daily cap."""
from __future__ import annotations

from datetime import date
from typing import Any

from config import FRED_API_KEY
from sources import MACRO, NotConfigured, SourceError, to_date, to_float
from sources._http import get_json

NAME = "fred"
CAPABILITIES = {MACRO}
BASE_URL = "https://api.stlouisfed.org/fred"


def is_configured() -> bool:
    return bool(FRED_API_KEY)


def _get(path: str, params: dict[str, Any]) -> Any:
    if not is_configured():
        raise NotConfigured("fred: FRED_API_KEY is not set")
    return get_json(
        NAME,
        f"{BASE_URL}/{path}",
        params={**params, "api_key": FRED_API_KEY, "file_type": "json"},
    )


def fetch_macro(
    series_id: str, start: date | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return (series metadata, observations)."""
    sid = series_id.strip().upper()

    meta_payload = _get("series", {"series_id": sid})
    entries = meta_payload.get("seriess") or []
    if not entries:
        raise SourceError(f"fred: unknown series {sid}")
    info = entries[0]
    meta = {
        "series_id": sid,
        "title": info.get("title"),
        "units": info.get("units"),
        "frequency": info.get("frequency"),
        "seasonal": info.get("seasonal_adjustment"),
    }

    params: dict[str, Any] = {"series_id": sid}
    if start is not None:
        params["observation_start"] = start.isoformat()
    obs_payload = _get("series/observations", params)

    observations = []
    for entry in obs_payload.get("observations") or []:
        day = to_date(entry.get("date"))
        if day is None:
            continue
        # FRED writes "." for a missing print; to_float maps that to None.
        observations.append({"series_id": sid, "date": day, "value": to_float(entry.get("value"))})

    if not observations:
        raise SourceError(f"fred: no observations for {sid}")
    observations.sort(key=lambda row: row["date"])
    return meta, observations
