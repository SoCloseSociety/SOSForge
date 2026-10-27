#!/usr/bin/env python3
"""Live data audit of SOSForge: freshness, validity, precision, importance.

It MEASURES and fixes nothing. It reads one or more running SOSForge servers on
the same minute (production and a local branch, typically) plus independent
reference feeds (USGS, EMSC, GDACS, tsunami.gov, NHC, ReliefWeb), and prints
per-source tables side by side, then every candidate defect with the record id
and the raw value, so a human can open the original record before reporting it.

    python3 tools/audit_live.py \
        --target prod=https://sosforge.soclose.co \
        --target branch=http://127.0.0.1:8300

    --save DIR    keep every raw read (to re-run the analysis offline)
    --load DIR    analyse saved reads instead of fetching (no network at all)
    --json FILE   write the findings as JSON as well
    --no-refs     skip the reference feeds (faster, but no "missing" check)

Stdlib only, on purpose: it must run on any machine that can reach the site,
including from the backend venv (`make audit`).

What a "served" event is here: what the public API hands a client, i.e. the
primary feed (`/api/events?limit=2000`) plus one read per non-quake kind (the
2000 cut is by event time, so an alert that started weeks ago -- an open
drought, a long cyclone -- is only reachable by kind). Earthquakes are also read
WITH their duplicates (`primary_only=false`) to measure the deduper.

Politeness: about twenty GETs per target, spaced, once per run. Not a load test.
"""

from __future__ import annotations

import argparse
import gzip
import html
import json
import math
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

UA = "SOSForge-audit/1.0 (+https://sosforge.soclose.co; data audit, a few reads)"

KINDS = [
    "tsunami",
    "volcano",
    "cyclone",
    "flood",
    "wildfire",
    "drought",
    "storm",
    "heat",
    "landslide",
    "avalanche",
    "space_weather",
    "other",
]

# The product's own rules, restated here so the audit can hold the feed to them
# without importing the code it is auditing (backend/app/core/config.py).
HORIZON_DAYS = 3.0  # max_event_age_days
FUTURE_TOLERANCE_S = 120.0  # future_tolerance_seconds
STALE_HOURS = 6.0  # stale_after_hours
DEDUPE_WINDOW_S = 90.0  # Deduper(window_seconds)
DEDUPE_RADIUS_KM = 250.0
DEDUPE_MAG = 1.2
SYNTHETIC = {"swarm", "aftershock"}
SEVERITIES = ["info", "minor", "moderate", "severe", "extreme"]
RANK = {s: i for i, s in enumerate(SEVERITIES)}

NE_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
    "master/geojson/ne_50m_admin_0_countries.geojson"
)

# ------------------------------------------------------------------ fetching


def fetch(url: str, timeout: float = 90.0, tries: int = 3) -> tuple[int, bytes]:
    """(status, body); status 0 = no HTTP answer at all. Retried: a TLS
    handshake timeout on the auditing machine is not a fact about the feed
    (measured 2026-10-27: four reference reads lost to one at the same second)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    last = b""
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                # by the gzip magic too: PHIVOLCS sends gzip even unasked
                if resp.headers.get("Content-Encoding") == "gzip" or body[:2] == b"\x1f\x8b":
                    body = gzip.decompress(body)
                return resp.status, body
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read() or b""
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = str(exc).encode()
            time.sleep(3 * (attempt + 1))
    return 0, last


def fetch_json(url: str, timeout: float = 90.0) -> Any:
    status, body = fetch(url, timeout)
    if status != 200:
        return {"_error": status, "_body": body[:300].decode("utf-8", "replace")}
    try:
        return json.loads(body)
    except ValueError:
        return {"_error": "not json", "_body": body[:300].decode("utf-8", "replace")}


def target_reads(base: str) -> dict[str, str]:
    base = base.rstrip("/")
    reads = {
        "healthz": f"{base}/healthz",
        "sources": f"{base}/api/sources",
        "stats": f"{base}/api/stats",
        "feed": f"{base}/api/events?limit=2000",
        # Every solution, duplicates included. Production holds more than the
        # 2000 a read can return over 72 h, and the cut keeps the NEWEST, so
        # the window is read twice: everything for 24 h, M2.5+ for 72 h.
        "quakes_24h": f"{base}/api/events?limit=2000&kind=earthquake&primary_only=false&hours=24",
        "quakes_72h_m25": (
            f"{base}/api/events?limit=2000&kind=earthquake&primary_only=false&hours=72&min_magnitude=2.5"
        ),
    }
    for kind in KINDS:
        reads[f"kind_{kind}"] = f"{base}/api/events?limit=2000&kind={kind}"
    # user-facing surfaces other than the feed (owner rule 2026-10-27:
    # "rien doit etre fake"): what the page and the app claim about the data
    for name, path in SURFACES.items():
        reads[name] = f"{base}{path}"
    return reads


# Read as TEXT: the page, the manifest, the APK metadata. A JSON resource
# answered by the SPA's index.html (200, text/html) is itself a finding.
SURFACES = {
    "page_index": "/",
    "page_manifest": "/manifest.webmanifest",
    "page_download_latest": "/download/latest.json",
    "api_climate": "/api/climate",
    "api_version": "/api/version",
}


def read_target(base: str, pause: float) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, url in target_reads(base).items():
        if name in SURFACES:
            status, body = fetch(url, 60)
            out[name] = {"_status": status, "_text": body.decode("utf-8", "replace")[:400_000]}
        else:
            out[name] = fetch_json(url)
        time.sleep(pause)
    return out


def reference_reads(now: datetime) -> dict[str, str]:
    start = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
    return {
        "usgs_sig_week": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_week.geojson",
        "usgs_45_week": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson",
        "emsc_m5_week": (
            "https://www.seismicportal.eu/fdsnws/event/1/query?format=json&minmag=5"
            f"&limit=500&orderby=time&starttime={start}"
        ),
        # `alertlevel=orange;red` lowercase: the capitalised form times out
        # server-side more often (measured 2026-10-27). Slow (~25 s), 125 KB.
        "gdacs_orange_red": (
            "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
            "?eventlist=EQ;TC;FL;VO;DR;WF&alertlevel=orange;red"
        ),
        "nhc": "https://www.nhc.noaa.gov/CurrentStorms.json",
        "ptwc_atom": "https://www.tsunami.gov/events/xml/PHEBAtom.xml",
        "ntwc_atom": "https://www.tsunami.gov/events/xml/PAAQAtom.xml",
        "reliefweb": "https://reliefweb.int/disasters",
        # what the agencies list NOW, to catch ids they have since replaced
        "usgs_all_day": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson",
        "phivolcs_page": "https://earthquake.phivolcs.dost.gov.ph/",
    }


def read_references(now: datetime) -> dict[str, Any]:
    out: dict[str, Any] = {}

    def one(item: tuple[str, str]) -> tuple[str, Any]:
        name, url = item
        if name.endswith("_atom") or name in ("reliefweb", "phivolcs_page"):
            status, body = fetch(url, 60)
            return name, {"_status": status, "_text": body.decode("utf-8", "replace")}
        return name, fetch_json(url, 120)

    with ThreadPoolExecutor(max_workers=4) as pool:
        for name, value in pool.map(one, reference_reads(now).items()):
            out[name] = value
    return out


def load_countries(cache_dir: Path) -> list[dict[str, Any]]:
    """Natural Earth 1:50m admin-0 (public domain), cached. Each entry:
    {code, name, bbox, polys: [[outer, *holes], ...]} with (lon, lat) rings."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "ne_50m_admin_0_countries.geojson"
    if not path.exists():
        status, body = fetch(NE_URL, 120)
        if status != 200:
            return []
        path.write_bytes(body)
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for feat in data.get("features", []):
        props = feat.get("properties", {})
        code = props.get("ISO_A2_EH") or props.get("ISO_A2")
        if not code or code == "-99":
            continue
        geom = feat.get("geometry") or {}
        polys = geom.get("coordinates") or []
        if geom.get("type") == "Polygon":
            polys = [polys]
        xs = [p[0] for poly in polys for ring in poly for p in ring]
        ys = [p[1] for poly in polys for ring in poly for p in ring]
        if not xs:
            continue
        out.append(
            {
                "code": code.upper(),
                "name": props.get("NAME", code),
                "bbox": (min(xs), min(ys), max(xs), max(ys)),
                "polys": polys,
            }
        )
    return out


# ------------------------------------------------------------------ geometry


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(a)))


