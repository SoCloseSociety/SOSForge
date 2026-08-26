"""Normalized event model, shared by all sources."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

log = logging.getLogger(__name__)


class Kind(str, Enum):
    EARTHQUAKE = "earthquake"
    TSUNAMI = "tsunami"
    VOLCANO = "volcano"
    CYCLONE = "cyclone"
    FLOOD = "flood"
    WILDFIRE = "wildfire"
    DROUGHT = "drought"
    STORM = "storm"
    HEAT = "heat"
    # Both of these ALREADY arrive -- EONET publishes landslides, Meteoalarm
    # publishes avalanche warnings as awareness type 9 -- and both were mapped
    # to OTHER, where they sat next to marine advisories and dust. A reader
    # looking for them could not find them, and a filter that cannot be
    # selected is a filter that reads as "no such thing here".
    LANDSLIDE = "landslide"
    AVALANCHE = "avalanche"
    # No position, forecast-oriented by nature, and its own iconography:
    # it does not belong in the OTHER catch-all with landslides and dust.
    SPACE_WEATHER = "space_weather"
    OTHER = "other"


class Severity(str, Enum):
    INFO = "info"
    MINOR = "minor"
    MODERATE = "moderate"
    SEVERE = "severe"
    EXTREME = "extreme"


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_utc(value: str | None) -> datetime | None:
    """Parses an ISO timestamp and brings it back to UTC.

    The trap this helper exists to close: `fromisoformat("2026-08-17T10:00")`
    returns a NAIVE datetime, and `.astimezone(UTC)` then interprets it as the
    SERVER'S LOCAL time. A backend in Paris would silently shift every event
    of a source that omits its timezone by two hours -- no crash, just wrong
    times on an emergency product. Here, a timestamp without a timezone is
    declared UTC, which is what all our sources do.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# Caps on the free text a feed can hand us. They are safety bounds, not
# editorial ones: measured on 1631 real NWS alerts and 2622 Meteoalarm ones,
# the longest `instruction` ever seen was 1155 characters and the longest
# `description` 2711. `instruction` is therefore never cut in practice -- it
# is the part that tells a person what to DO, and half an instruction is a
# dangerous thing to print. `description` is the part that can be resumed
# elsewhere, so it carries the tighter bound: 300 alerts land in the opening
# websocket snapshot, and every character is paid for on a phone.
INSTRUCTION_MAX = 2000
DESCRIPTION_MAX = 1200

# A blank line, or a line that opens a bullet, starts a new paragraph.
# CAP text arrives hard-wrapped for a teletype at about 60 columns; a browser
# does its own wrapping, and honouring the teletype's line breaks produces a
# ragged column. Re-flowing inside a paragraph is the normalization; the
# paragraph structure itself is meaning ("* WHAT... * WHERE... * WHEN...")
# and is kept.
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n+|\n(?=\s*[*-]\s)")


def normalize_text(value: str | None, limit: int) -> str | None:
    """Free text from a feed, turned into data we are willing to store.

    This is NOT sanitizing markup, and it deliberately does not try to be:
    an alert legitimately says `temperatures < 32F` or `SLOW DOWN & MOVE
    OVER`, and a filter that strips `<` or unescapes `&amp;` would corrupt
    the real text of an emergency instruction to protect against a threat
    that belongs one layer further out. What this guarantees is that the
    value is TEXT -- no control characters, bounded length, no markup
    *introduced* by us -- and the renderer's job is to put it in a text node.
    Nothing in this backend ever emits it as HTML.

    Removed: control characters (a lone `\r`, a NUL, the ANSI escapes that
    a terminal consumer would obey). Kept: paragraph structure. Bounded: the
    total length, cut on a paragraph or sentence boundary so the tail that
    survives is a whole thought rather than half a word.
    """
    if not value or not isinstance(value, str):
        return None
    # A carriage return becomes a line break rather than disappearing. Dropped
    # outright, `line one\rline two` from a CR-only producer comes out as
    # `line onetwo` -- two words welded into one inside an emergency
    # instruction, which is the kind of quiet corruption this whole function
    # exists to avoid.
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = "".join(c for c in value if c == "\n" or c == "\t" or c.isprintable())
    paragraphs = [" ".join(p.split()) for p in _PARAGRAPH_BREAK.split(cleaned)]
    text = "\n\n".join(p for p in paragraphs if p)
    if not text:
        return None
    if len(text) <= limit:
        return text
    head = text[:limit]
    # Cut back to the last boundary we can find, in decreasing order of how
    # clean the break is. The 60% floor stops a text with no boundary at all
    # from being reduced to almost nothing.
    cut = max(head.rfind("\n\n"), head.rfind(". "), head.rfind(" "))
    if cut > limit * 0.6:
        head = head[:cut]
    return head.rstrip(" .,;:") + " ..."


