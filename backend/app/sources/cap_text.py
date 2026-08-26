"""Read what a CAP document tells people to DO.

`cap_area.py` already downloads these documents, for their `<area><polygon>`.
The instruction is in the same file, a few elements away, and was going
straight into the bin with the rest of the document. This module is the other
half of that fetch: same bytes, no extra request, no extra load on the
agency.

Measured on 150 CAP documents drawn at random from the WMO aggregate's Severe
and Extreme alerts on 2026-08-26 (473 such alerts that day):

| field          | present |
|----------------|---------|
| `headline`     | 150/150 (100%)  -- already read elsewhere |
| `description`  | 114/150 (76.0%) |
| `instruction`  |  59/150 (39.3%) |
| `responseType` |  32/150 (21.3%) |

Two thirds of those documents carry a single `<info>` block; 65 of 150 carry
two and 2 carry three. The extra blocks are the SAME alert in another
language (`zh-CN` 49, `es-AR` 8, `it-IT` 6, `fr-FR` 4 in that sample), which
is why an English block is chosen when there is one rather than the first
that goes past: the first is frequently the local one, and a French
instruction under an English headline reads like a bug.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass

log = logging.getLogger(__name__)


def _tag(element: ET.Element) -> str:
    """Local name. The CAP version lives in the namespace, so matching on the
    full tag would break the day a producer moves from 1.2 to 1.3."""
    return element.tag.rsplit("}", 1)[-1]


@dataclass(frozen=True)
class CapText:
    """What one CAP document says, in the fields the model has room for."""

    instruction: str | None = None
    description: str | None = None
    response_type: str | None = None
    headline: str | None = None

    def __bool__(self) -> bool:
        return any((self.instruction, self.description, self.response_type, self.headline))


def _child_text(info: ET.Element, name: str) -> str | None:
    for child in info:
        if _tag(child) == name and (text := (child.text or "").strip()):
            return text
    return None


def _response_type(info: ET.Element) -> str | None:
    """CAP allows several `<responseType>` elements on one `<info>`.

    `AllClear` is the one that changes what the alert MEANS -- it says the
    warning has been lifted -- so it wins over any other value present, and
    `None` (a literal CAP value, not an absence) only wins if it is alone.
    Anything else, first one wins.
    """
    values = [(child.text or "").strip() for child in info if _tag(child) == "responseType"]
    values = [v for v in values if v]
    if not values:
        return None
    for value in values:
        if value.lower() == "allclear":
            return value
    for value in values:
        if value.lower() != "none":
            return value
    return values[0]


def pick_info(root: ET.Element) -> ET.Element | None:
    """The English `<info>` when the document has one, else the first.

    Same choice `alerts_world.py` makes on the Meteoalarm JSON, for the same
    reason: one alert must yield one event, and the language blocks are
    duplicates, not different alerts.
    """
    infos = [element for element in root.iter() if _tag(element) == "info"]
    if not infos:
        return None
    for info in infos:
        language = _child_text(info, "language") or ""
        if language.lower().startswith("en"):
            return info
    return infos[0]


def parse_cap_text(document: str | bytes) -> CapText:
    """Everything actionable in one CAP document, or an empty `CapText`.

    Never raises: this runs inside a polling loop over documents published by
    a hundred different agencies, and one malformed file must cost that file
    only. An unparseable document is indistinguishable here from one that
    says nothing, and both answers are "we have no instruction", which is the
    truth in either case.
    """
    try:
        root = ET.fromstring(document)
    except ET.ParseError as exc:
        log.debug("cap: unparseable document (%s)", exc)
        return CapText()

    info = pick_info(root)
    if info is None:
        return CapText()

    return CapText(
        instruction=_child_text(info, "instruction"),
        description=_child_text(info, "description"),
        response_type=_response_type(info),
        headline=_child_text(info, "headline"),
    )
