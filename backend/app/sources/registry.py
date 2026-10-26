"""Sources added after the core set, with their own settings.

`main.build_sources()` builds the original twenty-eight from `core/config.py`.
The sources below live here, settings included, so adding one touches only
`app/sources/`: `build_sources()` appends `extra_sources()` and that is the
whole wiring. Same `SOS_` prefix and `.env` file as the core settings, so
`SOS_ENABLE_IMO=false` works exactly like `SOS_ENABLE_JMA_EEW=false`.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.sources.base import Source
from app.sources.iceland import ImoQuakeSource, ImoVolcanoSource
from app.sources.jma_volcano import JmaVolcanoSource
from app.sources.ncs import NcsSource


class ExtraSourceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SOS_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Iceland (IMO). The API answers in 1.5 to 10 s: do not poll it hard.
    enable_imo: bool = True
    imo_poll_seconds: float = 120.0
    # Below M1 the feed is ~75 micro-quakes a day nobody feels; M1+ is ~10.
    imo_min_magnitude: float = 1.0
    enable_imo_volcano: bool = True
    imo_volcano_poll_seconds: float = 600.0

    # JMA volcanic warnings. The list changes a few times a month.
    enable_jma_volcano: bool = True
    jma_volcano_poll_seconds: float = 300.0

    # India (NCS). 630 KB of HTML per call: every three minutes is plenty.
    enable_ncs: bool = True
    ncs_poll_seconds: float = 180.0


def extra_sources(settings: ExtraSourceSettings | None = None) -> list[Source]:
    settings = settings or ExtraSourceSettings()
    built: list[Source] = []
    if settings.enable_imo:
        built.append(
            ImoQuakeSource(settings.imo_poll_seconds, min_magnitude=settings.imo_min_magnitude)
        )
    if settings.enable_imo_volcano:
        built.append(ImoVolcanoSource(settings.imo_volcano_poll_seconds))
    if settings.enable_jma_volcano:
        built.append(JmaVolcanoSource(settings.jma_volcano_poll_seconds))
    if settings.enable_ncs:
        built.append(NcsSource(settings.ncs_poll_seconds))
    return built