def _in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def country_distance_km(lat: float, lon: float, country: dict[str, Any]) -> float:
    """0 inside the country, else the distance to its nearest vertex (1:50m
    vertices are close enough together for a 500 km threshold)."""
    for poly in country["polys"]:
        if (
            poly
            and _in_ring(lon, lat, poly[0])
            and not any(_in_ring(lon, lat, h) for h in poly[1:])
        ):
            return 0.0
    best = float("inf")
    for poly in country["polys"]:
        for p in poly[0][:: max(1, len(poly[0]) // 400)]:
            best = min(best, haversine_km(lat, lon, p[1], p[0]))
    return best


def countries_at(lat: float, lon: float, countries: list[dict[str, Any]]) -> list[str]:
    hits = []
    for c in countries:
        w, s, e, n = c["bbox"]
        if not (w - 0.01 <= lon <= e + 0.01 and s - 0.01 <= lat <= n + 0.01):
            continue
        for poly in c["polys"]:
            if (
                poly
                and _in_ring(lon, lat, poly[0])
                and not any(_in_ring(lon, lat, h) for h in poly[1:])
            ):
                hits.append(c["code"])
                break
    return hits


# ------------------------------------------------------------------ helpers


def ts(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def fmt_s(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    if abs(seconds) < 120:
        return f"{seconds:.0f}s"
    if abs(seconds) < 7200:
        return f"{seconds / 60:.1f}m"
    if abs(seconds) < 172800:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def fmt(value: float | None, digits: int = 2) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


@dataclass
class Defect:
    target: str
    check: str
    source: str
    event_id: str
    raw: str
    expected: str
    owner: str
    url: str | None = None

    def row(self) -> list[str]:
        return [
            self.target,
            self.check,
            self.source,
            self.event_id,
            self.raw,
            self.expected,
            self.owner,
        ]


@dataclass
class Snapshot:
    """Everything one server served, on one read."""

    name: str
    server_time: datetime
    start: datetime | None
    served: dict[str, dict[str, Any]]
    quakes_all: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    reads_ok: dict[str, bool] = field(default_factory=dict)
    capped: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


def build_snapshot(name: str, raw: dict[str, Any]) -> Snapshot:
    feed = raw.get("feed") or {}
    now = ts(feed.get("server_time")) or datetime.now(UTC)
    uptime = (raw.get("healthz") or {}).get("uptime_seconds")
    start = now - timedelta(seconds=float(uptime)) if isinstance(uptime, (int, float)) else None
    served: dict[str, dict[str, Any]] = {}
    ok: dict[str, bool] = {}
    for key, body in raw.items():
        if key == "feed" or key.startswith("kind_"):
            good = isinstance(body, dict) and "events" in body
            ok[key] = good
            if good:
                for e in body["events"]:
                    served[e["id"]] = e
    quakes: dict[str, dict[str, Any]] = {}
    capped = []
    for key in ("quakes_24h", "quakes_72h_m25", "quakes_all"):
        body = raw.get(key)
        if body is None:
            continue
        good = isinstance(body, dict) and "events" in body
        ok[key] = good
        if good:
            if len(body["events"]) >= 2000:
                capped.append(key)
            for e in body["events"]:
                quakes[e["id"]] = e
    quakes_all = list(quakes.values())
    srcs = (raw.get("sources") or {}).get("sources", [])
    snap = Snapshot(name, now, start, served, quakes_all, srcs, ok)
    snap.capped = capped
    snap.raw = raw
    return snap


# ------------------------------------------------------------------ 1 freshness


def freshness(snap: Snapshot) -> dict[str, dict[str, Any]]:
    now = snap.server_time
    events = {**{e["id"]: e for e in snap.quakes_all}, **snap.served}
    per: dict[str, dict[str, Any]] = defaultdict(lambda: defaultdict(list))
    for e in events.values():
        src = e["source"]
        t, rec = ts(e.get("time")), ts(e.get("received_at"))
        last_seen = ts(e.get("last_seen"))
        row = per[src]
        row["n"].append(1)
        if t is None:
            continue
        age = (now - t).total_seconds()
        row["origin"].append(age)
        row["kinds"].append(e["kind"])
        if last_seen is not None:
            row["last_seen"].append((now - last_seen).total_seconds())
        if rec is not None:
            row["newest_rec"].append((now - rec).total_seconds())
            # a revision is news too (an NHC advisory moves the same storm)
            upd = ts(e.get("updated_at"))
            row["change"].append((now - max(rec, upd or rec)).total_seconds())
            lag = (rec - t).total_seconds()
            # only events that HAPPENED while this server was running: anything
            # older came from a startup backfill or a catalogue, and its "lag" is
            # the server's uptime, not the pipeline's latency
            if (snap.start is None or t >= snap.start) and lag >= -FUTURE_TOLERANCE_S:
                (row["lag_q"] if e["kind"] == "earthquake" else row["lag_a"]).append(max(lag, 0.0))
        if age < -FUTURE_TOLERANCE_S:
            (row["future_ongoing"] if e.get("ongoing") else row["future_point"]).append(e["id"])
        severe = e.get("severity") in ("severe", "extreme")
        if age > HORIZON_DAYS * 86400 and not e.get("ongoing") and not severe:
            row["past_horizon"].append(e["id"])
        if (
            last_seen is not None
            and (now - last_seen).total_seconds() > STALE_HOURS * 3600
            and (e.get("ongoing") or e["kind"] != "earthquake")
        ):
            row["stale_unswept"].append(e["id"])
    health = {s["name"]: s for s in snap.sources}
    out: dict[str, dict[str, Any]] = {}
    for src in sorted(set(per) | set(health)):
        row = per.get(src, {})
        h = health.get(src, {})
        last_ok = ts(h.get("last_ok"))
        last_event = ts(h.get("last_event_at"))
        lq, la = row.get("lag_q", []), row.get("lag_a", [])
        out[src] = {
            "served": len(row.get("n", [])),
            "lag_q_n": len(lq),
            "lag_q_p50": pct(lq, 0.5),
            "lag_q_p95": pct(lq, 0.95),
            "lag_a_n": len(la),
            "lag_a_p50": pct(la, 0.5),
            "lag_a_p95": pct(la, 0.95),
            "future_point": row.get("future_point", []),
            "future_ongoing": row.get("future_ongoing", []),
            "past_horizon": row.get("past_horizon", []),
            "stale_unswept": row.get("stale_unswept", []),
            "newest_received_age": min(row["newest_rec"]) if row.get("newest_rec") else None,
            "newest_change_age": min(row["change"]) if row.get("change") else None,
            "last_seen_age": min(row["last_seen"]) if row.get("last_seen") else None,
            "newest_origin_age": min(row["origin"]) if row.get("origin") else None,
            "quake_only": bool(row.get("kinds")) and set(row["kinds"]) == {"earthquake"},
            "last_event_age": (now - last_event).total_seconds() if last_event else None,
            "connected": h.get("connected"),
            "last_ok_age": (now - last_ok).total_seconds() if last_ok else None,
            "last_error": h.get("last_error"),
            "degraded": h.get("degraded"),
            "failed_feeds": h.get("failed_feeds"),
            "events_seen": h.get("events_seen"),
            "ingested": h.get("ingested"),
            "in_health": bool(h),
        }
    return out


# ------------------------------------------------------------------ 2 validity

ENTITY = re.compile(r"&(?:[a-zA-Z]{2,8}|#\d{2,6}|#x[0-9a-fA-F]{2,6});")
TAG = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^<>]*)?/?>")
MOJIBAKE = re.compile(r"Ã[\u0080-¿]|â€|Â[ -¿ ]|�|ãƒ|ã‚")
DRILL = re.compile(
    r"\b(?:test(?:ing)? (?:message|alert)|exercise|drill|training)\b|訓練|取消|キャンセル|"
    r"\bcancel(?:l?ed|lation)?\b",
    re.IGNORECASE,
)


def expected_quake_severity(mag: float | None, depth: float | None, pager: str | None) -> str:
    """`severity_for_quake` as documented in models/event.py."""
    if mag is None:
        sev = 0
    elif mag >= 7.0:
        sev = 4
    elif mag >= 6.0:
        sev = 3
    elif mag >= 4.5:
        sev = 2
    elif mag >= 2.5:
        sev = 1
    else:
        sev = 0
    if depth is not None and depth >= 300:
        sev = min(sev, 2)
    elif depth is not None and depth >= 150:
        sev = max(0, sev - 1)
    if pager:
        p = pager.strip().lower()
        if p == "red":
            sev = 4
        elif p == "orange":
            sev = max(sev, 3)
    return SEVERITIES[sev]


def validity(
    snap: Snapshot, countries: list[dict[str, Any]]
) -> tuple[dict[str, Counter], list[Defect]]:
    now = snap.server_time
    by_code = defaultdict(list)
    for c in countries:
        by_code[c["code"]].append(c)
    counts: dict[str, Counter] = defaultdict(Counter)
    defects: list[Defect] = []
    events = {**{e["id"]: e for e in snap.quakes_all}, **snap.served}

    def add(check: str, e: dict[str, Any], raw: str, expected: str, owner: str) -> None:
        counts[e["source"]][check] += 1
        defects.append(
            Defect(snap.name, check, e["source"], e["id"], raw, expected, owner, e.get("url"))
        )

    for e in events.values():
        src = e["source"]
        counts[src]["n"] += 1
        lat, lon = e.get("lat"), e.get("lon")
        if lat is None or lon is None:
            counts[src]["no_position"] += 1
        else:
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                add(
                    "position_out_of_range",
                    e,
                    f"lat={lat} lon={lon}",
                    "|lat|<=90, |lon|<=180",
                    "C2",
                )
            elif lat == 0 and lon == 0:
                add("position_0_0", e, "lat=0 lon=0", "a real position, or none", "C2")
            code = (e.get("country_code") or "").upper()
            if code and code in by_code and src not in SYNTHETIC:
                dist = min(country_distance_km(lat, lon, c) for c in by_code[code])
                # 500 km: Natural Earth 1:50m drops the smallest islands
                # (Niuatoputapu, Tonga), so an offshore quake 380 km from the
                # nearest DRAWN Tongan coast is still in Tongan waters. The real
                # contradictions measured are thousands of km.
                if dist > 500:
                    swapped = (
                        -90 <= lon <= 90
                        and min(country_distance_km(lon, lat, c) for c in by_code[code]) == 0
                    )
                    here = countries_at(lat, lon, countries)
                    add(
                        "lat_lon_swapped" if swapped else "country_vs_position",
                        e,
                        f"country_code={code} at ({lat:.2f},{lon:.2f}), {dist:.0f} km from {code}; "
                        f"point is in {','.join(here) or 'open sea'}",
                        "a country that contains (or borders) the point, or none",
                        "C2",
                    )
        if e["kind"] == "earthquake" and src not in SYNTHETIC:
            mag, depth = e.get("magnitude"), e.get("depth_km")
            if mag is None:
                counts[src]["quake_no_magnitude"] += 1
            elif mag < -2.0 or mag > 9.6:
                add("magnitude_implausible", e, f"M{mag}", "-2 <= M <= 9.6", "C2")
            if depth is not None and (depth < -5 or depth > 800):
                add("depth_implausible", e, f"depth_km={depth}", "-5..800 km, positive down", "C2")
            if mag is not None:
                want = expected_quake_severity(mag, depth, e.get("alert"))
                got = e.get("severity")
                if got != want and not e.get("tsunami") and e.get("intensity_mmi") is None:
                    check = (
                        "quake_over_graded"
                        if RANK.get(got, 0) > RANK[want]
                        else "quake_under_graded"
                    )
                    add(
                        check,
                        e,
                        f"M{mag} depth={depth} pager={e.get('alert')} -> {got}",
                        want,
                        "C2",
                    )
        exp = ts(e.get("expires"))
        if exp is not None and exp < now - timedelta(seconds=60):
            add(
                "expired_but_served",
                e,
                f"expires={e.get('expires')} ({fmt_s((now - exp).total_seconds())} ago)",
                "withdrawn at expiry",
                "P",
            )
        text = " ".join(
            str(e.get(k) or "") for k in ("title", "place", "description", "instruction")
        )
        m = DRILL.search(text)
        if m:
            add(
                "drill_or_cancelled",
                e,
                f"{m.group(0)!r} in: {text[:100]!r}",
                "never published",
                "C2",
            )
        if (e.get("response_type") or "").lower() == "allclear":
            add(
                "all_clear_published",
                e,
                "response_type=AllClear",
                "a lifted warning is withdrawn",
                "C2",
            )
        label = " ".join(str(e.get(k) or "") for k in ("title", "place", "region", "country"))
        for check, rx in (("html_entity", ENTITY), ("html_tag", TAG), ("mojibake", MOJIBAKE)):
            found = rx.search(label) or rx.search(str(e.get("description") or ""))
            if found:
                add(check, e, f"{found.group(0)!r} in {label[:90]!r}", "plain decoded text", "C2")
    return counts, defects


PHT = timedelta(hours=8)


def superseded(snap: Snapshot, refs: dict[str, Any]) -> list[Defect]:
    """Events whose source has since REPLACED them under another id, still
    served as quakes of their own. Earthquakes are never swept on silence, so
    nothing else ever removes them.

    USGS: `ids` lists every id merged into an event; any of them that is not
    the event's own `id` is a superseded solution. PHIVOLCS: each revision is a
    new bulletin file (B1 -> B2 -> B3F) and the front page keeps only the last;
    it lists the whole current Philippine month, so a bulletin of this month
    missing from it has been replaced or withdrawn.
    """
    out: list[Defect] = []
    events = {**{e["id"]: e for e in snap.quakes_all}, **snap.served}
    merged_into: dict[str, str] = {}
    for f in (refs.get("usgs_all_day") or {}).get("features", []):
        for other in (f["properties"].get("ids") or "").strip(",").split(","):
            if other and other != f["id"]:
                merged_into[other] = f["id"]
    page = (refs.get("phivolcs_page") or {}).get("_text", "")
    bulletins = set(re.findall(r"\d{4}_\d{4}_\d{4,6}_B\d+F?", page))
    page_month = (snap.server_time + PHT).strftime("%Y%m")
    for e in events.values():
        primary = (
            "primary" if e.get("cluster_id") == e["id"] else f"duplicate of {e.get('cluster_id')}"
        )
        if e["source"] == "usgs" and e["source_id"] in merged_into:
            out.append(
                Defect(
                    snap.name,
                    "superseded_id_still_served",
                    "usgs",
                    e["id"],
                    f"USGS now lists {e['source_id']} inside {merged_into[e['source_id']]} "
                    f"({primary}, M{e.get('magnitude')})",
                    "retracted once the source merges it",
                    "C2 (usgs.py) / P",
                    e.get("url"),
                )
            )
        if e["source"] == "phivolcs" and bulletins:
            t = ts(e.get("time"))
            if t and (t + PHT).strftime("%Y%m") == page_month and e["source_id"] not in bulletins:
                stem = e["source_id"][:14]
                now_listed = sorted(b for b in bulletins if b.startswith(stem))
                out.append(
                    Defect(
                        snap.name,
                        "superseded_id_still_served",
                        "phivolcs",
                        e["id"],
                        "bulletin no longer on the PHIVOLCS page "
                        f"({primary}, M{e.get('magnitude')}); "
                        f"page now lists {now_listed or 'nothing for that minute'}",
                        "retracted when PHIVOLCS replaces the bulletin",
                        "C2 (phivolcs.py)",
                        e.get("url"),
                    )
                )
    return out


# ------------------------------------------------------------------ 3 precision


def _close(
    a: dict[str, Any], b: dict[str, Any], dt_max: float, km_max: float
) -> tuple[float, float] | None:
    ta, tb = ts(a["time"]), ts(b["time"])
    if ta is None or tb is None or a.get("lat") is None or b.get("lat") is None:
        return None
    dt = abs((ta - tb).total_seconds())
    if dt > dt_max:
        return None
    km = haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
    if km > km_max:
        return None
    return dt, km


def precision(snap: Snapshot) -> tuple[dict[str, dict[str, Any]], dict[str, Any], list[Defect]]:
    quakes = [
        e
        for e in snap.quakes_all
        if e["source"] not in SYNTHETIC and e.get("lat") is not None and ts(e.get("time"))
    ]
    quakes.sort(key=lambda e: e["time"])
    by_src: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in quakes:
        by_src[e["source"]].append(e)
    defects: list[Defect] = []
    table: dict[str, dict[str, Any]] = {}
    for ref_name in ("usgs", "emsc"):
        refs = by_src.get(ref_name, [])
        for src, items in sorted(by_src.items()):
            if src == ref_name:
                continue
            dmag, dkm, dts, merged, not_merged = [], [], [], 0, 0
            for e in items:
                best = None
                for r in refs:
                    c = _close(e, r, 120.0, 300.0)
                    if c and (
                        best is None or c[0] / 90 + c[1] / 250 < best[0][0] / 90 + best[0][1] / 250
                    ):
                        best = (c, r)
                if not best:
                    continue
                (dt, km), r = best
                dts.append(dt)
                dkm.append(km)
                if e.get("magnitude") is not None and r.get("magnitude") is not None:
                    dmag.append(abs(e["magnitude"] - r["magnitude"]))
                in_window = (
                    dt <= DEDUPE_WINDOW_S
                    and km <= DEDUPE_RADIUS_KM
                    and (
                        e.get("magnitude") is None
                        or r.get("magnitude") is None
                        or abs(e["magnitude"] - r["magnitude"]) <= DEDUPE_MAG
                    )
                )
                if in_window:
                    if e.get("cluster_id") and e.get("cluster_id") == r.get("cluster_id"):
                        merged += 1
                    else:
                        not_merged += 1
            row = table.setdefault(src, {})
            row[ref_name] = {
                "n": len(items),
                "matched": len(dts),
                "dmag_p50": pct(dmag, 0.5),
                "dmag_p95": pct(dmag, 0.95),
                "km_p50": pct(dkm, 0.5),
                "km_p95": pct(dkm, 0.95),
                "dt_p50": pct(dts, 0.5),
                "dt_p95": pct(dts, 0.95),
                "merged": merged,
                "not_merged": not_merged,
            }

    # "Should be one": events from DIFFERENT sources inside the deduper's own
    # thresholds, linked transitively, that still show more than one primary.
    parent = {e["id"]: e["id"] for e in quakes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(quakes):
        ta = ts(a["time"])
        for b in quakes[i + 1 :]:
            tb = ts(b["time"])
            if (tb - ta).total_seconds() > DEDUPE_WINDOW_S:
                break
            if a["source"] == b["source"]:
                continue
            c = _close(a, b, DEDUPE_WINDOW_S, DEDUPE_RADIUS_KM)
            if not c:
                continue
            if (
                a.get("magnitude") is not None
                and b.get("magnitude") is not None
                and abs(a["magnitude"] - b["magnitude"]) > DEDUPE_MAG
            ):
                continue
            parent[find(a["id"])] = find(b["id"])
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in quakes:
        groups[find(e["id"])].append(e)
    multi = [g for g in groups.values() if len(g) > 1]
    extra_primaries = 0
    split_groups = 0
    for g in multi:
        primaries = [e for e in g if e.get("cluster_id") == e["id"]]
        if len(primaries) > 1:
            split_groups += 1
            extra_primaries += len(primaries) - 1
            if split_groups <= 40:
                strongest = max(g, key=lambda e: e.get("magnitude") or -9)
                defects.append(
                    Defect(
                        snap.name,
                        "duplicate_quake_on_screen",
                        "+".join(sorted({e["source"] for e in primaries})),
                        ", ".join(e["id"] for e in primaries[:4]),
                        "; ".join(
                            f"{e['source']} M{e.get('magnitude')} {e['time'][11:19]} "
                            f"({e['lat']:.2f},{e['lon']:.2f})"
                            for e in primaries[:4]
                        ),
                        f"one primary (all within {DEDUPE_WINDOW_S:.0f} s / "
                        f"{DEDUPE_RADIUS_KM:.0f} km "
                        f"/ {DEDUPE_MAG} mag); strongest M{strongest.get('magnitude')}",
                        "P",
                    )
                )
    # Over-merge: one cluster holding two solutions from the SAME source, i.e.
    # two different quakes folded into one (the hidden one is lost to the view).
    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in quakes:
        if e.get("cluster_id"):
            clusters[e["cluster_id"]].append(e)
    over = 0
    for cid, members in clusters.items():
        per_src = Counter(e["source"] for e in members)
        dup = [s for s, n in per_src.items() if n > 1]
        if dup and len(members) > 1:
            over += 1
            if over <= 15:
                same = [e for e in members if e["source"] in dup]
                defects.append(
                    Defect(
                        snap.name,
                        "two_quakes_one_cluster",
                        dup[0],
                        ", ".join(e["id"] for e in same[:3]),
                        "; ".join(
                            f"M{e.get('magnitude')} {e['time'][11:19]} "
                            f"({e['lat']:.2f},{e['lon']:.2f})"
                            for e in same[:3]
                        ),
                        f"distinct quakes in distinct clusters (cluster {cid})",
                        "P",
                    )
                )
    # Whole-hour offsets against USGS / EMSC at the same place and size: the
    # signature of a local time read as UTC (lesson: CENC, JMA EEW, NCS, AFAD).
    offsets: Counter = Counter()
    ref_pts = by_src.get("usgs", []) + by_src.get("emsc", [])
    for src, items in by_src.items():
        if src in ("usgs", "emsc"):
            continue
        for e in items:
            te = ts(e["time"])
            # an event that already has its twin at the same time cannot also
            # be a clock error; without this, a dense swarm (Campi Flegrei)
            # yields chance matches at whole-hour offsets
            if any(_close(e, r, 120.0, 100.0) for r in ref_pts):
                continue
            for r in ref_pts:
                if r.get("magnitude") is None or e.get("magnitude") is None:
                    continue
                if abs(r["magnitude"] - e["magnitude"]) > 0.4:
                    continue
                dt = (te - ts(r["time"])).total_seconds()
                hours = round(dt / 3600)
                if hours == 0 or abs(hours) > 14 or abs(dt - hours * 3600) > 90:
                    continue
                if haversine_km(e["lat"], e["lon"], r["lat"], r["lon"]) <= 30:
                    offsets[(src, hours)] += 1
    summary = {
        "quakes": len(quakes),
        "groups_multi_source": len(multi),
        "groups_split": split_groups,
        "extra_primaries": extra_primaries,
        "over_merged_clusters": over,
        "hour_offsets": {f"{s} {h:+d}h": n for (s, h), n in offsets.most_common() if n >= 2},
    }
    return table, summary, defects


# ------------------------------------------------------------------ 4 importance

CAP_SOURCES = {"meteoalarm", "wmo", "nws"}


def importance(snap: Snapshot) -> tuple[dict[str, Counter], list[Defect]]:
    counts: dict[str, Counter] = defaultdict(Counter)
    defects: list[Defect] = []
    for e in snap.served.values():
        src, sev = e["source"], e.get("severity", "info")
        counts[src][sev] += 1
        counts["ALL"][sev] += 1
        if sev not in ("severe", "extreme"):
            continue
        reason = None
        if e["kind"] == "earthquake" and src not in SYNTHETIC:
            mag = e.get("magnitude")
            if (
                mag is not None
                and mag < 6.0
                and not e.get("tsunami")
                and (e.get("alert") or "").lower() not in ("orange", "red")
            ):
                reason = f"M{mag}, no tsunami flag, PAGER {e.get('alert')}"
        elif src in CAP_SOURCES and sev == "extreme":
            reason = f"CAP {e.get('alert')} at EXTREME (reserved for observed)"
        elif src == "swarm":
            reason = f"synthetic swarm at {sev}"
        if reason:
            counts[src]["suspect_over_graded"] += 1
            defects.append(
                Defect(
                    snap.name,
                    "severe_suspect",
                    src,
                    e["id"],
                    f"{sev}: {reason} | {e.get('title', '')[:70]}",
                    "re-read the original record",
                    "C2",
                    e.get("url"),
                )
            )
    return counts, defects


def _gdacs_kind(code: str) -> str:
    return {
        "EQ": "earthquake",
        "TC": "cyclone",
        "FL": "flood",
        "VO": "volcano",
        "DR": "drought",
        "WF": "wildfire",
    }.get(code, "other")


def missing(snaps: list[Snapshot], refs: dict[str, Any]) -> list[dict[str, Any]]:
    """Every important reference item inside the horizon, and whether each
    server serves it (as a primary, only as a hidden duplicate, or not at all)."""
    now = max(s.server_time for s in snaps)
    items: list[dict[str, Any]] = []
    seen_quakes: list[tuple[datetime, float, float]] = []

    def add_quake(
        origin: str, rid: str, t: datetime, lat: float, lon: float, mag: float, label: str
    ) -> None:
        for t0, la, lo in seen_quakes:
            if abs((t - t0).total_seconds()) < 60 and haversine_km(lat, lon, la, lo) < 100:
                return
        seen_quakes.append((t, lat, lon))
        items.append(
            {
                "ref": origin,
                "id": rid,
                "kind": "earthquake",
                "time": t,
                "lat": lat,
                "lon": lon,
                "mag": mag,
                "label": label,
            }
        )

    for name in ("usgs_sig_week", "usgs_45_week"):
        for f in (refs.get(name) or {}).get("features", []):
            p = f["properties"]
            t = datetime.fromtimestamp(p["time"] / 1000, UTC)
            mag = p.get("mag") or 0
            age = (now - t).total_seconds()
            lon, lat = f["geometry"]["coordinates"][:2]
            if age > HORIZON_DAYS * 86400:
                # The horizon drops quakes after 3 d by design ("a quake is
                # instantaneous"). An orange/red PAGER quake is a disaster whose
                # aftermath outlives that: listed so the choice stays visible.
                if name == "usgs_sig_week" and (p.get("alert") or "") in ("orange", "red"):
                    items.append(
                        {
                            "ref": "USGS",
                            "id": f["id"],
                            "kind": "earthquake",
                            "time": t,
                            "lat": lat,
                            "lon": lon,
                            "mag": mag,
                            "label": f"AGED OUT ({fmt_s(age)}): M{mag} {p.get('place')} "
                            f"pager={p.get('alert')}",
                            "aged_out": True,
                        }
                    )
                continue
            if name == "usgs_45_week" and mag < 5.5:
                continue
            add_quake(
                "USGS",
                f["id"],
                t,
                lat,
                lon,
                mag,
                f"M{mag} {p.get('place')} pager={p.get('alert')} tsunami={p.get('tsunami')}",
            )
    for f in (refs.get("emsc_m5_week") or {}).get("features", []):
        p = f["properties"]
        t = ts(p.get("time"))
        if (
            t is None
            or (now - t).total_seconds() > HORIZON_DAYS * 86400
            or (p.get("mag") or 0) < 5.5
        ):
            continue
        add_quake(
            "EMSC",
            p.get("unid") or f.get("id"),
            t,
            p["lat"],
            p["lon"],
            p.get("mag"),
            f"M{p.get('mag')} {p.get('flynn_region')}",
        )
    for f in (refs.get("gdacs_orange_red") or {}).get("features", []):
        p = f["properties"]
        start = ts((p.get("fromdate") or "") + "Z")
        # `iscurrent=false` is GDACS saying the event is OVER (CLAUDE.md): it
        # must be withdrawn, so it is no reference. Earthquakes are never
        # "current" for long: they are references while inside the horizon.
        if p.get("eventtype") == "EQ":
            if start is None or (now - start).total_seconds() > HORIZON_DAYS * 86400:
                continue
        elif p.get("iscurrent") not in (True, "true"):
            continue
        lon, lat = f["geometry"]["coordinates"][:2]
        items.append(
            {
                "ref": "GDACS",
                "id": f"{p['eventtype']}{p['eventid']}",
                "kind": _gdacs_kind(p["eventtype"]),
                "time": ts(p["fromdate"] + "Z"),
                "lat": lat,
                "lon": lon,
                "mag": None,
                "label": f"{p['alertlevel']} {p.get('name')} "
                f"current={p.get('iscurrent')} to={p.get('todate')}",
                "gdacs": True,
            }
        )
    for storm in (refs.get("nhc") or {}).get("activeStorms", []) or []:
        items.append(
            {
                "ref": "NHC",
                "id": storm.get("id"),
                "kind": "cyclone",
                "time": ts(storm.get("lastUpdate")),
                "lat": storm.get("latitudeNumeric"),
                "lon": storm.get("longitudeNumeric"),
                "mag": None,
                "label": f"{storm.get('classification')} {storm.get('name')} "
                f"{storm.get('intensity')} kt",
                "nhc": True,
            }
        )
    for name in ("ptwc_atom", "ntwc_atom"):
        text = (refs.get(name) or {}).get("_text", "")
        for entry in re.findall(r"<entry>(.*?)</entry>", text, re.S):
            upd = re.search(r"<updated>([^<]+)</updated>", entry)
            t = ts(upd.group(1)) if upd else None
            if t is None or (now - t).total_seconds() > 86400:
                continue
            title = re.search(r"<title>([^<]+)</title>", entry)
            eid = re.search(r"<id>([^<]+)</id>", entry)
            summary = html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(entry)))
            cat = re.search(r"Category:\s*(\w+)", summary)
            items.append(
                {
                    "ref": name[:4].upper(),
                    "id": (eid.group(1) if eid else "").split(":")[-1],
                    "kind": "tsunami",
                    "time": t,
                    "lat": None,
                    "lon": None,
                    "mag": None,
                    "label": f"{cat.group(1) if cat else '?'} "
                    f"{title.group(1).strip() if title else ''}",
                    "atom": True,
                }
            )

    for item in items:
        for snap in snaps:
            item[snap.name] = _presence(item, snap)
    return items


def _presence(item: dict[str, Any], snap: Snapshot) -> str:
    if item.get("atom"):
        hit = [
            e
            for e in snap.served.values()
            if e["source"] == "tsunami" and item["id"] and item["id"] in e["id"]
        ]
        return f"yes {hit[0]['id']} ({hit[0]['severity']})" if hit else "NO"
    if item.get("gdacs"):
        hit = [
            e
            for e in snap.served.values()
            if e["source"] == "gdacs" and e.get("source_id") == item["id"]
        ]
        if hit:
            return f"yes {hit[0]['id']} ({hit[0]['severity']})"
    if item.get("nhc"):
        hit = [e for e in snap.served.values() if e["id"] == f"nhc:{item['id']}"]
        if hit:
            return f"yes {hit[0]['id']} ({hit[0]['severity']})"
    if item["lat"] is None:
        return "NO"
    if item["kind"] == "earthquake":
        best = None
        pool = {e["id"]: e for e in snap.quakes_all}
        pool.update({k: v for k, v in snap.served.items() if v["kind"] == "earthquake"})
        for e in pool.values():
            if e.get("lat") is None or e["source"] in SYNTHETIC:
                continue
            t = ts(e["time"])
            if t is None or abs((t - item["time"]).total_seconds()) > 120:
                continue
            km = haversine_km(item["lat"], item["lon"], e["lat"], e["lon"])
            if km <= 300 and (best is None or km < best[0]):
                best = (km, e)
        if best is None:
            return "NO"
        e = best[1]
        if e.get("cluster_id") in (None, e["id"]):
            return f"yes {e['id']} M{e.get('magnitude')} ({e['severity']})"
        # shown through its cluster's representative: report what the reader
        # actually sees, which is the REPRESENTATIVE's numbers, not these
        rep = pool.get(e["cluster_id"])
        if rep is None:
            return f"HIDDEN {e['id']} (its representative {e['cluster_id']} is not served)"
        return f"yes via {rep['id']} M{rep.get('magnitude')} ({rep['severity']})"
    near = [
        e
        for e in snap.served.values()
        if e["kind"] == item["kind"]
        and e.get("lat") is not None
        and haversine_km(item["lat"], item["lon"], e["lat"], e["lon"]) < 800
    ]
    if near:
        e = max(near, key=lambda x: RANK.get(x["severity"], 0))
        return f"near {e['id']} ({e['severity']})"
    return "NO"


def reliefweb(refs: dict[str, Any], snaps: list[Snapshot]) -> list[dict[str, Any]]:
    text = (refs.get("reliefweb") or {}).get("_text", "")
    out = []
    for attrs, body in re.findall(
        r'<article class="rw-disaster-map__article[^"]*"([^>]*)>(.*?)</article>', text, re.S
    ):
        a = dict(re.findall(r'data-([a-z-]+)="([^"]*)"', attrs))
        t = re.search(r'rw-river-article__title[^>]*><a href="([^"]+)">([^<]+)</a>', body)
        if not t:
            continue
        title = html.unescape(t.group(2))
        month = re.search(r"- (\w{3}) (\d{4})$", title)
        out.append(
            {
                "type": a.get("disaster-type"),
                "status": a.get("disaster-status"),
                "title": title,
                "url": t.group(1),
                "recent": bool(
                    month and month.group(2) == "2026" and month.group(1) in ("Oct", "Sep")
                ),
                "lat": float(a["disaster-lat"]) if a.get("disaster-lat") else None,
                "lon": float(a["disaster-lon"]) if a.get("disaster-lon") else None,
            }
        )
    return out


# ------------------------------------------------------------------ 6 nothing fake

_UNITS = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
NUMBER_WORDS = {
    w: i
    for i, w in enumerate(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
        ]
    )
}
for _tens, _base in (("twenty", 20), ("thirty", 30), ("forty", 40)):
    NUMBER_WORDS[_tens] = _base
    for _i, _word in enumerate(_UNITS, start=1):
        NUMBER_WORDS[f"{_tens}-{_word}"] = _base + _i
        NUMBER_WORDS[f"{_tens} {_word}"] = _base + _i
