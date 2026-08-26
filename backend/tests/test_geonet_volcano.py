"""GeoNet volcanic alert levels (New Zealand).

Both fixtures are VERBATIM. `VAL` is an excerpt of
`https://api.geonet.org.nz/volcano/val` (Accept:
`application/vnd.geo+json;version=2`) and `BULLETINS` are entries taken exactly
as served from `https://api.geonet.org.nz/news/geonet`, both captured on
2026-08-26. The zero-width spaces in two of the bulletin titles are the real
ones -- they are why `normalize` exists.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.event import Kind, Severity
from app.sources.geonet_volcano import GeonetVolcanoSource, normalize

VAL = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [175.896, -38.784]},
            "properties": {
                "acc": "Green",
                "activity": "No volcanic unrest.",
                "hazards": "Volcanic environment hazards.",
                "level": 0,
                "volcanoID": "taupo",
                "volcanoTitle": "Taupo",
            },
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-177.914, -29.254]},
            "properties": {
                "acc": "Green",
                "activity": "No volcanic unrest.",
                "hazards": "Volcanic environment hazards.",
                "level": 0,
                "volcanoID": "kermadecislands",
                "volcanoTitle": "Kermadec Islands",
            },
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [177.183, -37.521]},
            "properties": {
                "acc": "Yellow",
                "activity": "Moderate to heightened volcanic unrest.",
                "hazards": "Volcanic unrest hazards, potential for eruption hazards.",
                "level": 2,
                "volcanoID": "whiteisland",
                "volcanoTitle": "White Island",
            },
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [175.563, -39.281]},
            "properties": {
                "acc": "Green",
                "activity": "Minor volcanic unrest.",
                "hazards": "Volcanic unrest hazards.",
                "level": 1,
                "volcanoID": "ruapehu",
                "volcanoTitle": "Ruapehu",
            },
        },
    ],
}

BULLETINS = [
    {
        "title": "Decrease in volcanic tremor at Ruapehu. Volcanic Alert Level remains at Level 1.",
        "type": "vab",
        "tag": "Volcanic Activity Bulletin",
        "val": 1,
        "published": "2026-08-10T23:51:00Z",
        "link": "https://www.geonet.org.nz/vabs/6hzysePuYJs24GMgLR1h3v",
        "mlink": "https://www.geonet.org.nz/vabs/6hzysePuYJs24GMgLR1h3v",
    },
    {
        "title": (
            "Steam and gas emissions continue at Whakaari/White Island. "
            "Volcanic Alert Level remains at 2. "
        ),
        "type": "vab",
        "tag": "Volcanic Activity Bulletin",
        "val": 2,
        "published": "2026-06-22T22:30:00Z",
        "link": "https://www.geonet.org.nz/vabs/ZIeHnseGWfe2KsdnmnaFP",
        "mlink": "https://www.geonet.org.nz/vabs/ZIeHnseGWfe2KsdnmnaFP",
    },
    {
        "title": "New GeoNet website coming soon!",
        "type": "newsStory",
        "tag": "News",
        "val": 0,
        "published": "2026-08-19T22:57:00Z",
        "link": "https://www.geonet.org.nz/news/1dhPQFN8G8qAIJdvD7sWMq",
        "mlink": "https://www.geonet.org.nz/news/1dhPQFN8G8qAIJdvD7sWMq",
    },
]

# Real titles carrying U+200B around the volcano name.
BULLETIN_ZERO_WIDTH = {
    "title": (
        "No evidence of further ash emissions at ​Whakaari/White Island​. "
        "Volcanic Alert Level lowered to 2 and Aviation Colour Code to Yellow. "
    ),
    "type": "vab",
    "tag": "Volcanic Activity Bulletin",
    "val": 2,
    "published": "2026-03-17T02:00:00Z",
    "link": "https://www.geonet.org.nz/vabs/5CvhbpWWERMfDmBmGgcXQV",
    "mlink": "https://www.geonet.org.nz/vabs/5CvhbpWWERMfDmBmGgcXQV",
}

# A real title that names the volcano ONLY as "Whakaari", never "White Island".
BULLETIN_WHAKAARI_ONLY = {
    "title": (
        "Observation flight shows no ash in the steam plume from Whakaari. "
        "Volcanic Alert Level remains at 2"
    ),
    "type": "vab",
    "tag": "Volcanic Activity Bulletin",
    "val": 2,
    "published": "2025-09-08T02:00:00Z",
    "link": "https://www.geonet.org.nz/vabs/whakaari-only",
    "mlink": "https://www.geonet.org.nz/vabs/whakaari-only",
}


def dated(bulletins=BULLETINS, **kwargs) -> GeonetVolcanoSource:
    """A source with its bulletin index already filled, as one poll would."""
    source = GeonetVolcanoSource(**kwargs)
    source._titles = {
        str(f["properties"]["volcanoID"]): str(f["properties"]["volcanoTitle"])
        for f in VAL["features"]
    }
    index: dict = {}
    for entry in bulletins:
        if entry.get("type") != "vab":
            continue
        text = normalize(entry["title"])
        for volcano_id, names in source._alias_map().items():
            if any(name in text for name in names):
                published = datetime.fromisoformat(entry["published"].replace("Z", "+00:00"))
                previous = index.get(volcano_id)
                if previous is None or published > previous[0]:
                    index[volcano_id] = (published, entry["val"], entry["title"], entry["link"])
    source._bulletins = index
    return source


def test_level_zero_is_not_an_alert():
    """Ten of the twelve New Zealand volcanoes sit at "No volcanic unrest"
    permanently. Publishing them would be ten "nothing is happening" markers
    on an emergency map."""
    events = dated().parse_payload(VAL)
    assert {e.source_id for e in events} == {"whiteisland", "ruapehu"}
    # and it stays a choice, not a hard rule
    assert len(dated(min_level=0).parse_payload(VAL)) == 4


def test_the_key_is_the_volcano_not_the_bulletin():
    """The HANS trap this product already paid for once: bulletins stacking up
    as separate markers."""
    source = dated()
    first = source.parse_payload(VAL)
    second = source.parse_payload(VAL)
    assert [e.id for e in first] == [e.id for e in second]
    assert sorted(e.id for e in first) == [
        "geonet-volcano:ruapehu",
        "geonet-volcano:whiteisland",
    ]


def test_the_level_drives_the_severity():
    events = {e.source_id: e for e in dated().parse_payload(VAL)}
    assert events["ruapehu"].severity is Severity.MINOR  # level 1, minor unrest
    assert events["whiteisland"].severity is Severity.MODERATE  # level 2
    assert events["whiteisland"].kind is Kind.VOLCANO
    assert events["whiteisland"].country == "New Zealand"
    assert events["whiteisland"].ongoing is True
    # GeoNet gives [lon, lat], like every GeoJSON
    assert (events["whiteisland"].lat, events["whiteisland"].lon) == (-37.521, 177.183)
    # the aviation colour code is a separate axis from the alert level
    assert events["whiteisland"].alert == "yellow"
    assert events["ruapehu"].alert == "green"


def test_the_level_is_dated_by_its_bulletin_not_by_the_clock():
    """VAL carries no timestamp at all. Dating it "now" would make a level
    standing since June flash as breaking on every cold start (lesson 4)."""
    events = {e.source_id: e for e in dated().parse_payload(VAL)}
    whakaari = events["whiteisland"]
    assert whakaari.time == datetime(2026, 6, 22, 22, 30, tzinfo=UTC)
    assert whakaari.raw["dated_by"] == "bulletin"
    assert whakaari.title.startswith("Steam and gas emissions continue")
    assert whakaari.url == "https://www.geonet.org.nz/vabs/ZIeHnseGWfe2KsdnmnaFP"
    assert events["ruapehu"].time == datetime(2026, 8, 10, 23, 51, tzinfo=UTC)


def test_a_fresh_escalation_is_never_dated_by_a_stale_bulletin():
    """The case that matters most. A volcano that has just jumped to level 4
    has no bulletin yet; borrowing the level-2 one from June would date the
    eruption two months ago and it would never read as breaking."""
    erupting = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [177.183, -37.521]},
                "properties": {
                    "acc": "Red",
                    "activity": "Moderate to heightened volcanic unrest.",
                    "hazards": "Eruption hazards.",
                    "level": 4,
                    "volcanoID": "whiteisland",
                    "volcanoTitle": "White Island",
                },
            }
        ],
    }
    (event,) = dated().parse_payload(erupting)
    assert event.severity is Severity.EXTREME
    assert event.raw["dated_by"] == "first_seen"
    assert datetime.now(UTC) - event.time < timedelta(seconds=30)
    # and the stale June bulletin is nowhere near it
    assert event.time.month != 6
    assert event.title == "White Island -- volcanic alert level 4"


def test_the_first_seen_date_is_stable_across_polls():
    """Otherwise every poll would restamp the event and the age would reset."""
    source = dated(bulletins=[])
    first = source.parse_payload(VAL)
    second = source.parse_payload(VAL)
    assert [e.time for e in first] == [e.time for e in second]
    assert all(e.raw["dated_by"] == "first_seen" for e in first)


def test_zero_width_spaces_do_not_hide_the_volcano():
    """Two of the 37 real bulletins wrap the name in U+200B."""
    assert "​" in BULLETIN_ZERO_WIDTH["title"]
    assert "whakaari/white island" in normalize(BULLETIN_ZERO_WIDTH["title"])
    source = dated(bulletins=[BULLETIN_ZERO_WIDTH])
    events = {e.source_id: e for e in source.parse_payload(VAL)}
    assert events["whiteisland"].time == datetime(2026, 3, 17, 2, 0, tzinfo=UTC)


def test_a_bulletin_naming_only_whakaari_still_matches_white_island():
    """VAL calls it "White Island"; several bulletins say only "Whakaari".
    Matching on `volcanoTitle` alone would have missed them."""
    source = dated(bulletins=[BULLETIN_WHAKAARI_ONLY])
    events = {e.source_id: e for e in source.parse_payload(VAL)}
    assert events["whiteisland"].time == datetime(2025, 9, 8, 2, 0, tzinfo=UTC)
    assert events["whiteisland"].raw["dated_by"] == "bulletin"


def test_a_news_story_is_not_a_volcanic_bulletin():
    source = dated()
    assert "news" not in str(source._bulletins)


def test_junk_never_takes_the_batch_down():
    source = dated()
    assert source.parse_payload({}) == []
    assert source.parse_payload({"features": [{"properties": {"volcanoID": "x"}}]}) == []
    # a level that is not an integer says nothing and must not be ranked
    assert source.parse_payload({"features": [{"properties": {"level": "high"}}]}) == []