class Event(BaseModel):
    """An event, whatever its source."""

    id: str = Field(description="stable identifier: <source>:<source_id>")
    source: str
    source_id: str
    kind: Kind = Kind.OTHER

    # time of the event itself, and time when SOSForge saw it go by
    time: datetime
    received_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime | None = None
    # last time a source mentioned this event. Used to purge "ongoing" alerts
    # whose source has stopped publishing them: without it, a dissipated
    # cyclone or an old volcanic bulletin stayed displayed indefinitely, since
    # the ingestion horizon precisely exempts ongoing alerts.
    last_seen: datetime = Field(default_factory=utcnow)

    lat: float | None = None
    lon: float | None = None
    depth_km: float | None = None

    magnitude: float | None = None
    mag_type: str | None = None

    place: str = ""
    region: str | None = None
    country: str | None = None
    # ISO 3166-1 alpha-2 code, resolved in the pipeline. None when we cannot
    # honestly conclude (high seas): the UI then shows a globe.
    country_code: str | None = None

    severity: Severity = Severity.INFO
    # Event declared ONGOING by its source (active EONET fire, active NHC
    # storm, current GDACS alert). It escapes the ingestion horizon as long as
    # its source keeps publishing it -- the sweep of alerts gone silent is
    # what will remove it, not its age.
    # An automatic solution, not yet reviewed by a human. The first automatic
    # magnitude of a large quake is routinely off by up to a full unit and
    # relocated by tens of kilometres in the minutes that follow. Every agency
    # labels it; so must we, or a screenshot of the wrong number travels
    # further than the correction.
    preliminary: bool = False
    # Shaking, as opposed to size. The only quantity that answers the question
    # the person who just felt it is actually asking, and magnitude does not.
    intensity_mmi: float | None = None
    # How many people reported feeling it. Absent is NOT zero: most of the
    # planet has no reporters, and a zero would be a claim we cannot make.
    felt_reports: int | None = None
    # What people REPORTED feeling (USGS `cdi`, the DYFI community intensity),
    # as opposed to `intensity_mmi`, which is what a ground-motion model
    # ESTIMATES they felt. They are the same scale and they disagree often,
    # and the difference is exactly the interesting part: the modelled value
    # arrives in seconds and is a guess, the reported one arrives in minutes
    # and is a report from people -- so they are two fields, never averaged
    # into one number that would be neither.
    intensity_cdi: float | None = None
    ongoing: bool = False
    # When the SOURCE states an expiry (NWS, Meteoalarm and the CAP feeds all
    # do). An explicit end beats every heuristic we could apply: waiting for
    # six hours of silence to drop a tornado warning that expired at 15:00
    # keeps a red polygon over a quiet county all afternoon.
    expires: datetime | None = None
    tsunami: bool = False
    alert: str | None = Field(default=None, description="USGS PAGER: green/yellow/orange/red")

    title: str = ""
    url: str | None = None

    # What to DO. Every serious CAP consumer leads with this, and until now it
    # was downloaded on every NWS poll and thrown away: a person inside a
    # tornado polygon got a severity, a place, an age and a link. `headline`
    # says what is happening; `instruction` is the only field that says
    # "move to an interior room on the lowest floor".
    #
    # It is attacker-influenced text from feeds we do not control. It is
    # stored as DATA (see `normalize_text`) and must reach the browser as a
    # text node -- never as markup, on either side.
    instruction: str | None = None
    # The body of the alert: the WHAT / WHERE / WHEN / IMPACTS block. Present
    # on 100% of NWS alerts, 94% of Meteoalarm ones. Bounded harder than the
    # instruction, because it is the part a reader can do without.
    description: str | None = None
    # CAP `responseType`, verbatim from the standard's own closed vocabulary:
    # Shelter, Evacuate, Prepare, Execute, Avoid, Monitor, Assess, AllClear,
    # None. One word that classifies the action, present on 99.9% of NWS
    # alerts and 100% of Meteoalarm ones -- so it is usable as an icon or a
    # badge where the full instruction does not fit.
    response_type: str | None = None

    # ---------------------------------------------------------- tsunami
    # The two questions a coastal reader actually has, and neither of them is
    # "what magnitude was it".
    #
    # When the first wave is forecast to reach the nearest threatened place,
    # and which place that is. Read from the warning centre's own bulletin,
    # never computed here: a travel time we estimated ourselves would be a
    # number this product has no business publishing.
    wave_eta: datetime | None = None
    wave_eta_site: str | None = None
    # What a tide gauge ACTUALLY recorded, above the normal tide level, and
    # where. This is the difference between a cancelled advisory and Tohoku,
    # and it is the one field here that is a measurement rather than a
    # forecast. Absent means no gauge has reported yet -- never "no wave".
    wave_max_m: float | None = None
    wave_max_site: str | None = None

    # Forecast positions, when the source publishes them (NHC cyclone tracks).
    # A first-class field rather than a corner of `raw`, because `public()`
    # strips `raw` before sending to the browser -- and a forecast the client
    # never receives is a forecast that does not exist.
    forecast_track: list[dict[str, Any]] | None = None

    # filled by the deduplicator: several sources describe the same event
    cluster_id: str | None = None
    revision: int = 0

    raw: dict[str, Any] = Field(default_factory=dict, repr=False)

    @field_validator("time", "received_at", "updated_at", "last_seen", "expires", "wave_eta")
    @classmethod
    def _must_be_aware(cls, value: datetime | None) -> datetime | None:
        """A naive datetime compared to an aware one raises TypeError and kills
        the source that produced it. Every normalizer is careful about this;
        the model is where the guarantee belongs, so a future one cannot forget.
        Naive means UTC here, which is what all our sources publish."""
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=UTC)

    @field_validator("instruction")
    @classmethod
    def _clean_instruction(cls, value: str | None) -> str | None:
        return normalize_text(value, INSTRUCTION_MAX)

    @field_validator("description")
    @classmethod
    def _clean_description(cls, value: str | None) -> str | None:
        return normalize_text(value, DESCRIPTION_MAX)

    @field_validator("response_type", "wave_eta_site", "wave_max_site")
    @classmethod
    def _clean_label(cls, value: str | None) -> str | None:
        """A one-line label (a CAP responseType, a gauge name). Same rules as
        the long text, on a much shorter leash: these are printed inside a
        badge, and a feed that sends a paragraph where a word belongs must
        not be able to blow the layout open."""
        return normalize_text(value, 80)

    @field_validator("url")
    @classmethod
    def _only_real_links(cls, value: str | None) -> str | None:
        """A feed's `url` ends up in an `href`. Escaping protects the
        ATTRIBUTE, never the SCHEME: `javascript:alert(1)` survives every
        escape intact and becomes a link that runs as the page.

        The live site's Content-Security-Policy blocks that today, but that
        policy lives in an nginx file on one VPS, outside this repository. Any
        redeploy from this repo, any second host, any container run on its own
        has no such protection -- so the product carries its own.

        Only absolute http(s) survives. A relative URL is dropped too: nothing
        here legitimately serves its own pages from a feed value.
        """
        if value is None:
            return None
        # Control characters are the classic way to smuggle a scheme past a
        # naive check: `java\tscript:` is read as `javascript:` by browsers.
        cleaned = "".join(c for c in value if c.isprintable()).strip()
        if not cleaned:
            return None
        scheme, _, rest = cleaned.partition(":")
        if scheme.lower() not in ("http", "https") or not rest.startswith("//"):
            log.debug("dropped a non-http link from a feed: %.60s", value)
            return None
        return cleaned

    @field_validator("lat")
    @classmethod
    def _valid_latitude(cls, value: float | None) -> float | None:
        if value is not None and not -90 <= value <= 90:
            # Lesson 15: a wrong position is far worse than a missing one. Only
            # parse_iso6709 bounded anything, so any other source could inject a
            # point off the globe and nothing downstream would notice.
            raise ValueError(f"latitude out of range: {value}")
        return value

    @field_validator("lon")
    @classmethod
    def _valid_longitude(cls, value: float | None) -> float | None:
        if value is not None and not -180 <= value <= 180:
            raise ValueError(f"longitude out of range: {value}")
        return value

    @property
    def age_seconds(self) -> float:
        return (utcnow() - self.time).total_seconds()

    def fingerprint(self) -> str:
        """Content fingerprint: used to detect a revision of an event already seen."""
        parts = [
            f"{self.magnitude}",
            f"{round(self.lat, 3) if self.lat is not None else None}",
            f"{round(self.lon, 3) if self.lon is not None else None}",
            f"{self.depth_km}",
            self.place,
            self.severity.value,
            str(self.tsunami),
            # The END of the alert belongs here. Without it, a warning
            # re-issued with a later `ends` -- how NWS extends a tornado
            # warning, several times an hour -- looked identical to the one
            # already stored, and kept the old end: purged while still in
            # force, or left standing after being cut short.
            str(self.ongoing),
            self.expires.isoformat() if self.expires else "None",
            # The ORIGIN TIME, to the second. Agencies revise it, and two
            # events fifteen minutes apart fingerprinted identically, so the
            # wrong time stayed in the feed and the date sort put the event in
            # the wrong place. Second resolution because a revision is minutes
            # apart, never microseconds: finer precision would only add noise.
            str(int(self.time.timestamp())),
            # The FORECAST. It was made a first-class field precisely because
            # "a forecast the client never receives is a forecast that does not
            # exist" -- and then the fingerprint could not see it, so a
            # stationary storm whose forecast shifted looked unchanged.
            json.dumps(self.forecast_track, sort_keys=True, default=str),
            # The TITLE. It is the only channel by which a swarm's count
            # reaches the browser, since `public()` strips `raw`: a swarm
            # growing from 9 quakes to 30 never updated on screen.
            self.title,
            # WHAT PEOPLE FELT and WHAT THE SEA DID. Same reason as the
            # forecast above, and the same trap: these arrive LATER than the
            # event -- DYFI needs minutes to collect reports, a tide gauge
            # needs the wave to travel. USGS republishes the same quake with
            # `felt` climbing from 12 to 1200; a warning centre reissues the
            # same bulletin with the first observed amplitude in it. Left out
            # of the fingerprint, every one of those updates is a `noop` and
            # the browser keeps the version where nobody had felt anything
            # and no gauge had seen a thing.
            f"{self.intensity_mmi}|{self.intensity_cdi}|{self.felt_reports}",
            f"{self.wave_eta}|{self.wave_max_m}|{self.wave_max_site}",
            # The INSTRUCTION and the response type. A warning that is
            # extended or upgraded rewrites what to do, and that rewrite is
            # the whole point of re-reading the alert.
            f"{self.instruction}|{self.response_type}|{self.description}",
        ]
        return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]

    def public(self) -> dict[str, Any]:
        """Payload sent to the browser (without `raw`, which is heavy)."""
        return self.model_dump(mode="json", exclude={"raw"})


