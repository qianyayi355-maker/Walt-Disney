"""Fetch static attraction metadata (coordinates, type) from ThemeParks.wiki.

Run once while online to build data/attraction_meta.json.  The Streamlit app
reads that cache, so the deployed app only needs the lightweight /live call at
runtime and still works when the API is unreachable.

Usage:  python3 tools/build_attraction_meta.py
"""

from __future__ import annotations

import json
import time
import pathlib
from concurrent.futures import ThreadPoolExecutor

import requests

API = "https://api.themeparks.wiki/v1"
DATA = pathlib.Path(__file__).resolve().parent.parent / "data"

# The four Walt Disney World parks that the historical dataset covers.
PARKS = {
    "Magic Kingdom": "75ea578a-adc8-4116-a54d-dccb60765ef9",
    "Epcot": "47f90d2c-e191-4239-a466-5892ef59a88b",
    "Hollywood Studios": "288747d1-8b4f-4a64-867e-ea7c9b27bad8",
    "Animal Kingdom": "1c84a229-8862-4648-9c71-378ddd2c7693",
}

session = requests.Session()
session.headers["User-Agent"] = "clawsgo-disney-app/1.0 (teaching project)"


def get(url: str, tries: int = 3):
    for attempt in range(tries):
        try:
            r = session.get(url, timeout=20)
            if r.status_code == 200:
                return r.json()
        except requests.RequestException:
            pass
        time.sleep(0.8 * (attempt + 1))
    return None


def main() -> None:
    meta: dict[str, dict] = {}
    for park_name, park_id in PARKS.items():
        children = get(f"{API}/entity/{park_id}/children")
        if not children:
            print(f"!! children failed for {park_name}")
            continue
        kids = [c for c in children["children"] if c["entityType"] in ("ATTRACTION", "SHOW")]
        print(f"{park_name}: {len(kids)} entities")

        def details(c):
            d = get(f"{API}/entity/{c['id']}")
            return c, d

        with ThreadPoolExecutor(max_workers=8) as pool:
            for c, d in pool.map(details, kids):
                if not d:
                    continue
                loc = d.get("location") or {}
                meta[c["id"]] = {
                    "name": c["name"],
                    "park_id": park_id,
                    "park_name": park_name,
                    "entity_type": d.get("entityType", c["entityType"]),
                    "attraction_type": d.get("attractionType"),
                    "latitude": loc.get("latitude"),
                    "longitude": loc.get("longitude"),
                    "external_id": (d.get("externalId") or "").split(";")[0] or None,
                }

    out = DATA / "attraction_meta.json"
    out.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    with_geo = sum(1 for v in meta.values() if v["latitude"])
    print(f"wrote {out}  ({len(meta)} entities, {with_geo} with coordinates)")


if __name__ == "__main__":
    main()
