r"""PHIVOLCS -- Philippine Institute of Volcanology and Seismology.

Why this source exists. The Philippines sits on the Pacific ring with 115
million people on it, and this product could not see a single Philippine
earthquake that the global catalogues missed. Measured on 2026-08-26 over the
seven days the ingestion horizon can reach, matched with this product's own
dedup thresholds (90 s, 250 km): PHIVOLCS published **483 events, of which 408
(84%) are in neither USGS nor EMSC**. USGS carried nine of them. The feed goes
down to M1.0 and it is 18 minutes behind real time.

A previous audit wrote this source off as "HTML only". It is HTML, and that
turned out not to be the objection it sounds like: the page is a single table
whose every row carries a link to that event's own bulletin, and the bulletin
FILENAME is a better identifier and a better timestamp than anything in the
visible columns.

Traps verified against the real payload (captured 2026-08-26, 1881 rows):

- **The page is 3.6 MB uncompressed and 138 KB gzipped.** The server sets
  `vary: Accept-Encoding` and honours gzip, which is the only reason polling
  this is reasonable at all. httpx asks for it by default; do not remove that.
- **The displayed time is Philippine time and says so only in a column
  header**, in a 12-hour clock with no seconds: `26 August 2026 - 07:14 PM`.
- **The bulletin filename carries the same instant in UTC**, and sometimes
  with seconds: `2026_0826_1114_B1.html` for that same 07:14 PM row. Verified
  across a date rollover, which is the case that would expose a wrong offset:
  `23 August 2026 - 07:12 AM` links to `2026_0822_231249_B1F.html`, so 07:12
  PHT on the 23rd is 23:12:49 UTC on the 22nd. Exactly UTC+8, and the
  filename is the more precise of the two. We read the filename and keep the
  displayed local string in `raw`.
- **The displayed time is NOT a usable key.** 43 of the 1881 rows share a
  date-and-minute with another row: two distinct earthquakes in the same
  minute is ordinary here. A key derived from the visible timestamp would
  silently merge them into one event.
- **Five rows link to a bulletin that another row also links to.** Keying on
  the filename collapses those, which is what should happen -- they are the
  same bulletin listed twice, not two earthquakes.
- **One href in 1881 is malformed**: `2026_0818_1350_B2html`, with the dot
  before the extension missing. One bad row must not cost the other 1880.
- **The archive hrefs are Windows paths** (`\..\..\2026_Earthquake_Information
  \July\...`), backslashes and all.
- **Depth is zero-padded** (`030`, `007`) and the location text is mangled
  with tabs and newlines mid-sentence.
- **The month boundary is a blind spot, and there is an archive for it.** The
  front page lists the current PHILIPPINE month, so on 1 September it resets
  and the last days of August fall off it. `EQLatest-Monthly/<year>/<year>_
  <Month>.html` serves the finished month (July: 200, 3072 rows, same table
  shape); the CURRENT month answers 404 there, so a 404 is "not archived yet",
  not an outage. We poll the previous month's archive for the first few days
  of a new month, the same way the Peru source handles its year rollover.
- **The `F` in a bulletin code means nothing we could prove.** The obvious
  guess is "felt", and it is wrong or at least unsupported: across 1881 rows
  the F bulletins have a median magnitude of 2.20 against 2.30 for the rest,
  and 4% versus 2% at M4 and above. If F marked felt reports those
  distributions would not be interchangeable. Lesson 27 says a code is not a
  code until you have read the other end of it, so the bulletin code goes into
  `raw` uninterpreted rather than becoming a badge that means whatever the
  reader assumes.
- **Country is stamped from the source's own reference, not from a box.**
  Every location reads "<N> km <bearing> of <locality> (<province>)", and N is
  small: median 21 km, 90th percentile 67 km. Only 2% exceed 150 km, and those
  are the Sarangani-area events 300 to 400 km out, whose coordinates
  (lat 2-4.5, lon 126-128) are in Indonesian water. So we read the source as
  saying "Philippines" when it places an event near a Philippine locality, and
  we decline to extrapolate when it does not -- lesson 12, and the same
  discipline as the Mexican-state and Canadian-province tables.
- **The server does not send its intermediate certificate.** It presents only
  the leaf (`CN=*.phivolcs.dost.gov.ph`, issued by `GlobalSign RSA OV SSL CA
  2018`) and stops there. curl on macOS succeeds because the OS store chases
  the issuer itself; certifi ships ROOTS only, so httpx fails with
  `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate` -- and
  a Linux deployment fails exactly the same way, which is where this actually
  runs. The fix is not to switch verification off on an emergency product: we
  add the one missing intermediate to certifi's roots and keep full chain and
  hostname verification. Verified: `Verify return code: 0 (ok)`.
- A name filter would have been the wrong mechanism for that, and the data
  says so: a regex for foreign country names matched three rows, all of them
  the town of **Palauig**, Zambales, because it contains "Palau". Lesson 19
  in its natural habitat.
"""

