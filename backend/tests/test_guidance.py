"""The prevention catalogue: complete for every kind x language x phase, every
line sourced, and translations without an official source in their language
flagged for human review."""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from app.guidance import DATA, PHASES, Catalogue, GuidanceError, Lang, load
from app.models.event import Kind

RAW = json.loads(DATA.read_text(encoding="utf-8"))
# English is the reference: 12 words. The others run longer for the same
# sentence; Japanese has no spaces, so it is bounded in characters.
MAX_WORDS = {"en": 12, "fr": 16, "es": 16, "id": 16}
MAX_JA_CHARS = 40


def lines(lang: str):
    for kind, phases in RAW["languages"][lang].items():
        for phase, entries in phases.items():
            for entry in entries:
                yield kind, phase, entry


@pytest.mark.parametrize("lang", [lang.value for lang in Lang])
def test_every_kind_and_phase_is_covered(lang):
    catalogue = RAW["languages"][lang]
    for kind in Kind:
        for phase in PHASES:
            assert catalogue[kind.value][phase], f"{lang}/{kind.value}/{phase} is empty"
            assert len(catalogue[kind.value][phase]) <= 5


@pytest.mark.parametrize("lang", [lang.value for lang in Lang])
def test_every_line_carries_its_official_source_and_the_excerpt_it_paraphrases(lang):
    for kind, phase, entry in lines(lang):
        where = f"{lang}/{kind}/{phase}: {entry['text']}"
        assert entry["source"]["url"].startswith("https://"), where
        assert entry["source"]["name"].strip(), where
        assert entry["source"]["lang"] in {lang.value for lang in Lang}, where
        assert entry["quote"].strip(), where
        if entry["needs_review"]:
            assert entry.get("note"), f"flagged without saying why: {where}"


@pytest.mark.parametrize("lang", ["en", "fr", "es", "id"])
def test_lines_are_short(lang):
    for kind, phase, entry in lines(lang):
        words = entry["text"].split()
        assert len(words) <= MAX_WORDS[lang], f"{lang}/{kind}/{phase}: {entry['text']}"


def test_japanese_lines_are_short():
    for kind, phase, entry in lines("ja"):
        assert len(entry["text"]) <= MAX_JA_CHARS, f"ja/{kind}/{phase}: {entry['text']}"


def test_no_em_dash_anywhere():
    assert chr(0x2014) not in DATA.read_text(encoding="utf-8")


def test_each_line_is_one_sentence_ending_like_one():
    for lang in RAW["languages"]:
        for kind, phase, entry in lines(lang):
            assert re.search(r"[.。]$", entry["text"]), f"{lang}/{kind}/{phase}: {entry['text']}"


def test_a_translation_without_a_source_in_its_language_is_flagged():
    raw = json.loads(json.dumps(RAW))
    entry = raw["languages"]["fr"]["earthquake"]["before"][0]
    entry["source"]["lang"] = "en"
    entry["needs_review"] = False
    body = Catalogue(raw).for_language(Lang.FR)
    assert body["kinds"]["earthquake"]["before"][0]["needs_review"] is True


def test_a_hole_in_the_catalogue_fails_loudly():
    raw = json.loads(json.dumps(RAW))
    raw["languages"]["ja"]["tsunami"]["during"] = []
    with pytest.raises(GuidanceError):
        Catalogue(raw)


def test_the_shipped_file_loads():
    catalogue = load()
    for lang in Lang:
        body = catalogue.for_language(lang)
        assert body["lines"] > 100
        flagged = sum(
            line["needs_review"]
            for phases in body["kinds"].values()
            for entries in phases.values()
            for line in entries
        )
        assert body["needs_review"] == flagged


class TestEndpoint:
    @pytest.fixture
    def client(self):
        from app.main import app

        return TestClient(app)

    def test_default_is_english(self, client):
        body = client.get("/api/guidance").json()
        assert body["lang"] == "en"
        line = body["kinds"]["earthquake"]["during"][0]
        assert set(line) == {"text", "source", "needs_review"}
        assert set(line["source"]) == {"name", "url", "lang"}

    @pytest.mark.parametrize("lang", ["fr", "es", "ja", "id"])
    def test_each_language(self, client, lang):
        body = client.get(f"/api/guidance?lang={lang}").json()
        assert body["lang"] == lang
        assert set(body["kinds"]) == {k.value for k in Kind}

    def test_an_unknown_language_is_refused(self, client):
        assert client.get("/api/guidance?lang=de").status_code == 422
