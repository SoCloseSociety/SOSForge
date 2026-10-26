"""What to do before, during and after each kind of hazard.

Safety-critical text. Every line is a paraphrase of an official page, and the
data file keeps, next to each line, the page it paraphrases (`source`) and the
verbatim excerpt of that page it was written from (`quote`), so a reviewer
can check one against the other without hunting for it. Nothing here is
generated at runtime: the catalogue is a reviewed file, loaded once.

A line is `needs_review` when the file says so (the paraphrase or the phase
placement is a judgement call, explained in its `note`) OR when its source is
not in the language of the line -- a translation that no official page in
that language backs. The second rule is computed here, not trusted from the
file, so the flag cannot be forgotten by whoever edits it.

Loaded lazily and only by `/api/guidance`: a broken catalogue must cost that
one endpoint, never the live feed.
"""

from __future__ import annotations

import json
from enum import Enum
from functools import cache
from pathlib import Path
from typing import Any

from app.models.event import Kind

DATA = Path(__file__).parent / "data" / "guidance.json"
PHASES = ("before", "during", "after")


class Lang(str, Enum):
    EN = "en"
    FR = "fr"
    ES = "es"
    JA = "ja"
    ID = "id"


class GuidanceError(ValueError):
    pass


class Catalogue:
    def __init__(self, raw: dict[str, Any]):
        self.version = int(raw.get("version") or 0)
        languages = raw.get("languages") or {}
        self._public: dict[str, dict[str, Any]] = {}
        for lang in Lang:
            kinds = languages.get(lang.value)
            if not isinstance(kinds, dict):
                raise GuidanceError(f"guidance: no catalogue for {lang.value}")
            self._public[lang.value] = self._build(lang.value, kinds)

    @staticmethod
    def _build(lang: str, kinds: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, dict[str, list[dict[str, Any]]]] = {}
        flagged = total = 0
        for kind in Kind:
            phases = kinds.get(kind.value)
            if not isinstance(phases, dict):
                raise GuidanceError(f"guidance: {lang} has no {kind.value}")
            out[kind.value] = {}
            for phase in PHASES:
                lines = phases.get(phase)
                if not lines:
                    raise GuidanceError(f"guidance: {lang}/{kind.value}/{phase} is empty")
                public = []
                for line in lines:
                    source = line.get("source") or {}
                    text = str(line.get("text") or "").strip()
                    url = str(source.get("url") or "")
                    if not text or not url.startswith("https://") or not source.get("lang"):
                        raise GuidanceError(
                            f"guidance: {lang}/{kind.value}/{phase}: a line without "
                            "text, https source or source language"
                        )
                    review = bool(line.get("needs_review")) or source["lang"] != lang
                    flagged += review
                    total += 1
                    public.append(
                        {
                            "text": text,
                            "source": {
                                "name": str(source.get("name") or ""),
                                "url": url,
                                "lang": str(source["lang"]),
                            },
                            "needs_review": review,
                        }
                    )
                out[kind.value][phase] = public
        return {"lines": total, "needs_review": flagged, "kinds": out}

    def for_language(self, lang: Lang) -> dict[str, Any]:
        body = self._public[lang.value]
        return {
            "lang": lang.value,
            "version": self.version,
            "phases": list(PHASES),
            "lines": body["lines"],
            "needs_review": body["needs_review"],
            "kinds": body["kinds"],
        }


def load(path: Path = DATA) -> Catalogue:
    return Catalogue(json.loads(path.read_text(encoding="utf-8")))


@cache
def catalogue() -> Catalogue:
    return load()