SOURCE_CLAIM = re.compile(
    r"\b(\d{1,3}|(?:twenty|thirty|forty)[- ](?:one|two|three|four|five|six|seven|eight|nine)"
    r"|twenty|thirty|forty|nineteen|eighteen|seventeen|sixteen|fifteen)"
    r"\s+(?:official\s+)?(?:public\s+)?(?:open\s+)?(?:sources|feeds)\b",
    re.IGNORECASE,
)
PLACEHOLDER = re.compile(
    r"\b(?:test|demo|sample|example|dummy|fake|lorem|placeholder|mock)\b|xxx", re.IGNORECASE
)


def _claimed_counts(text: str) -> list[tuple[str, int]]:
    out = []
    for m in SOURCE_CLAIM.finditer(text):
        word = m.group(1).lower()
        n = int(word) if word.isdigit() else NUMBER_WORDS.get(word)
        if n is not None:
            out.append((m.group(0), n))
    return out


def _walk_dates(obj: Any, path: str = "") -> list[tuple[str, str]]:
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            key_is_date = re.search(r"(as_of|asof|date|updated|time|period)", k, re.I)
            if isinstance(v, str) and key_is_date and ts(v[:25]):
                found.append((f"{path}.{k}", v))
            else:
                found.extend(_walk_dates(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:50]):
            found.extend(_walk_dates(v, f"{path}[{i}]"))
    return found