from __future__ import annotations

import asyncio
import calendar
import logging
import re
import ssl
from datetime import UTC, datetime, timedelta, timezone

import certifi
import httpx

from app.models.event import Event, Kind, severity_for_quake
from app.sources.base import Emit, Source

log = logging.getLogger(__name__)

BASE = "https://earthquake.phivolcs.dost.gov.ph"
LATEST_URL = f"{BASE}/"
ARCHIVE_URL = f"{BASE}/EQLatest-Monthly/{{year}}/{{year}}_{{month}}.html"
USER_AGENT = "SOSForge/1.0 (+https://soclose.co)"

# The bulletins are Word exports of 40 KB each; we never fetch them, but the
# front page is a real page and a real link for a reader.
PAGE_URL = "https://earthquake.phivolcs.dost.gov.ph/"

# Philippine Standard Time. No daylight saving, and the country has not
# observed any since 1978.
PHT = timezone(timedelta(hours=8))

RE_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
RE_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
RE_TAG = re.compile(r"<[^>]+>")
RE_HREF = re.compile(r'href="([^"]+)"', re.I)
RE_DATE_CELL = re.compile(r"^\d{1,2}\s+\w+\s+\d{4}")
# "2026_0826_1114_B1.html", and the seconds are optional -- both shapes are in
# the same page.
RE_BULLETIN = re.compile(r"^(\d{4})_(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})?_(\S+?)\.?html$", re.I)
# "087 km N 07 W of Itbayat (Batanes)"
RE_DISTANCE = re.compile(r"^\s*(\d+)\s*km", re.I)

# Beyond this, "N km from a Philippine locality" stops being a statement that
# the epicentre is in the Philippines. Measured: median 21 km, p90 67 km, and
# only 2% of rows exceed this -- the far-offshore Sarangani events, which are
# in Indonesian water. Generous by an order of magnitude over the normal case,
# and it refuses exactly the ambiguous tail.
DOMESTIC_RADIUS_KM = 150.0


# The intermediate PHIVOLCS omits from its TLS handshake: "GlobalSign RSA OV
# SSL CA 2018", valid to 2028-11-21, itself issued by "GlobalSign Root CA -
# R3" which certifi already trusts. Embedding it lets us keep verification
# fully on instead of reaching for `verify=False` (see the module docstring).
#
# This is public CA material, not a secret, and it is pinned to nothing: if
# PHIVOLCS moves to another CA the handshake fails closed and the source goes
# red, which is the honest failure for this product.
GLOBALSIGN_RSA_OV_SSL_CA_2018 = """\
-----BEGIN CERTIFICATE-----
MIIETjCCAzagAwIBAgINAe5fIh38YjvUMzqFVzANBgkqhkiG9w0BAQsFADBMMSAw
HgYDVQQLExdHbG9iYWxTaWduIFJvb3QgQ0EgLSBSMzETMBEGA1UEChMKR2xvYmFs
U2lnbjETMBEGA1UEAxMKR2xvYmFsU2lnbjAeFw0xODExMjEwMDAwMDBaFw0yODEx
MjEwMDAwMDBaMFAxCzAJBgNVBAYTAkJFMRkwFwYDVQQKExBHbG9iYWxTaWduIG52
LXNhMSYwJAYDVQQDEx1HbG9iYWxTaWduIFJTQSBPViBTU0wgQ0EgMjAxODCCASIw
DQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBAKdaydUMGCEAI9WXD+uu3Vxoa2uP
UGATeoHLl+6OimGUSyZ59gSnKvuk2la77qCk8HuKf1UfR5NhDW5xUTolJAgvjOH3
idaSz6+zpz8w7bXfIa7+9UQX/dhj2S/TgVprX9NHsKzyqzskeU8fxy7quRU6fBhM
abO1IFkJXinDY+YuRluqlJBJDrnw9UqhCS98NE3QvADFBlV5Bs6i0BDxSEPouVq1
lVW9MdIbPYa+oewNEtssmSStR8JvA+Z6cLVwzM0nLKWMjsIYPJLJLnNvBhBWk0Cq
o8VS++XFBdZpaFwGue5RieGKDkFNm5KQConpFmvv73W+eka440eKHRwup08CAwEA
AaOCASkwggElMA4GA1UdDwEB/wQEAwIBhjASBgNVHRMBAf8ECDAGAQH/AgEAMB0G
A1UdDgQWBBT473/yzXhnqN5vjySNiPGHAwKz6zAfBgNVHSMEGDAWgBSP8Et/qC5F
JK5NUPpjmove4t0bvDA+BggrBgEFBQcBAQQyMDAwLgYIKwYBBQUHMAGGImh0dHA6
Ly9vY3NwMi5nbG9iYWxzaWduLmNvbS9yb290cjMwNgYDVR0fBC8wLTAroCmgJ4Yl
aHR0cDovL2NybC5nbG9iYWxzaWduLmNvbS9yb290LXIzLmNybDBHBgNVHSAEQDA+
MDwGBFUdIAAwNDAyBggrBgEFBQcCARYmaHR0cHM6Ly93d3cuZ2xvYmFsc2lnbi5j
b20vcmVwb3NpdG9yeS8wDQYJKoZIhvcNAQELBQADggEBAJmQyC1fQorUC2bbmANz
EdSIhlIoU4r7rd/9c446ZwTbw1MUcBQJfMPg+NccmBqixD7b6QDjynCy8SIwIVbb
0615XoFYC20UgDX1b10d65pHBf9ZjQCxQNqQmJYaumxtf4z1s4DfjGRzNpZ5eWl0
6r/4ngGPoJVpjemEuunl1Ig423g7mNA2eymw0lIYkN5SQwCuaifIFJ6GlazhgDEw
fpolu4usBCOmmQDo8dIm7A9+O4orkjgTHY+GzYZSR+Y0fFukAj6KYXwidlNalFMz
hriSqHKvoflShx8xpfywgVcvzfTO3PYkz6fiNJBonf6q8amaEsybwMbDqKWwIX7e
SPY=
-----END CERTIFICATE-----
"""


