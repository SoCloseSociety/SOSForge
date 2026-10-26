"""Zone scope: parsing is strict, matching is a great-circle distance, and
what cannot be placed is counted instead of silently dropped."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.event import Event, Kind, Severity, utcnow
from app.scope import (
    MAX_ZONES,
    Scope,
    ScopeError,
    Zone,
    compose,
    haversine_km,
    parse_point_scope,
    parse_scope,
)

TOKYO = (35.6762, 139.6503)
SAPPORO = (43.0618, 141.3545)  # ~830 km from Tokyo
YOKOHAMA = (35.4437, 139.6380)  # ~26 km from Tokyo


def event(
    sid: str,
    lat: float | None,
    lon: float | None,
    *,
    severity: Severity = Severity.MODERATE,
    kind: Kind = Kind.STORM,
    minutes_ago: float = 5,
    country: str | None = None,
    **extra,
) -> Event:
    return Event(
        id=f"test:{sid}",
        source="test",
        source_id=sid,
        kind=kind,
        time=utcnow() - timedelta(minutes=minutes_ago),
        lat=lat,
        lon=lon,
        severity=severity,
        country_code=country,
        place=f"place {sid}",
        **extra,
    )


class TestParsing:
    def test_no_parameter_is_the_legacy_client(self):
        assert parse_scope({}) is None
        assert parse_scope({"zones": None, "kinds": None}) is None

    def test_a_valid_scope_is_echoed_back_as_understood(self):
        scope = parse_scope(
            {
                "zones": "35.68,139.65,300;34.05,-118.24,50",
                "min_severity": "SEVERE",
                "kinds": "earthquake, tsunami",
            }
        )
        assert scope is not None
        assert scope.public() == {
            "zones": [
                {"lat": 35.68, "lon": 139.65, "radius_km": 300.0},
                {"lat": 34.05, "lon": -118.24, "radius_km": 50.0},
            ],
            "min_severity": "severe",
            "kinds": ["earthquake", "tsunami"],
            "unplaced": "drop",
        }

    @pytest.mark.parametrize(
        ("params", "code"),
        [
            ({"zones": "91,0,10"}, "bad_zones"),
            ({"zones": "0,181,10"}, "bad_zones"),
            ({"zones": "0,0,0"}, "bad_zones"),
            ({"zones": "0,0,2001"}, "bad_zones"),
            ({"zones": "0,0"}, "bad_zones"),
            ({"zones": "nan,0,10"}, "bad_zones"),
            ({"zones": "inf,0,10"}, "bad_zones"),
            ({"zones": "a,b,c"}, "bad_zones"),
            ({"zones": ""}, "bad_zones"),
            ({"zones": ";".join(["0,0,10"] * (MAX_ZONES + 1))}, "bad_zones"),
            ({"min_severity": "critical"}, "bad_min_severity"),
            ({"kinds": "quake"}, "bad_kinds"),
            ({"kinds": ","}, "bad_kinds"),
            ({"zones": "0,0,10", "unplaced": "France"}, "bad_unplaced"),
            ({"zones": "0,0,10", "unplaced": ",".join(["FR"] * 11)}, "bad_unplaced"),
            ({"unplaced": "FR"}, "bad_unplaced"),
        ],
    )
    def test_a_bad_parameter_is_refused_with_a_reason(self, params, code):
        with pytest.raises(ScopeError) as err:
            parse_scope(params)
        assert err.value.code == code
        assert err.value.reason

    def test_the_point_triple_is_all_or_nothing(self):
        assert parse_point_scope(None, None, None) is None
        assert parse_point_scope(1.0, 2.0, 3.0) == "1.0,2.0,3.0"
        with pytest.raises(ScopeError):
            parse_point_scope(1.0, None, 3.0)

    def test_unplaced_accepts_drop_all_or_countries(self):
        assert parse_scope({"zones": "0,0,10", "unplaced": "all"}).unplaced == "all"
        scope = parse_scope({"zones": "0,0,10", "unplaced": "fr,de"})
        assert scope.unplaced == "country"
        assert scope.countries == frozenset({"FR", "DE"})
        assert scope.public()["unplaced"] == "DE,FR"


class TestMatching:
    def test_great_circle_distance_is_right_at_city_scale(self):
        # Paris - London, 343.5 km on the WGS84 mean sphere
        assert haversine_km(48.8566, 2.3522, 51.5074, -0.1278) == pytest.approx(343.5, abs=1)

    def test_a_zone_matches_by_distance(self):
        scope = Scope(zones=(Zone(*TOKYO, 300),))
        assert scope.matches(event("y", *YOKOHAMA))
        assert not scope.matches(event("s", *SAPPORO))

    def test_any_zone_is_enough(self):
        scope = Scope(zones=(Zone(*TOKYO, 50), Zone(*SAPPORO, 50)))
        assert scope.matches(event("s", *SAPPORO))

    def test_across_the_antimeridian(self):
        # Fiji (178.4E) and Samoa-side (-179.9) are 200 km apart, not 40000
        scope = Scope(zones=(Zone(-17.7, 178.4, 300),))
        assert scope.matches(event("f", -17.0, -179.9))

    def test_severity_floor_and_kinds(self):
        scope = Scope(min_severity=3, kinds=frozenset({"earthquake"}))
        assert not scope.matches(
            event("a", *TOKYO, severity=Severity.MODERATE, kind=Kind.EARTHQUAKE)
        )
        assert not scope.matches(event("b", *TOKYO, severity=Severity.SEVERE, kind=Kind.STORM))
        assert scope.matches(event("c", *TOKYO, severity=Severity.EXTREME, kind=Kind.EARTHQUAKE))

    def test_without_zones_an_unplaced_event_is_simply_in_scope(self):
        scope = Scope(min_severity=0)
        assert scope.matches(event("n", None, None))

    def test_unplaced_events_are_dropped_and_counted_by_default(self):
        scope = Scope(zones=(Zone(*TOKYO, 300),))
        kept, unplaced = scope.apply(
            [event("in", *YOKOHAMA), event("far", *SAPPORO), event("none", None, None)]
        )
        assert [e.id for e in kept] == ["test:in"]
        assert unplaced.public() == {"dropped": 1, "delivered": 0}

    def test_unplaced_by_country_is_the_clients_explicit_choice(self):
        scope = Scope(
            zones=(Zone(48.85, 2.35, 300),), unplaced="country", countries=frozenset({"FR"})
        )
        kept, unplaced = scope.apply(
            [
                event("fr", None, None, country="FR"),
                event("de", None, None, country="DE"),
                event("x", None, None),
            ]
        )
        assert [e.id for e in kept] == ["test:fr"]
        assert unplaced.public() == {"dropped": 2, "delivered": 1}

    def test_an_unplaced_event_below_the_floor_is_not_counted(self):
        scope = Scope(zones=(Zone(*TOKYO, 300),), min_severity=3)
        _, unplaced = scope.apply([event("n", None, None, severity=Severity.MINOR)])
        assert unplaced.dropped == 0

    def test_public_dict_and_model_give_the_same_answer(self):
        scope = Scope(
            zones=(Zone(*TOKYO, 300),),
            min_severity=2,
            unplaced="country",
            countries=frozenset({"JP"}),
        )
        for e in (
            event("a", *YOKOHAMA),
            event("b", *SAPPORO),
            event("c", None, None, country="JP"),
            event("d", None, None, country="US"),
            event("e", *YOKOHAMA, severity=Severity.MINOR),
        ):
            assert scope.matches(e) == scope.matches_public(e.public()), e.id


class TestCompose:
    """The snapshot cut reached back only 8.3 h on production: 49 of 70
    severe/extreme/tsunami events and 264 of 346 ongoing alerts never reached
    a fresh tab."""

    def newest_first(self, events):
        return sorted(events, key=lambda e: e.time, reverse=True)

    def test_the_latest_n_plus_every_older_pinned_event(self):
        recent = [event(f"r{i}", 0, i, minutes_ago=i, severity=Severity.MINOR) for i in range(5)]
        old_severe = event("red", 0, 50, minutes_ago=19 * 60, severity=Severity.SEVERE)
        old_ongoing = event(
            "wind", 0, 51, minutes_ago=20 * 60, severity=Severity.MINOR, ongoing=True
        )
        old_tsunami = event("ts", 0, 52, minutes_ago=21 * 60, severity=Severity.INFO, tsunami=True)
        old_minor = event("old", 0, 53, minutes_ago=22 * 60, severity=Severity.MINOR)
        out, _ = compose(
            self.newest_first([*recent, old_severe, old_ongoing, old_tsunami, old_minor]), 2, None
        )
        assert [e.id for e in out] == ["test:r0", "test:r1", "test:red", "test:wind", "test:ts"]

    def test_the_zone_filter_runs_before_the_size_cut(self):
        far = [event(f"far{i}", *SAPPORO, minutes_ago=i) for i in range(5)]
        near = event("near", *YOKOHAMA, minutes_ago=60, severity=Severity.MINOR)
        scope = Scope(zones=(Zone(*TOKYO, 300),))
        out, _ = compose(self.newest_first([*far, near]), 2, scope)
        assert [e.id for e in out] == ["test:near"]

    def test_a_pinned_event_out_of_the_zone_stays_out(self):
        scope = Scope(zones=(Zone(*TOKYO, 300),))
        out, _ = compose([event("red", *SAPPORO, severity=Severity.EXTREME)], 0, scope)
        assert out == []


class TestSplit:
    def test_nothing_past_the_first_message(self):
        from app.scope import split

        head, chunks = split([{"id": i} for i in range(3)], 5, 2, 10_000)
        assert [e["id"] for e in head] == [0, 1, 2] and chunks == []

    def test_order_is_kept_and_both_bounds_apply(self):
        from app.scope import split

        events = [{"id": i, "pad": "x" * (500 if i == 4 else 10)} for i in range(9)]
        head, chunks = split(events, 2, 3, 200)
        assert [e["id"] for e in head] == [0, 1]
        assert [[e["id"] for e in c] for c in chunks] == [[2, 3], [4], [5, 6, 7], [8]]
        flat = head + [e for c in chunks for e in c]
        assert flat == events