# Below this depth, a large quake is a long slow sway a thousand kilometres
# wide and damages almost nothing. Fiji-Tonga, Vrancea and the Sea of Okhotsk
# produce M7s at these depths routinely.
DEEP_FOCUS_KM = 300.0
INTERMEDIATE_KM = 150.0

_LADDER = list(Severity)


def _step(severity: Severity, by: int) -> Severity:
    index = max(0, min(len(_LADDER) - 1, _LADDER.index(severity) + by))
    return _LADDER[index]


def severity_for_quake(
    mag: float | None,
    depth_km: float | None = None,
    pager: str | None = None,
) -> Severity:
    """Magnitude is where this starts, not where it ends.

    A M7.2 at 600 km under the Sea of Okhotsk is felt as a sway and breaks
    nothing. A M6.2 at 10 km under a city is a mass-casualty event. Ranking
    them on magnitude alone put the harmless one above the deadly one, and the
    depth was sitting in the event the whole time, unread.

    `pager` is the USGS PAGER estimate, which already folds in depth AND the
    population actually exposed -- when it speaks, it knows more than we do.
    """
    severity = severity_from_magnitude(mag)

    if depth_km is not None and depth_km >= DEEP_FOCUS_KM:
        # never the top alarm, whatever the magnitude
        severity = min(severity, Severity.MODERATE, key=_LADDER.index)
    elif depth_km is not None and depth_km >= INTERMEDIATE_KM:
        severity = _step(severity, -1)

    # PAGER only ever promotes. A green estimate on a M7.4 means "few
    # casualties expected", not "small quake", and demoting on it would hide
    # an event that is about to be revised.
    if pager:
        rank = pager.strip().lower()
        if rank == "red":
            severity = Severity.EXTREME
        elif rank == "orange":
            severity = max(severity, Severity.SEVERE, key=_LADDER.index)

    return severity