def build_ssl_context() -> ssl.SSLContext:
    """certifi's roots plus the one intermediate the server forgets to send.

    Hostname checking and certificate verification both stay enabled; the only
    thing added is the missing link in the chain.
    """
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_verify_locations(cadata=GLOBALSIGN_RSA_OV_SSL_CA_2018)
    return context


def _text(fragment: str) -> str:
    """Cell text: tags out, entities and stray whitespace collapsed."""
    plain = RE_TAG.sub("", fragment).replace("&nbsp;", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", plain).strip()


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        # Depth arrives zero-padded ("030"); float() does not mind.
        return float(value.strip())
    except (TypeError, ValueError):
        return None


def bulletin_key(href: str) -> str | None:
    """The bulletin filename stem, from a Windows-style relative href."""
    if not href:
        return None
    name = href.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return name or None


def parse_bulletin_time(filename: str) -> datetime | None:
    """The UTC instant encoded in the bulletin filename.

    This is the timestamp we trust: it is UTC where the table shows local
    time, and it carries seconds where the table shows only minutes. Verified
    against the displayed Philippine time across a date rollover.
    """
    match = RE_BULLETIN.match(filename)
    if not match:
        return None
    year, month, day, hour, minute, second, _code = match.groups()
    try:
        return datetime(
            int(year), int(month), int(day), int(hour), int(minute), int(second or 0), tzinfo=UTC
        )
    except ValueError:
        # A malformed date is a reason to fall back on the displayed time,
        # never a reason to invent one.
        return None


def parse_local_time(value: str) -> datetime | None:
    """`26 August 2026 - 07:14 PM`, Philippine time, to UTC.

    The fallback for a row whose bulletin filename is unreadable -- and one of
    the 1881 measured rows is exactly that.
    """
    cleaned = re.sub(r"\s+", " ", value).replace("- ", "").strip()
    for fmt in ("%d %B %Y %I:%M %p", "%d %B %Y %H:%M"):
        try:
            naive = datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
        return naive.replace(tzinfo=PHT).astimezone(UTC)
    return None


def _country(place: str) -> str | None:
    """Philippines when the source places the event near a Philippine
    locality. See DOMESTIC_RADIUS_KM and the module docstring."""
    match = RE_DISTANCE.match(place)
    if not match:
        return None
    return "Philippines" if float(match.group(1)) <= DOMESTIC_RADIUS_KM else None


def parse_row(cells: list[str]) -> Event | None:
    """One table row -> an Event. `cells` are the raw <td> fragments."""
    when = _text(cells[0])
    if not RE_DATE_CELL.match(when):
        # A header, a month banner, or a spacer row.
        return None

    href_match = RE_HREF.search(cells[0])
    filename = bulletin_key(href_match.group(1)) if href_match else None
    time = parse_bulletin_time(filename) if filename else None
    if time is None:
        time = parse_local_time(when)
    if time is None:
        # Without an instant there is no chronology and no key.
        return None

    # The filename stem is the identifier. It survives two earthquakes in the
    # same minute, which the displayed timestamp does not.
    if filename:
        source_id = re.sub(r"\.?html$", "", filename, flags=re.I)
    else:
        source_id = time.strftime("%Y%m%d%H%M%S")

    lat, lon = _number(_text(cells[1])), _number(_text(cells[2]))
    # Lesson 15: a wrong position is far worse than a missing one, and the
    # model RAISES on an out-of-range coordinate -- one bad row unguarded
    # would take the whole batch down with it.
    if lat is not None and not -90 <= lat <= 90:
        lat = None
    if lon is not None and not -180 <= lon <= 180:
        lon = None

    depth = _number(_text(cells[3]))
    magnitude = _number(_text(cells[4]))
    place = _text(cells[5])

    return Event(
        id=f"phivolcs:{source_id}",
        source="phivolcs",
        source_id=source_id,
        kind=Kind.EARTHQUAKE,
        time=time,
        lat=lat,
        lon=lon,
        depth_km=depth,
        magnitude=magnitude,
        # The bulletins say "Ms" where they name a scale at all; the table
        # does not, so we do not claim one it never stated.
        mag_type=None,
        place=place,
        country=_country(place),
        # Depth matters here more than almost anywhere: this is a subduction
        # margin and the deep events under it are felt very differently from
        # the shallow ones on the same magnitude.
        severity=severity_for_quake(magnitude, depth),
        title=f"M {magnitude} -- {place}" if magnitude is not None else place,
        url=PAGE_URL,
        raw={"local_time": when, "bulletin": source_id},
    )


def pages_to_poll(now: datetime) -> list[str]:
    """Which pages can hold a current event.

    The front page lists the current PHILIPPINE month, so for the first days
    of a new month the tail of the previous one lives only in the archive.
    Three days of overlap covers the ingestion horizon and costs one extra
    request a cycle for three days a month.
    """
    pages = [LATEST_URL]
    local = now.astimezone(PHT)
    if local.day <= 3:
        previous = local.replace(day=1) - timedelta(days=1)
        pages.append(
            ARCHIVE_URL.format(year=previous.year, month=calendar.month_name[previous.month])
        )
    return pages


class PhivolcsSource(Source):
    """PHIVOLCS -- the Philippine national bulletin table."""

    name = "phivolcs"
    kind = "poll"

    def __init__(self, poll_seconds: float = 120.0, window_days: float = 7.0):
        super().__init__()
        self.poll_seconds = poll_seconds
        # The page is a whole month, 1881 events by the 26th. Emitting all of
        # it every cycle would be 1881 pipeline round-trips to re-say what the
        # store already knows, so the source carries its own relevance rule
        # (lesson 6), generously wider than the three-day ingestion horizon.
        self.window_days = window_days

    def parse_payload(self, html: str, now: datetime | None = None) -> list[Event]:
        cutoff = (now or datetime.now(UTC)) - timedelta(days=self.window_days)
        seen: set[str] = set()
        events: list[Event] = []
        for row in RE_ROW.findall(html):
            cells = RE_CELL.findall(row)
            if len(cells) != 6:
                continue
            try:
                event = parse_row(cells)
            except Exception as exc:
                # One malformed row must not cost us the other 1880.
                log.warning("phivolcs: unreadable row (%s)", exc)
                continue
            if event is None or event.time < cutoff:
                continue
            # Five rows in 1881 link to a bulletin another row also links to:
            # the same bulletin listed twice, not two earthquakes.
            if event.source_id in seen:
                continue
            seen.add(event.source_id)
            events.append(event)
        events.sort(key=lambda e: e.time, reverse=True)
        return events

    async def run(self, emit: Emit) -> None:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html",
            # 3.6 MB against 138 KB. httpx would ask anyway; being explicit
            # keeps the reason visible to whoever edits this next.
            "Accept-Encoding": "gzip, deflate",
        }
        async with httpx.AsyncClient(
            timeout=90.0, headers=headers, follow_redirects=True, verify=build_ssl_context()
        ) as client:
            while True:
                events: list[Event] = []
                # Lesson 9: a multi-feed source counts its successes. Zero
                # successes is not `ok()`, however many pages we asked for.
                successes = 0
                last_error: Exception | None = None
                for url in pages_to_poll(datetime.now(UTC)):
                    try:
                        resp = await client.get(url)
                        if resp.status_code == 404 and url != LATEST_URL:
                            # The current month is not archived yet. That is
                            # the normal state of the archive, not an outage.
                            log.info("phivolcs: %s not archived yet", url)
                            successes += 1
                            continue
                        resp.raise_for_status()
                        events.extend(self.parse_payload(resp.text))
                        successes += 1
                    except Exception as exc:
                        last_error = exc
                        log.warning("%s: %s (%s)", self.name, exc, url)

                if successes:
                    by_id = {e.source_id: e for e in events}
                    ordered = sorted(by_id.values(), key=lambda e: e.time, reverse=True)
                    for event in ordered:
                        await emit(event)
                    self.health.ok(len(ordered))
                elif last_error is not None:
                    self.health.fail(last_error)
                await asyncio.sleep(self.poll_seconds)
