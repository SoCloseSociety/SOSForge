"""Country resolution -> ISO 3166-1 alpha-2 code, to display a flag.

Six of the ten sources already give a country name. The seventh, USGS, only
gives a place text ("7 km WSW of Anza, CA", "84 km NE of Ruteng, Indonesia").
Over a full week of USGS feed (2110 events), that text produces only **58
distinct endings**, overwhelmingly US states: a targeted table therefore
solves the problem entirely, with no dependency and no approximate reverse
geocoding.

Guiding rule: when in doubt, NO flag. A quake in open sea ("South Sandwich
Islands region", "Banda Sea") belongs to no country, and slapping on a random
flag would be false information on an emergency product. `resolve` returns
`None` and the UI shows a globe (the flag itself is computed browser-side from
the code, it has no business here).
"""

from __future__ import annotations

import re

# States, territories and abbreviations used by USGS: all -> US, except those
# that have their own ISO code (Puerto Rico, Guam, Virgin Islands...).
US_STATES = {
    "alabama",
    "alaska",
    "arizona",
    "arkansas",
    "california",
    "colorado",
    "connecticut",
    "delaware",
    "florida",
    "hawaii",
    "idaho",
    "illinois",
    "indiana",
    "iowa",
    "kansas",
    "kentucky",
    "louisiana",
    "maine",
    "maryland",
    "massachusetts",
    "michigan",
    "minnesota",
    "mississippi",
    "missouri",
    "montana",
    "nebraska",
    "nevada",
    "new hampshire",
    "new jersey",
    "new mexico",
    "new york",
    "north carolina",
    "north dakota",
    "ohio",
    "oklahoma",
    "oregon",
    "pennsylvania",
    "rhode island",
    "south carolina",
    "south dakota",
    "tennessee",
    "texas",
    "utah",
    "vermont",
    "virginia",
    "washington",
    "west virginia",
    "wisconsin",
    "wyoming",
    # postal abbreviations seen in the feeds
    "ak",
    "al",
    "ar",
    "az",
    "ca",
    "co",
    "ct",
    "de",
    "fl",
    "ga",
    "hi",
    "ia",
    "id",
    "il",
    "in",
    "ks",
    "ky",
    "la",
    "ma",
    "md",
    "me",
    "mi",
    "mn",
    "mo",
    "ms",
    "mt",
    "nc",
    "nd",
    "ne",
    "nh",
    "nj",
    "nm",
    "nv",
    "ny",
    "oh",
    "ok",
    "or",
    "pa",
    "ri",
    "sc",
    "sd",
    "tn",
    "tx",
    "ut",
    "va",
    "vt",
    "wa",
    "wi",
    "wv",
    "wy",
}

# Labels that contain the name of a country or state WITHOUT being that place.
# Each was observed in a real feed and produced a wrong flag:
#   "GULF OF CALIFORNIA"  -> California -> United States, when these are
#                            Mexican waters (231 EMSC events over a year)
#   "NEAR EAST COAST OF NEW GUINEA" -> "guinea" -> Guinea (West Africa)
#   "LAC KIVU REGION, CONGO" -> Congo-Brazzaville, when Kivu is in the DRC
#   "SOUTH GEORGIA RISE" -> Georgia -> United States, in the middle of the
#                           Southern Ocean
AMBIGUOUS_PHRASES = {
    "new guinea": None,  # Papua or Equatorial Guinea: we do not decide
    "equatorial guinea": "GQ",
    "papua new guinea": "PG",
    "gulf of california": "MX",
    "sea of japan": None,
    "south georgia": None,
    "south georgia rise": None,
    "south georgia island": None,
    "lac kivu": "CD",
    "kivu": "CD",
}