def severity_from_magnitude(mag: float | None, tsunami: bool = False) -> Severity:
    """In-house severity scale, calibrated on typical felt intensity/damage."""
    if tsunami:
        return Severity.EXTREME
    if mag is None:
        return Severity.INFO
    if mag >= 7.0:
        return Severity.EXTREME
    if mag >= 6.0:
        return Severity.SEVERE
    if mag >= 4.5:
        return Severity.MODERATE
    if mag >= 2.5:
        return Severity.MINOR
    return Severity.INFO


# --------------------------------------------------------------- CAP severity
#
# CAP (Common Alerting Protocol) is what Meteoalarm, the WMO aggregate and the
# NWS all speak. It carries THREE independent axes, and this product read only
# one of them:
#
#     severity   Extreme / Severe / Moderate / Minor / Unknown
#     urgency    Immediate / Expected / Future / Past / Unknown
#     certainty  Observed / Likely / Possible / Unlikely / Unknown
#
# Reading `severity` alone flattened them. Measured on the live feed of
# 2026-08-26, that cost the product its one summary judgment: 461 of the 480
# Meteoalarm warnings sat at SEVERE -- the rank this product gives a M6.5
# earthquake -- and the single most frequent entry in the whole feed was
# "Orange Thunderstorm warning". A US "Fire Weather Watch" that might happen
# the day after tomorrow outranked a shallow M5.
#
# The rule below extends what `severity_for_quake` already does rather than
# inventing a second philosophy. There, magnitude is the base and DEPTH -- does
# the energy actually reach people -- moves it; PAGER, which knows the exposed
# population, is allowed to promote and never to demote. Here:
#
#   * the agency's own rank is the base. It is the only quantity in the
#     payload, and CAP defines it in harm terms: Extreme is "extraordinary
#     threat to life or property", Severe "significant threat";
#
#   * `certainty` is this product's PAGER -- the field that knows more than we
#     do about whether the harm is real. A warning is a FORECAST of harm until
#     the agency says it is OBSERVING it, and a forecast does not belong on the
#     same rung as a measurement. So an observed alert keeps the identity
#     mapping and everything else sits one step below it. Measured on the real
#     payloads of 2026-08-26, that is what separates the Spanish red rain
#     warning already dropping 180 mm (Extreme/Immediate/Observed, "extreme or
#     catastrophic damages to people ... may occur") from the 40 Kazakh
#     fire-danger bulletins forecast for tomorrow at the very same rank;
#
#   * a rank the agency is only half sure of (`Possible`, `Unlikely`) drops one
#     further. This is the "Fire Weather Watch" case: Severe, but Possible.
#
# What deliberately does NOT enter the base: `urgency`, except for `Past`.
# Severity answers "how bad", urgency answers "how soon", and collapsing them
# re-creates exactly the flattening this rule exists to undo. Demoting a
# warning because it has not started yet is also lesson 14 in a new costume: a
# weather warning is PUBLISHED BEFORE it starts, and that advance notice is the
# whole point of it. `Past` is the one exception, and it is not about timing but
# about harm: the danger is over.
#
# Nothing here promotes above the agency's own rank. No CAP field tells us who
# is exposed, so there is no honest way to say a warning is worse than the
# agency called it.

