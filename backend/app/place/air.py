"""`air` (CAMS global, 0.4 deg) and `pollen` (CAMS Europe, 0.1 deg), from the
Open-Meteo open-data bucket. Every value is a MODEL forecast, and the panel
says so: "CAMS model forecast, not a measurement".

The two indices are ours, computed from the same series and listed in
`data.derived`:

- EU: the EEA European Air Quality Index, 2024 revision of the bands (ETC HE
  Report 2024/17), transcribed from the EEA's own page
  (airindex.eea.europa.eu/AQI/index.html, captured 2026-10-27): hourly
  concentrations of PM2.5, PM10, O3, NO2 and SO2, the poorest band wins. The
  table is in whole ug/m3, so a value is rounded before it is banded.
- US: the EPA AQI, from the breakpoint table EPA's AQS publishes
  (aqs.epa.gov/aqsweb/documents/codetables/aqi_breakpoints.csv, captured
  2026-10-27; 2024 PM2.5 breakpoints): PM 24 h means, O3 8 h, NO2 and SO2
  1 h, of the model series. Gases are converted from ug/m3 to ppb at 25 C
  and 1 atm. CO is not read yet (its bucket cassette is not recorded), so
  the US index is computed without it and says so.

Not read until their bucket cassettes are recorded: carbon monoxide,
aerosol optical depth, clear-sky UV (cams_global), and alder, mugwort and
olive pollen (cams_europe).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from app.place import block as B
from app.place import om
from app.place.context import Context
from app.place.geo import in_box

SRC_CAMS = "https://open-meteo.com/en/docs/air-quality-api"


def _source(name: str, year: str) -> B.Source:
    tail = f" {year}" if year else ""
    return B.Source(
        name,
        SRC_CAMS,
        "Air quality data by Open-Meteo.com (open-data bucket), from the Copernicus "
        f"Atmosphere Monitoring Service. Generated using Copernicus Atmosphere Monitoring "
        f"Service information{tail}; neither the European Commission nor ECMWF is responsible "
        "for any use of it. Indices computed by SOSForge (modified)",
        "CC BY 4.0",
    )


SPEC_AIR = B.Spec(
    "air", "model", "model_run", 36 * 3600.0, _source("CAMS global via Open-Meteo", "")
)
SPEC_POLLEN = B.Spec(
    "pollen", "model", "model_run", 60 * 3600.0, _source("CAMS Europe via Open-Meteo", "")
)

AIR_VARS = {
    "pm2_5": "pm2_5_ugm3",
    "pm10": "pm10_ugm3",
    "ozone": "o3_ugm3",
    "nitrogen_dioxide": "no2_ugm3",
    "sulphur_dioxide": "so2_ugm3",
    "dust": "dust_ugm3",
    "uv_index": "uv_index",
}
POLLEN_VARS = {
    "birch_pollen": "birch_grains_m3",
    "grass_pollen": "grass_grains_m3",
    "ragweed_pollen": "ragweed_grains_m3",
}
# cams_europe's own BBOX, written [71.95,-24.95,30.049995,44.95]
EUROPE = (30.05, 71.95, -24.95, 44.95)

# ---------------------------------------------------------------- EEA EAQI
# (upper bound of each band, ug/m3), Good, Fair, Moderate, Poor, Very poor;
# above the last is Extremely poor.
EAQI_BANDS = ("good", "fair", "moderate", "poor", "very_poor", "extremely_poor")
EAQI = {
    "pm2_5": (5, 15, 50, 90, 140),
    "pm10": (15, 45, 120, 195, 270),
    "ozone": (60, 100, 120, 160, 180),
    "nitrogen_dioxide": (10, 25, 60, 100, 150),
    "sulphur_dioxide": (20, 40, 125, 190, 275),
}
EAQI_SCALE = "EEA European Air Quality Index, 2024 revision of the bands (ETC HE Report 2024/17)"

# ------------------------------------------------------------------ US EPA
# (low breakpoint, high breakpoint, low AQI, high AQI, category), from the
# AQS table: PM2.5 88101 "24 HOUR", PM10 81102 "24 HOUR", ozone 44201
# "8-HR RUN AVG BEGIN HOUR" (ppm), NO2 42602 and SO2 42401 "1 HOUR" (ppb).
EPA: dict[str, tuple[tuple[float, float, int, int, str], ...]] = {
    "pm2_5": (
        (0.0, 9.0, 0, 50, "good"),
        (9.1, 35.4, 51, 100, "moderate"),
        (35.5, 55.4, 101, 150, "usg"),
        (55.5, 125.4, 151, 200, "unhealthy"),
        (125.5, 225.4, 201, 300, "very_unhealthy"),
        (225.5, 325.4, 301, 500, "hazardous"),
    ),
    "pm10": (
        (0.0, 54.0, 0, 50, "good"),
        (55.0, 154.0, 51, 100, "moderate"),
        (155.0, 254.0, 101, 150, "usg"),
        (255.0, 354.0, 151, 200, "unhealthy"),
        (355.0, 424.0, 201, 300, "very_unhealthy"),
        (425.0, 604.0, 301, 500, "hazardous"),
    ),
    "ozone": (
        (0.0, 0.054, 0, 50, "good"),
        (0.055, 0.07, 51, 100, "moderate"),
        (0.071, 0.085, 101, 150, "usg"),
        (0.086, 0.105, 151, 200, "unhealthy"),
        (0.106, 0.2, 201, 300, "very_unhealthy"),
    ),
    "nitrogen_dioxide": (
        (0.0, 53.0, 0, 50, "good"),
        (54.0, 100.0, 51, 100, "moderate"),
        (101.0, 360.0, 101, 150, "usg"),
        (361.0, 649.0, 151, 200, "unhealthy"),
        (650.0, 1249.0, 201, 300, "very_unhealthy"),
        (1250.0, 2049.0, 301, 500, "hazardous"),
    ),
    "sulphur_dioxide": (
        (0.0, 35.0, 0, 50, "good"),
        (36.0, 75.0, 51, 100, "moderate"),
        (76.0, 185.0, 101, 150, "usg"),
        (186.0, 304.0, 151, 200, "unhealthy"),
    ),
}
EPA_SCALE = (
    "US EPA AQI, 2024 PM2.5 breakpoints; PM 24 h mean, O3 8 h, NO2 and SO2 1 h, "
    "of the model series; CO not included"
)
# molar masses (g/mol): ppb = ug/m3 * 24.45 / M at 25 C and 1 atm
MOLAR = {"ozone": 48.00, "nitrogen_dioxide": 46.0055, "sulphur_dioxide": 64.066}
# EPA truncation before the lookup
TRUNCATE = {"pm2_5": 1, "pm10": 0, "ozone": 3, "nitrogen_dioxide": 0, "sulphur_dioxide": 0}


def eaqi(values: dict[str, float | None]) -> dict[str, Any]:
    worst: tuple[int, str] | None = None
    for pollutant, bounds in EAQI.items():
        v = values.get(pollutant)
        if v is None:
            continue
        whole = round(v)
        band = next((i for i, top in enumerate(bounds) if whole <= top), len(bounds))
        if worst is None or band > worst[0]:
            worst = (band, pollutant)
    return {
        "band": EAQI_BANDS[worst[0]] if worst else None,
        "dominant": worst[1] if worst else None,
        "scale": EAQI_SCALE,
    }


def _truncate(value: float, digits: int) -> float:
    factor = 10**digits
    return math.floor(value * factor + 1e-9) / factor


def epa_sub_index(pollutant: str, concentration: float) -> tuple[int, str] | None:
    c = _truncate(concentration, TRUNCATE[pollutant])
    for low, high, i_low, i_high, category in EPA[pollutant]:
        if low <= c <= high:
            return round((i_high - i_low) / (high - low) * (c - low) + i_low), category
    return None  # beyond the table this transcription carries


def _mean(values: list[float | None], need: int) -> float | None:
    got = [v for v in values if v is not None]
    return sum(got) / len(got) if len(got) >= need else None


def us_aqi(past: dict[str, list[float | None]], now_index: int) -> dict[str, Any]:
    """`past[var]` holds the hourly series, `now_index` the current hour."""

    def window(var: str, hours: int) -> list[float | None]:
        series = past.get(var) or []
        return series[max(0, now_index - hours + 1) : now_index + 1]

    concentrations: dict[str, float | None] = {
        "pm2_5": _mean(window("pm2_5", 24), 18),
        "pm10": _mean(window("pm10", 24), 18),
    }
    for gas, hours, need in (
        ("ozone", 8, 6),
        ("nitrogen_dioxide", 1, 1),
        ("sulphur_dioxide", 1, 1),
    ):
        mean = _mean(window(gas, hours), need)
        ppb = mean * 24.45 / MOLAR[gas] if mean is not None else None
        concentrations[gas] = (ppb / 1000.0 if gas == "ozone" else ppb) if ppb is not None else None
    best: tuple[int, str, str] | None = None
    for pollutant, c in concentrations.items():
        if c is None:
            continue
        found = epa_sub_index(pollutant, c)
        if found and (best is None or found[0] > best[0]):
            best = (found[0], found[1], pollutant)
    return {
        "value": best[0] if best else None,
        "category": best[1] if best else None,
        "dominant": best[2] if best else None,
        "scale": EPA_SCALE,
    }


def _hour_floor(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def _rows(series: om.Series, names: dict[str, str], digits: int) -> list[dict[str, Any]]:
    rows = []
    for k, t in enumerate(series.times):
        row: dict[str, Any] = {"time": B.iso(t)}
        for var, field in names.items():
            row[field] = B.rounded(series.values[var][k], digits)
        rows.append(row)
    return rows


async def build_air(ctx: Context) -> B.Block:
    hour = _hour_floor(ctx.now)
    series = await om.series(
        ctx, "cams_global", list(AIR_VARS), hour - timedelta(hours=23), hour + timedelta(hours=48)
    )
    now_index = series.times.index(hour)
    rows = _rows(series, AIR_VARS, 1)
    now_row = rows[now_index]
    if all(v is None for k, v in now_row.items() if k != "time"):
        return B.unavailable(SPEC_AIR, "masked", "CAMS global has no value at this hour")
    current = {var: series.values[var][now_index] for var in AIR_VARS}
    data = {
        "cell": {"lat": series.cell[0], "lon": series.cell[1]},
        "now": now_row,
        "aqi": {"eu": eaqi(current), "us": us_aqi(series.values, now_index)},
        "hours": rows[now_index:],
        "derived": ["aqi"],
    }
    block = B.ok(
        SPEC_AIR,
        series.run,
        data,
        fetched_at=series.fetched_at,
        source=_source(SPEC_AIR.source.name, B.years_of(series.run)),
    )
    return om.with_refresh_error(block, series, SPEC_AIR)


async def build_pollen(ctx: Context) -> B.Block:
    if not in_box(ctx.lat, ctx.lon, *EUROPE):
        return B.unavailable(SPEC_POLLEN, "no_coverage", "outside the CAMS Europe domain")
    hour = _hour_floor(ctx.now)
    try:
        series = await om.series(
            ctx, "cams_europe", list(POLLEN_VARS), hour, hour + timedelta(hours=48)
        )
    except om.NoCell as exc:
        return B.unavailable(SPEC_POLLEN, exc.code, exc.reason)
    rows = _rows(series, POLLEN_VARS, 1)
    if all(v is None for k, v in rows[0].items() if k != "time"):
        return B.unavailable(SPEC_POLLEN, "masked", "CAMS Europe has no pollen value at this hour")
    data = {
        "cell": {"lat": series.cell[0], "lon": series.cell[1]},
        "now": rows[0],
        "hours": rows,
    }
    block = B.ok(
        SPEC_POLLEN,
        series.run,
        data,
        fetched_at=series.fetched_at,
        source=_source(SPEC_POLLEN.source.name, B.years_of(series.run)),
    )
    return om.with_refresh_error(block, series, SPEC_POLLEN)