NAME_TO_ISO2: dict[str, str] = {
    # North and Central America
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "u.s.": "US",
    "canada": "CA",
    "mexico": "MX",
    "mx": "MX",
    "b.c., mx": "MX",
    "guatemala": "GT",
    "belize": "BZ",
    "honduras": "HN",
    "el salvador": "SV",
    "nicaragua": "NI",
    "costa rica": "CR",
    "panama": "PA",
    "puerto rico": "PR",
    "u.s. virgin islands": "VI",
    "virgin islands": "VI",
    "guam": "GU",
    "northern mariana islands": "MP",
    "american samoa": "AS",
    "cuba": "CU",
    "jamaica": "JM",
    "haiti": "HT",
    "dominican republic": "DO",
    "bahamas": "BS",
    "trinidad and tobago": "TT",
    "barbados": "BB",
    "greenland": "GL",
    "bermuda": "BM",
    # South America
    "colombia": "CO",
    "venezuela": "VE",
    "ecuador": "EC",
    "peru": "PE",
    "bolivia": "BO",
    "brazil": "BR",
    "chile": "CL",
    "argentina": "AR",
    "uruguay": "UY",
    "paraguay": "PY",
    "guyana": "GY",
    "suriname": "SR",
    # Europe
    "france": "FR",
    "spain": "ES",
    "portugal": "PT",
    "italy": "IT",
    "germany": "DE",
    "austria": "AT",
    "switzerland": "CH",
    "belgium": "BE",
    "netherlands": "NL",
    "luxembourg": "LU",
    "united kingdom": "GB",
    "ireland": "IE",
    "iceland": "IS",
    "norway": "NO",
    "sweden": "SE",
    "finland": "FI",
    "denmark": "DK",
    "poland": "PL",
    "czechia": "CZ",
    "czech republic": "CZ",
    "slovakia": "SK",
    "hungary": "HU",
    "romania": "RO",
    "bulgaria": "BG",
    "greece": "GR",
    "albania": "AL",
    "north macedonia": "MK",
    "serbia": "RS",
    "montenegro": "ME",
    "croatia": "HR",
    "slovenia": "SI",
    "bosnia & herzegovina": "BA",
    "bosnia and herzegovina": "BA",
    "kosovo": "XK",
    "moldova": "MD",
    "ukraine": "UA",
    "belarus": "BY",
    "lithuania": "LT",
    "latvia": "LV",
    "estonia": "EE",
    "malta": "MT",
    "cyprus": "CY",
    "russia": "RU",
    "russian federation": "RU",
    # Africa
    "morocco": "MA",
    "algeria": "DZ",
    "tunisia": "TN",
    "libya": "LY",
    "egypt": "EG",
    "sudan": "SD",
    "south sudan": "SS",
    "eritrea": "ER",
    "ethiopia": "ET",
    "somalia": "SO",
    "djibouti": "DJ",
    "kenya": "KE",
    "uganda": "UG",
    "tanzania": "TZ",
    "rwanda": "RW",
    "burundi": "BI",
    "democratic republic of congo": "CD",
    "the democratic republic of congo": "CD",
    "democratic republic of the congo": "CD",
    # Unqualified, "Congo" in a seismic label means the East African Rift,
    # hence the DRC. Congo-Brazzaville stays reachable through its full name
    # below.
    "congo": "CD",
    "republic of congo": "CG",
    "republic of the congo": "CG",
    "congo-brazzaville": "CG",
    "georgia": "GE",
    "angola": "AO",
    "zambia": "ZM",
    "zimbabwe": "ZW",
    "malawi": "MW",
    "mozambique": "MZ",
    "madagascar": "MG",
    "namibia": "NA",
    "botswana": "BW",
    "south africa": "ZA",
    "lesotho": "LS",
    "eswatini": "SZ",
    "nigeria": "NG",
    "ghana": "GH",
    "ivory coast": "CI",
    "cote d'ivoire": "CI",
    "senegal": "SN",
    "mali": "ML",
    "niger": "NE",
    "chad": "TD",
    "burkina faso": "BF",
    "guinea": "GN",
    "sierra leone": "SL",
    "liberia": "LR",
    "cameroon": "CM",
    "central african republic": "CF",
    "gabon": "GA",
    "mauritania": "MR",
    "benin": "BJ",
    "togo": "TG",
    # West and Central Asia
    "turkey": "TR",
    "turkiye": "TR",
    "türkiye": "TR",
    "syria": "SY",
    "lebanon": "LB",
    "israel": "IL",
    "palestine": "PS",
    "jordan": "JO",
    "iraq": "IQ",
    "iran": "IR",
    "saudi arabia": "SA",
    "yemen": "YE",
    "oman": "OM",
    "united arab emirates": "AE",
    "qatar": "QA",
    "kuwait": "KW",
    "bahrain": "BH",
    "armenia": "AM",
    "azerbaijan": "AZ",
    "kazakhstan": "KZ",
    "uzbekistan": "UZ",
    "turkmenistan": "TM",
    "kyrgyzstan": "KG",
    "tajikistan": "TJ",
    "afghanistan": "AF",
    # South and East Asia
    "pakistan": "PK",
    "india": "IN",
    "nepal": "NP",
    "bhutan": "BT",
    "bangladesh": "BD",
    "sri lanka": "LK",
    "maldives": "MV",
    "china": "CN",
    "mongolia": "MN",
    "japan": "JP",
    "south korea": "KR",
    "north korea": "KP",
    "taiwan": "TW",
    "hong kong": "HK",
    "myanmar": "MM",
    "thailand": "TH",
    "laos": "LA",
    "cambodia": "KH",
    "vietnam": "VN",
    "malaysia": "MY",
    "singapore": "SG",
    "brunei": "BN",
    "indonesia": "ID",
    "philippines": "PH",
    "timor-leste": "TL",
    # Oceania
    "australia": "AU",
    "new zealand": "NZ",
    "papua new guinea": "PG",
    "fiji": "FJ",
    "vanuatu": "VU",
    "solomon islands": "SB",
    "new caledonia": "NC",
    "samoa": "WS",
    "tonga": "TO",
    "tuvalu": "TV",
    "kiribati": "KI",
    "nauru": "NR",
    "palau": "PW",
    "micronesia": "FM",
    "marshall islands": "MH",
    "french polynesia": "PF",
}