CAP_RANKS = {"minor": 1, "moderate": 2, "severe": 3, "extreme": 4}

# What the agency says it is SEEING: its rank is taken at face value.
_CAP_OBSERVED = {
    1: Severity.MINOR,
    2: Severity.MODERATE,
    3: Severity.SEVERE,
    4: Severity.EXTREME,
}
# What the agency says it EXPECTS: one step below, whatever the colour on the
# national map. EXTREME then means what it says on this product -- a measured
# mass-casualty event, a tsunami in the water, a M7 -- and stays empty on a day
# when none of that has happened, which is the honest answer.
_CAP_FORECAST = {
    1: Severity.INFO,
    2: Severity.MINOR,
    3: Severity.MODERATE,
    4: Severity.SEVERE,
}


def severity_for_cap(
    rank: str | None,
    urgency: str | None = None,
    certainty: str | None = None,
    *,
    observed: bool = False,
    actionable: bool | None = None,
) -> Severity:
    """CAP severity / urgency / certainty -> this product's ladder.

    `rank` is the CAP `severity` element by name ("Severe"), or any national
    scale already translated into that vocabulary -- Meteoalarm's awareness
    level is one, and it is the only scale its ten countries share.

    `observed=True` forces the observed reading for a source we already know is
    not forecasting. It exists for exactly one caller (the NWS tsunami
    bulletins, see `nws.py`) and is not a general escape hatch.
    """
    tier = CAP_RANKS.get((rank or "").strip().lower(), 0)
    if not tier:
        # Unknown is not a low rank, it is an absent one. Nothing can be
        # concluded, and this product says nothing rather than something wrong.
        return Severity.INFO

    certainty = (certainty or "").strip().lower()
    urgency = (urgency or "").strip().lower()

    seen = observed or certainty == "observed"
    severity = (_CAP_OBSERVED if seen else _CAP_FORECAST)[tier]

    if certainty in ("possible", "unlikely"):
        severity = _step(severity, -1)
        # ...but the TOP rank has a floor. A national service publishing RED is
        # at the top of its own scale, and "take action now" is what red means
        # in every European country's public communication. Demoting a forecast
        # one rung is right -- a forecast of harm is not a measurement of harm.
        # Carrying it a second rung is not: it puts a red warning BELOW a
        # routine orange one that happens to be marked Observed, an inversion
        # the reader would notice the moment they opened the national site next
        # to ours, and would be right to distrust us for.
        if tier == 4:
            severity = max(severity, Severity.SEVERE, key=_LADDER.index)

    # A CAP document carrying NEITHER an instruction NOR a description is a
    # statement about conditions: it tells nobody to do anything, because
    # there is nothing to do yet. Measured on 70 real documents at the top two
    # ranks, and consistent per ISSUER rather than per alert -- India 17/17
    # carry actionable text, Kazakhstan 0/14, issuer 066 0/8. That is what
    # separates 75 routine fire-danger bulletins from "Extremely Heavy Rain"
    # over Uttar Pradesh, which a blanket demotion would have taken with them.
    #
    # `None` means we have not read the document yet, and that is NOT the same
    # as knowing it says nothing: demoting on absence of knowledge would rank
    # an alert by how recently we happened to meet it.
    if actionable is False and not seen:
        severity = _step(severity, -1)

    if urgency == "past":
        # The danger has been and gone. Keeping it visible is right; keeping it
        # ranked is not. (No source in the current set publishes `Past` today:
        # this branch is a guard, not a measured behaviour.)
        severity = min(severity, Severity.MINOR, key=_LADDER.index)

    return severity