# Where the page text lives in a checkout, for a target that serves only the
# API (a local `make dev-api`): the same claims, read from the files that the
# next build will ship.
UI_FILES = {
    "page_index": ("index.html", "src/seo.json"),
    "page_manifest": ("public/manifest.webmanifest",),
}


def fake_checks(
    snap: Snapshot, fresh: dict[str, dict[str, Any]], ui_dir: Path | None = None
) -> tuple[list[list[str]], list[Defect]]:
    """What a user sees that is not real live data. Rows: finding, where, evidence."""
    now = snap.server_time
    rows: list[list[str]] = []
    defects: list[Defect] = []
    n_sources = len(snap.sources)

    def flag(
        check: str, where: str, raw: str, expected: str, owner: str, event_id: str = "-"
    ) -> None:
        rows.append([check, where, raw[:160]])
        defects.append(Defect(snap.name, check, where, event_id, raw, expected, owner))

    pages = (
        ("page_index", "G (frontend/index.html) + H (src/seo.json)"),
        ("page_manifest", "G (public/manifest.webmanifest)"),
    )
    for key, owner in pages:
        page = snap.raw.get(key) or {}
        text = page.get("_text", "") if page.get("_status") == 200 else ""
        if not text and ui_dir is not None:
            parts = [ui_dir / rel for rel in UI_FILES[key] if (ui_dir / rel).exists()]
            text = "\n".join(p.read_text(encoding="utf-8") for p in parts)
            key = f"{key} (files: {', '.join(str(p) for p in parts)})"
        if not text:
            rows.append(["surface_not_served", key, f"HTTP {page.get('_status')}"])
            continue
        claims = Counter(n for _, n in _claimed_counts(html.unescape(text)))
        for n, times in sorted(claims.items()):
            if n != n_sources:
                flag(
                    "hard_coded_source_count",
                    key,
                    f"says {n} sources ({times}x) while /api/sources lists {n_sources}",
                    "no number, or the live count",
                    owner,
                )

    dl = snap.raw.get("page_download_latest") or {}
    body = dl.get("_text", "")
    if dl.get("_status") == 200 and body.lstrip().lower().startswith(("<!doctype", "<html")):
        flag(
            "json_resource_answered_by_spa",
            "/download/latest.json",
            "HTTP 200 text/html (the SPA index.html) for a JSON resource",
            "the APK metadata, or a 404",
            "G",
        )
    elif dl.get("_status") == 200:
        try:
            meta = json.loads(body)
        except ValueError:
            flag("download_meta_not_json", "/download/latest.json", body[:80], "JSON", "G")
        else:
            missing_keys = [k for k in ("version", "url", "sha256", "size") if not meta.get(k)]
            rows.append(
                [
                    "download_meta",
                    "/download/latest.json",
                    f"version={meta.get('version')} size={meta.get('size')} missing={missing_keys}",
                ]
            )
            if missing_keys:
                flag(
                    "download_meta_incomplete",
                    "/download/latest.json",
                    f"missing {missing_keys}",
                    "every field read back from the APK",
                    "G",
                )
    else:
        rows.append(["surface_not_served", "/download/latest.json", f"HTTP {dl.get('_status')}"])

    climate = snap.raw.get("api_climate") or {}
    if climate.get("_status") == 200:
        try:
            data = json.loads(climate.get("_text", ""))
        except ValueError:
            data = None
        dates = _walk_dates(data)
        if not dates:
            flag(
                "climate_without_as_of",
                "/api/climate",
                "no as-of date anywhere in the payload",
                "each indicator with its own as-of date",
                "C2",
            )
        for path, value in dates:
            age = (now - ts(value[:25])).total_seconds()
            rows.append(["climate_as_of", f"/api/climate{path}", f"{value} ({fmt_s(age)} old)"])
    else:
        rows.append(["surface_not_served", "/api/climate", f"HTTP {climate.get('_status')}"])

    # A source shown "up" whose data is not current. `last_seen` is refreshed
    # on every poll for each event the source still lists, so a polled source
    # that is up has a last_seen within minutes. EMSC pushes: nothing re-lists.
    for src, f in sorted(fresh.items()):
        if not f["in_health"] or not f["connected"]:
            continue
        if f["served"] == 0:
            flag(
                "up_but_serves_nothing",
                src,
                f"connected, events_seen={f['events_seen']}, ingested={f['ingested']}, "
                "0 events served",
                "a source shown green has current data, or says why not",
                "C2",
            )
        elif f["last_seen_age"] is not None and f["last_seen_age"] > 3600 and src != "emsc":
            flag(
                "up_but_stale",
                src,
                f"connected, last_ok {fmt_s(f['last_ok_age'])} ago, but no served event "
                f"re-confirmed for {fmt_s(f['last_seen_age'])}",
                "fresh last_seen, or the source shown stale",
                "C2",
            )
        # The footer prints "Last event <age>" from `last_event_at`, which a
        # polled source advances on every cycle that returned ANY row -- the
        # same old rows re-listed. Held against the newest event this source
        # actually ADDED or REVISED in the feed.
        # For a quake source the reader takes "Last event" for the last QUAKE:
        # the newest origin time is then the honest comparison.
        lea, newest = f.get("last_event_age"), f.get("newest_change_age")
        origin = f.get("newest_origin_age")
        nothing_new = newest is None or newest - lea > 3 * 3600 if lea is not None else False
        old_quakes = (
            bool(f.get("quake_only"))
            and origin is not None
            and lea is not None
            and origin - lea > 3 * 3600
        )
        if src not in SYNTHETIC and lea is not None and lea < 900 and (nothing_new or old_quakes):
            flag(
                "footer_last_event_not_new",
                src,
                f"/api/sources last_event_at {fmt_s(lea)} ago (footer: 'Last event {fmt_s(lea)}'), "
                f"but its newest new-or-revised event dates from {fmt_s(newest)} ago "
                f"(newest origin {fmt_s(f.get('newest_origin_age'))} ago)",
                "the age of the newest NEW event, or a label that says 'last answer'",
                "C2 (sources/base.py last_event_at) + H (footer label)",
            )

    events = {**{e["id"]: e for e in snap.quakes_all}, **snap.served}
    by_src: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events.values():
        by_src[e["source"]].append(e)
        text = " ".join(str(e.get(k) or "") for k in ("id", "title", "place"))
        m = PLACEHOLDER.search(text)
        if m:
            flag(
                "placeholder_or_test_item",
                e["source"],
                f"{m.group(0)!r} in {text[:120]!r}",
                "real records only",
                "C2",
                e["id"],
            )
    for src, items in sorted(by_src.items()):
        if len(items) < 3 or src in SYNTHETIC:
            continue
        stamped = []
        for e in items:
            t, rec = ts(e.get("time")), ts(e.get("received_at"))
            if t and rec and abs((rec - t).total_seconds()) < 1.0:
                stamped.append(e)
        if len(stamped) >= max(3, len(items) // 2):
            flag(
                "time_stamped_at_ingest",
                src,
                f"{len(stamped)}/{len(items)} events have time == received_at (< 1 s), "
                f"e.g. {stamped[0]['id']}",
                "the source's own event time",
                "C2",
                stamped[0]["id"],
            )
        spots: dict[tuple[float, float], set[str]] = defaultdict(set)
        for e in items:
            if e.get("lat") is not None:
                spots[(round(e["lat"], 4), round(e["lon"], 4))].add(
                    e.get("place") or e.get("title") or ""
                )
        for (la, lo), places in spots.items():
            if len(places) >= 3:
                flag(
                    "shared_placeholder_position",
                    src,
                    f"({la},{lo}) carries {len(places)} different places: {sorted(places)[:3]}",
                    "each event at its own position, or none",
                    "C2",
                )
    return rows, defects


# ------------------------------------------------------------------ output


def table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        lines.append(
            "| " + " | ".join(str(c).replace("|", "/").replace("\n", " ") for c in r) + " |"
        )
    return "\n".join(lines)


def report(
    snaps: list[Snapshot],
    refs: dict[str, Any],
    countries: list[dict[str, Any]],
    ui_dirs: dict[str, Path] | None = None,
) -> tuple[str, dict[str, Any]]:
    out: list[str] = []
    data: dict[str, Any] = {"targets": {}, "defects": []}
    names = [s.name for s in snaps]
    out.append("# SOSForge live data audit\n")
    for s in snaps:
        out.append(
            f"- **{s.name}**: server_time {s.server_time.isoformat(timespec='seconds')}, "
            f"started {s.start.isoformat(timespec='seconds') if s.start else '?'}, "
            f"{len(s.served)} served primaries, {len(s.quakes_all)} quake solutions "
            "(24 h all + 72 h M2.5+, with duplicates), "
            f"reads at the 2000 cap: {s.capped or 'none'}, "
            f"failed reads: {[k for k, v in s.reads_ok.items() if not v] or 'none'}"
        )
    all_defects: list[Defect] = []

    # 1 freshness
    fr = {s.name: freshness(s) for s in snaps}
    srcs = sorted(set().union(*[set(f) for f in fr.values()]))
    out.append("\n## 1. Freshness\n")
    out.append(
        "lag = received_at - origin time, only for events that happened while the server was up. "
        "q = earthquakes, a = alerts (onset-based). newest = age of the source's newest "
        "arrival in the feed. fut! = point events dated in the future; past = non-ongoing, "
        f"non-severe events older than the {HORIZON_DAYS:.0f} d horizon; stale = interval "
        f"events not mentioned for {STALE_HOURS:.0f} h but still served.\n"
    )
    rows = []
    for src in srcs:
        row = [src]
        for n in names:
            f = fr[n].get(src)
            if not f:
                row.append("absent")
                continue
            status = "up" if f["connected"] else ("DOWN" if f["in_health"] else "n/a")
            if f["degraded"]:
                status = f"DEGRADED {f['failed_feeds']}"
            if f["last_error"]:
                status += f" err={str(f['last_error'])[:40]}"
            if f["in_health"] and f["last_ok_age"] is not None and f["last_ok_age"] > 1800:
                status += f" last_ok {fmt_s(f['last_ok_age'])} ago"
            if f["in_health"] and not f["ingested"]:
                status += " ingested=0"
            row.append(
                f"{status}; n={f['served']}; "
                f"q {fmt_s(f['lag_q_p50'])}/{fmt_s(f['lag_q_p95'])} (n{f['lag_q_n']}); "
                f"a {fmt_s(f['lag_a_p50'])}/{fmt_s(f['lag_a_p95'])} (n{f['lag_a_n']}); "
                f"newest {fmt_s(f['newest_received_age'])}; "
                f"fut! {len(f['future_point'])} fut~ {len(f['future_ongoing'])} "
                f"past {len(f['past_horizon'])} stale {len(f['stale_unswept'])}"
            )
            for eid in f["future_point"][:5]:
                all_defects.append(
                    Defect(
                        n,
                        "future_point_event",
                        src,
                        eid,
                        "time > now + 120 s",
                        "rejected at ingest",
                        "P",
                    )
                )
            for eid in f["stale_unswept"][:5]:
                all_defects.append(
                    Defect(
                        n,
                        "stale_not_swept",
                        src,
                        eid,
                        f"last_seen > {STALE_HOURS:.0f} h",
                        "swept",
                        "P",
                    )
                )
        rows.append(row)
    out.append(table(["source", *names], rows))
    data["freshness"] = fr

    # 2 validity
    out.append("\n## 2. Validity\n")
    val = {}
    for s in snaps:
        counts, defects = validity(s, countries)
        for d in superseded(s, refs):
            counts[d.source][d.check] += 1
            defects.append(d)
        val[s.name] = counts
        all_defects.extend(defects)
    checks = sorted({c for v in val.values() for cs in v.values() for c in cs if c not in ("n",)})
    rows = []
    for check in checks:
        row = [check]
        for n in names:
            per = {src: c[check] for src, c in val[n].items() if c[check]}
            total = sum(per.values())
            row.append(
                f"{total}: "
                + ", ".join(f"{k} {v}" for k, v in sorted(per.items(), key=lambda x: -x[1])[:6])
                if total
                else "0"
            )
        rows.append(row)
    out.append(table(["check", *names], rows))
    data["validity"] = {n: {k: dict(v) for k, v in c.items()} for n, c in val.items()}

    # 3 precision
    out.append("\n## 3. Precision (earthquakes, 72 h, every solution)\n")
    prec = {}
    for s in snaps:
        t, summary, defects = precision(s)
        prec[s.name] = (t, summary)
        all_defects.extend(defects)
    for ref_name in ("usgs", "emsc"):
        out.append(
            f"\nAgainst {ref_name.upper()} (best match within 120 s / 300 km): matched/n, "
            "|dmag| p50/p95, km p50/p95, s p50/p95, merged/not merged inside the dedupe window\n"
        )
        srcs = sorted(set().union(*[set(p[0]) for p in prec.values()]))
        rows = []
        for src in srcs:
            row = [src]
            for n in names:
                r = prec[n][0].get(src, {}).get(ref_name)
                if not r or not r["matched"]:
                    row.append(f"0/{r['n'] if r else 0}")
                    continue
                row.append(
                    f"{r['matched']}/{r['n']}; {fmt(r['dmag_p50'])}/{fmt(r['dmag_p95'])}; "
                    f"{fmt(r['km_p50'], 0)}/{fmt(r['km_p95'], 0)} km; "
                    f"{fmt(r['dt_p50'], 0)}/{fmt(r['dt_p95'], 0)} s; "
                    f"{r['merged']}/{r['not_merged']}"
                )
            rows.append(row)
        out.append(table(["source", *names], rows))
    out.append("\nDedupe summary\n")
    keys = [
        "quakes",
        "groups_multi_source",
        "groups_split",
        "extra_primaries",
        "over_merged_clusters",
        "hour_offsets",
    ]
    out.append(table(["measure", *names], [[k, *[prec[n][1][k] for n in names]] for k in keys]))
    data["precision"] = {n: {"table": p[0], "summary": p[1]} for n, p in prec.items()}

    # 4 importance
    out.append("\n## 4. Importance\n")
    imp = {}
    for s in snaps:
        counts, defects = importance(s)
        imp[s.name] = counts
        all_defects.extend(defects)
    srcs = sorted(set().union(*[set(c) for c in imp.values()]), key=lambda x: (x != "ALL", x))
    rows = []
    for src in srcs:
        row = [src]
        for n in names:
            c = imp[n].get(src, Counter())
            total = sum(c[s] for s in SEVERITIES)
            if not total:
                row.append("-")
                continue
            noise = (c["info"] + c["minor"]) / total
            row.append(
                f"n={total}; noise {noise:.0%}; "
                + "/".join(str(c[s]) for s in SEVERITIES)
                + (f"; suspect {c['suspect_over_graded']}" if c["suspect_over_graded"] else "")
            )
        rows.append(row)
    out.append("info/minor/moderate/severe/extreme counts; noise = share info+minor\n")
    out.append(table(["source", *names], rows))
    data["importance"] = {n: {k: dict(v) for k, v in c.items()} for n, c in imp.items()}

    if refs:
        out.append("\n### Reference events inside the horizon: served?\n")
        miss = missing(snaps, refs)
        rows = [
            [
                m["ref"],
                m["id"],
                m["kind"],
                m["time"].isoformat(timespec="minutes") if m["time"] else "?",
                m["label"][:70],
                *[m[n] for n in names],
            ]
            for m in sorted(miss, key=lambda x: (x["ref"], str(x["time"])))
        ]
        out.append(table(["ref", "id", "kind", "time", "what", *names], rows))
        for m in miss:
            for n in names:
                if m[n].startswith(("NO", "HIDDEN")):
                    check, owner = (
                        ("aged_out_major_quake", "P (horizon policy: owner decision)")
                        if m.get("aged_out")
                        else ("missing_reference_event", "C2/P")
                    )
                    all_defects.append(
                        Defect(
                            n, check, m["ref"], m["id"], m["label"][:90], f"served ({m[n]})", owner
                        )
                    )
        data["missing"] = [
            {**m, "time": m["time"].isoformat() if m["time"] else None} for m in miss
        ]
        rw = reliefweb(refs, snaps)
        recent = [r for r in rw if r["recent"]]
        out.append(
            f"\nReliefWeb alert/ongoing list: {len(rw)} items, {len(recent)} from Sep/Oct 2026 "
            f"(outside the {HORIZON_DAYS:.0f} d horizon unless still ongoing):\n"
        )
        out.append(
            table(
                ["type", "status", "title"], [[r["type"], r["status"], r["title"]] for r in recent]
            )
        )
        data["reliefweb"] = rw

    # 5 production vs branch
    if len(snaps) >= 2:
        a, b = snaps[0], snaps[1]
        out.append(f"\n## 5. {a.name} vs {b.name}\n")
        by_check: dict[str, Counter] = defaultdict(Counter)
        for d in all_defects:
            by_check[d.check][d.target] += 1
        rows = [[c, *[by_check[c][n] for n in names]] for c in sorted(by_check)]
        out.append(table(["defect class", *names], rows))
        # After a deploy the journal replay re-serves what the OLD build stored
        # (its grading, its clusters, ids since superseded) until they age out:
        # counted apart, so a post-deploy run tells the new code from its residue.
        for s in snaps:
            if not s.start:
                continue
            known = {**{e["id"]: e for e in s.quakes_all}, **s.served}
            event_defects = [d for d in all_defects if d.target == s.name and d.event_id in known]
            carried = [
                d
                for d in event_defects
                if (ts(known[d.event_id].get("received_at")) or s.start) < s.start
            ]
            out.append(
                f"\n{s.name}: {len(carried)} of {len(event_defects)} event defects are on events "
                f"received before this server started ({s.start.isoformat(timespec='minutes')}), "
                "i.e. restored from the journal"
            )
        # Like for like. A polled source re-lists every CURRENT alert each
        # cycle, so after one cycle both servers should hold the same alert
        # set; quakes only compare from the moment the younger one started.
        since = max((s.start for s in snaps if s.start), default=None)
        out.append(
            "\nLike for like: alerts = non-quake primaries served now; quakes = primaries "
            f"with an origin after {since.isoformat(timespec='minutes') if since else '?'} "
            "(the younger server's start)\n"
        )
        per: dict[str, dict[str, list[int]]] = defaultdict(lambda: {n: [0, 0] for n in names})
        for s in snaps:
            for e in s.served.values():
                if e["kind"] != "earthquake":
                    per[e["source"]][s.name][0] += 1
                elif since and (ts(e["time"]) or since) >= since:
                    per[e["source"]][s.name][1] += 1
        rows = [
            [src, *[f"{per[src][n][0]} / {per[src][n][1]}" for n in names]]
            for src in sorted(per)
            if any(sum(v) for v in per[src].values())
        ]
        out.append(table(["source (alerts / quakes)", *names], rows))
        sev_a = {
            k
            for k, e in a.served.items()
            if e.get("severity") in ("severe", "extreme") or e.get("tsunami")
        }
        sev_b = {
            k
            for k, e in b.served.items()
            if e.get("severity") in ("severe", "extreme") or e.get("tsunami")
        }
        out.append(
            f"\nsevere+/tsunami served: {a.name} {len(sev_a)}, {b.name} {len(sev_b)}, "
            f"only in {a.name} {len(sev_a - sev_b)}, only in {b.name} {len(sev_b - sev_a)}\n"
        )
        rows = []
        for k in sorted(sev_a - sev_b)[:40]:
            e = a.served[k]
            rows.append(
                [
                    f"only {a.name}",
                    k,
                    e["severity"],
                    e["time"],
                    e.get("title", "")[:60],
                    b.served[k]["severity"] if k in b.served else "absent",
                ]
            )
        for k in sorted(sev_b - sev_a)[:40]:
            e = b.served[k]
            rows.append(
                [
                    f"only {b.name}",
                    k,
                    e["severity"],
                    e["time"],
                    e.get("title", "")[:60],
                    a.served[k]["severity"] if k in a.served else "absent",
                ]
            )
        out.append(table(["where", "id", "severity", "time", "title", "other side"], rows))

    out.append("\n## 6. Nothing fake: user-facing surfaces that are not real live data\n")
    for s in snaps:
        rows, defects = fake_checks(s, fr[s.name], (ui_dirs or {}).get(s.name))
        all_defects.extend(defects)
        out.append(f"\n**{s.name}**\n")
        out.append(table(["finding", "where", "evidence"], rows) if rows else "nothing found")

    out.append("\n## Candidate defects (open the original record before reporting any)\n")
    seen = set()
    rows = []
    for d in all_defects:
        key = (d.target, d.check, d.event_id)
        if key in seen:
            continue
        seen.add(key)
        rows.append(d.row())
    per_check: dict[tuple[str, str], int] = Counter()
    shown = []
    for r in rows:
        per_check[(r[0], r[1])] += 1
        if per_check[(r[0], r[1])] <= 12:
            shown.append([*r[:4], r[4][:140], r[5][:80], r[6]])
    out.append(table(["target", "check", "source", "id", "raw", "should be", "owner"], shown))
    hidden = len(rows) - len(shown)
    if hidden:
        out.append(f"\n({hidden} more candidates in --json)")
    data["defects"] = [d.__dict__ for d in all_defects]
    return "\n".join(out) + "\n", data


# ------------------------------------------------------------------ main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--target",
        action="append",
        default=[],
        help="NAME=BASE_URL, repeatable (first one is the reference side)",
    )
    ap.add_argument("--save", type=Path, help="directory to keep every raw read")
    ap.add_argument("--load", type=Path, help="analyse saved reads, no network")
    ap.add_argument("--json", type=Path, help="write findings as JSON")
    ap.add_argument("--no-refs", action="store_true", help="skip the reference feeds")
    ap.add_argument(
        "--pause", type=float, default=0.4, help="seconds between two reads of one target"
    )
    ap.add_argument("--cache", type=Path, default=Path(tempfile.gettempdir()) / "sosforge-audit")
    ap.add_argument(
        "--ui",
        action="append",
        default=[],
        help="NAME=FRONTEND_DIR: read the page text from these files when NAME serves only the API",
    )
    args = ap.parse_args(argv)

    raw: dict[str, Any] = {}
    refs: dict[str, Any] = {}
    if args.load:
        for path in sorted(args.load.glob("target_*.json")):
            raw[path.stem.removeprefix("target_")] = json.loads(path.read_text(encoding="utf-8"))
        ref_path = args.load / "references.json"
        if ref_path.exists() and not args.no_refs:
            refs = json.loads(ref_path.read_text(encoding="utf-8"))
        order = [t.split("=", 1)[0] for t in args.target] or sorted(raw)
        raw = {k: raw[k] for k in order if k in raw}
    else:
        targets = args.target or ["prod=https://sosforge.soclose.co"]
        pairs = [t.split("=", 1) for t in targets]
        with ThreadPoolExecutor(max_workers=len(pairs) + 1) as pool:
            futures = {name: pool.submit(read_target, base, args.pause) for name, base in pairs}
            ref_future = None if args.no_refs else pool.submit(read_references, datetime.now(UTC))
            raw = {name: f.result() for name, f in futures.items()}
            refs = ref_future.result() if ref_future else {}
        if args.save:
            args.save.mkdir(parents=True, exist_ok=True)
            for name, body in raw.items():
                (args.save / f"target_{name}.json").write_text(json.dumps(body), encoding="utf-8")
            if refs:
                (args.save / "references.json").write_text(json.dumps(refs), encoding="utf-8")

    if not raw:
        print("nothing to audit", file=sys.stderr)
        return 2
    snaps = [build_snapshot(name, body) for name, body in raw.items()]
    countries = load_countries(args.cache)
    if not countries:
        print("warning: no country polygons, country/position check skipped", file=sys.stderr)
    ui_dirs = {name: Path(path) for name, path in (u.split("=", 1) for u in args.ui)}
    text, data = report(snaps, refs, countries, ui_dirs)
    print(text)
    if args.json:
        args.json.write_text(json.dumps(data, default=str, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