# Areas the feeds name but that belong to no country: we want an explicit
# failure rather than an approximate attachment.
RE_STRIP = re.compile(r"\s+(region|border region|area|sea|ocean|ridge|rise)$", re.I)
RE_SEPARATORS = re.compile(r"\s*[;/]\s*")


def _normalize(value: str) -> str:
    text = value.strip().lower()
    text = RE_STRIP.sub("", text).strip()
    return text.strip(" .")


def _lookup(candidate: str) -> str | None:
    key = _normalize(candidate)
    if not key:
        return None
    if key in AMBIGUOUS_PHRASES:
        return AMBIGUOUS_PHRASES[key]
    if key in NAME_TO_ISO2:
        return NAME_TO_ISO2[key]
    if key in US_STATES:
        return "US"
    return None


# Sentinel: distinguishes "not an ambiguous phrase" from "ambiguous phrase, no
# country". A boolean mixed with a country code made the signature untenable
# (mypy flagged it), and above all unreadable.
NOT_AMBIGUOUS = "?"


def _is_ambiguous(text: str) -> str | None:
    """An ambiguous phrase is settled BEFORE any suffix matching: otherwise
    "GULF OF CALIFORNIA" falls back to "california" and becomes American.

    Returns `NOT_AMBIGUOUS` if the text is not ambiguous, otherwise the
    country code (or None when the phrase is so ambiguous we refuse to
    decide).
    """
    lowered = _normalize(text)
    for phrase, iso2 in AMBIGUOUS_PHRASES.items():
        if phrase in lowered:
            return iso2
    return NOT_AMBIGUOUS


def resolve(country: str | None, place: str | None = None) -> str | None:
    """Returns an ISO2 code, or None if we cannot honestly conclude."""
    if country:
        # GDACS sometimes lists several countries: "Kenya, Somalia, Ethiopia".
        # The first is enough to show a representative flag.
        for chunk in RE_SEPARATORS.split(country):
            for part in chunk.split(","):
                found = _lookup(part)
                if found:
                    return found

    if not place:
        return None

    verdict = _is_ambiguous(place)
    if verdict != NOT_AMBIGUOUS:
        return verdict

    # USGS and EMSC labels end with the region: "..., CA", "..., Indonesia",
    # "FLORES REGION, INDONESIA". ONLY that last segment is looked up:
    # searching further made "21 km NNW of T'q'ibuli, Georgia" resolve to the
    # US state of Georgia.
    if "," in place:
        return _lookup(place.rsplit(",", 1)[1])

    # Some labels have no comma: "Fiji region", "WESTERN TEXAS", "Banda Sea".
    # We try the full label, then its shorter and shorter suffixes ("western
    # texas" -> "texas"), which covers EMSC's Flynn regions. "Banda Sea" stays
    # unanswered: it is a sea, and that is the right answer.
    found = _lookup(place)
    if found:
        return found
    words = _normalize(place).split()
    for start in range(1, len(words)):
        found = _lookup(" ".join(words[start:]))
        if found:
            return found
    return None
