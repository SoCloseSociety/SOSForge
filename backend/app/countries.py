"""Resolution du pays -> code ISO 3166-1 alpha-2, pour afficher un drapeau.

Six des dix sources donnent deja un nom de pays. La septieme, l'USGS, ne donne
qu'un texte de lieu ("7 km WSW of Anza, CA", "84 km NE of Ruteng, Indonesia").
Sur une semaine entiere de flux USGS (2110 evenements), ce texte ne produit que
**58 terminaisons distinctes**, tres majoritairement des Etats americains: une
table ciblee resout donc le probleme entierement, sans dependance ni geocodage
inverse approximatif.

Regle de conduite: en cas de doute, PAS de drapeau. Un seisme en pleine mer
("South Sandwich Islands region", "Banda Sea") n'appartient a aucun pays, et
coller un drapeau au hasard serait une information fausse sur un produit
d'urgence. `resolve` rend `None` et l'interface affiche un globe.
"""

from __future__ import annotations

import re

# Etats, territoires et abreviations utilises par l'USGS: tous -> US, sauf ceux
# qui ont leur propre code ISO (Porto Rico, Guam, iles Vierges...).
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
    # abreviations postales vues dans les flux
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

# Libelles qui contiennent le nom d'un pays ou d'un Etat SANS etre ce lieu.
# Chacun a ete observe dans un flux reel et produisait un faux drapeau:
#   "GULF OF CALIFORNIA"  -> Californie -> Etats-Unis, alors que ce sont des
#                            eaux mexicaines (231 evenements EMSC sur un an)
#   "NEAR EAST COAST OF NEW GUINEA" -> "guinea" -> Guinee (Afrique de l'Ouest)
#   "LAC KIVU REGION, CONGO" -> Congo-Brazzaville, alors que le Kivu est en RDC
#   "SOUTH GEORGIA RISE" -> Georgie -> Etats-Unis, en plein ocean Austral
AMBIGUOUS_PHRASES = {
    "new guinea": None,  # Papouasie ou Guinee equatoriale: on ne tranche pas
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
    # Amerique du Nord et centrale
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
    # Amerique du Sud
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
    # Afrique
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
    # Sans precision, "Congo" dans un libelle sismique designe le Rift
    # est-africain, donc la RDC. Le Congo-Brazzaville reste joignable par son
    # nom complet ci-dessous.
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
    # Asie de l'Ouest et centrale
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
    # Asie du Sud et de l'Est
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
    # Oceanie
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

# Zones que les flux nomment mais qui n'appartiennent a aucun pays: on veut un
# echec explicite plutot qu'un rattachement approximatif.
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


def _is_ambiguous(text: str) -> str | None | bool:
    """Une phrase ambigue est tranchee AVANT tout matching par suffixe: sinon
    "GULF OF CALIFORNIA" retombe sur "california" et devient americain.
    Rend False si le texte n'est pas ambigu, sinon le code (ou None)."""
    lowered = _normalize(text)
    for phrase, iso2 in AMBIGUOUS_PHRASES.items():
        if phrase in lowered:
            return iso2
    return False


def resolve(country: str | None, place: str | None = None) -> str | None:
    """Rend un code ISO2, ou None si on ne peut pas conclure honnetement."""
    if country:
        # GDACS liste parfois plusieurs pays: "Kenya, Somalia, Ethiopia".
        # Le premier suffit a poser un drapeau representatif.
        for chunk in RE_SEPARATORS.split(country):
            for part in chunk.split(","):
                found = _lookup(part)
                if found:
                    return found

    if not place:
        return None

    verdict = _is_ambiguous(place)
    if verdict is not False:
        return verdict

    # Les libelles USGS et EMSC finissent par la region: "..., CA",
    # "..., Indonesia", "FLORES REGION, INDONESIA". SEUL ce dernier segment est
    # interrogé: chercher plus loin faisait dire "Georgie" a
    # "21 km NNW of T'q'ibuli, Georgia" pour l'Etat americain.
    if "," in place:
        return _lookup(place.rsplit(",", 1)[1])

    # Certains libelles n'ont pas de virgule: "Fiji region", "WESTERN TEXAS",
    # "Banda Sea". On essaie le libelle entier, puis ses suffixes de plus en plus
    # courts ("western texas" -> "texas"), ce qui couvre les regions Flynn de
    # l'EMSC. "Banda Sea" reste sans reponse: c'est une mer, tant mieux.
    found = _lookup(place)
    if found:
        return found
    words = _normalize(place).split()
    for start in range(1, len(words)):
        found = _lookup(" ".join(words[start:]))
        if found:
            return found
    return None


def flag_emoji(iso2: str | None) -> str | None:
    """Le drapeau est calcule, pas stocke: deux indicateurs regionaux Unicode."""
    if not iso2 or len(iso2) != 2 or not iso2.isalpha():
        return None
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in iso2.upper())
