"""`error.message`: one sentence in the reader's language, shown as is by the
web and Android clients. `error.reason` stays the raw English text for
operators. Five languages, and a test checks every code has all five.

A sentence may use the block's own parameters (`{km}`, `{hours}`); a
parameter that was not given leaves the generic sentence of the code."""

from __future__ import annotations

from typing import Any

LANGS = ("en", "fr", "es", "ja", "id")

# code -> lang -> sentence. A code may have a variant used when every
# parameter it names is present (key "code:params").
MESSAGES: dict[str, dict[str, str]] = {
    "timeout": {
        "en": "The source did not answer in time.",
        "fr": "La source n’a pas répondu à temps.",
        "es": "La fuente no respondió a tiempo.",
        "ja": "情報源が時間内に応答しませんでした。",
        "id": "Sumber tidak menjawab tepat waktu.",
    },
    "budget": {
        "en": "Still loading from the source: it will appear on the next refresh.",
        "fr": "Chargement en cours depuis la source : visible au prochain rafraîchissement.",
        "es": "Aún cargando desde la fuente: aparecerá en la próxima actualización.",
        "ja": "情報源から読み込み中です。次の更新で表示されます。",
        "id": "Masih dimuat dari sumber: akan tampil pada pembaruan berikutnya.",
    },
    "busy": {
        "en": "The source is busy right now: try again in a moment.",
        "fr": "La source est occupée en ce moment : réessayez dans un instant.",
        "es": "La fuente está ocupada en este momento: inténtelo de nuevo en un momento.",
        "ja": "情報源が混み合っています。しばらくしてから再度お試しください。",
        "id": "Sumber sedang sibuk: coba lagi sebentar lagi.",
    },
    "upstream_http": {
        "en": "The source answered with an error.",
        "fr": "La source a répondu par une erreur.",
        "es": "La fuente respondió con un error.",
        "ja": "情報源がエラーを返しました。",
        "id": "Sumber menjawab dengan galat.",
    },
    "upstream_unreachable": {
        "en": "The source cannot be reached.",
        "fr": "La source est injoignable.",
        "es": "No se puede contactar con la fuente.",
        "ja": "情報源に接続できません。",
        "id": "Sumber tidak dapat dihubungi.",
    },
    "upstream_parse": {
        "en": "The source sent data in an unexpected shape.",
        "fr": "La source a envoyé des données dans une forme inattendue.",
        "es": "La fuente envió datos con una forma inesperada.",
        "ja": "情報源のデータが想定外の形式でした。",
        "id": "Sumber mengirim data dalam bentuk yang tidak terduga.",
    },
    "stale_upstream": {
        "en": "The source has published nothing recent.",
        "fr": "La source n’a rien publié de récent.",
        "es": "La fuente no ha publicado nada reciente.",
        "ja": "情報源に最近の更新がありません。",
        "id": "Sumber tidak menerbitkan data terbaru.",
    },
    "no_coverage": {
        "en": "This place is outside the area this source covers.",
        "fr": "Ce lieu est hors de la zone couverte par cette source.",
        "es": "Este lugar está fuera de la zona que cubre esta fuente.",
        "ja": "この場所はこの情報源の対象範囲外です。",
        "id": "Tempat ini di luar wilayah cakupan sumber ini.",
    },
    "no_station": {
        "en": "No station near this place has reported recently.",
        "fr": "Aucune station proche de ce lieu n’a transmis récemment.",
        "es": "Ninguna estación cercana a este lugar ha informado recientemente.",
        "ja": "この場所の近くで最近報告した観測所はありません。",
        "id": "Tidak ada stasiun di dekat tempat ini yang melapor baru-baru ini.",
    },
    "no_station:km,since": {
        "en": "No station within {km} km has reported since {since} UTC.",
        "fr": "Aucune station à moins de {km} km n’a transmis depuis {since} UTC.",
        "es": "Ninguna estación a menos de {km} km ha informado desde las {since} UTC.",
        "ja": "{km} km 以内に、{since} UTC 以降に報告した観測所はありません。",
        "id": "Tidak ada stasiun dalam {km} km yang melapor sejak {since} UTC.",
    },
    "no_station:km,hours": {
        "en": "No station within {km} km has reported in the last {hours} hours.",
        "fr": "Aucune station à moins de {km} km n’a transmis depuis {hours} heures.",
        "es": "Ninguna estación a menos de {km} km ha informado en las últimas {hours} horas.",
        "ja": "{km} km 以内に、過去 {hours} 時間に報告した観測所はありません。",
        "id": "Tidak ada stasiun dalam {km} km yang melapor dalam {hours} jam terakhir.",
    },
    # the reads held cover too little to name a window (just after a restart)
    "no_station:km": {
        "en": "No station within {km} km is in the latest reports.",
        "fr": "Aucune station à moins de {km} km ne figure dans les derniers relevés.",
        "es": "Ninguna estación a menos de {km} km figura en los últimos informes.",
        "ja": "{km} km 以内の観測所は、最新の報告に含まれていません。",
        "id": "Tidak ada stasiun dalam {km} km di laporan terbaru.",
    },
    "coast_not_resolved": {
        "en": "The model has no sea cell close enough to this place.",
        "fr": "Le modèle n’a pas de maille marine assez proche de ce lieu.",
        "es": "El modelo no tiene una celda marina lo bastante cerca de este lugar.",
        "ja": "このモデルにはこの場所に十分近い海域の格子がありません。",
        "id": "Model tidak memiliki sel laut yang cukup dekat dengan tempat ini.",
    },
    "masked": {
        "en": "The source publishes no value at this place.",
        "fr": "La source ne publie aucune valeur pour ce lieu.",
        "es": "La fuente no publica ningún valor para este lugar.",
        "ja": "情報源はこの場所の値を公表していません。",
        "id": "Sumber tidak menerbitkan nilai untuk tempat ini.",
    },
    "warming": {
        "en": "This source is being read for the first time: try again in a few minutes.",
        "fr": "Cette source est lue pour la première fois : réessayez dans quelques minutes.",
        "es": "Esta fuente se está leyendo por primera vez: inténtelo de nuevo en unos minutos.",
        "ja": "この情報源を初めて読み込んでいます。数分後に再度お試しください。",
        "id": "Sumber ini sedang dibaca untuk pertama kali: coba lagi dalam beberapa menit.",
    },
    "inland": {
        "en": "This place is not on a coast.",
        "fr": "Ce lieu n’est pas sur une côte.",
        "es": "Este lugar no está en una costa.",
        "ja": "この場所は海岸沿いではありません。",
        "id": "Tempat ini tidak berada di pesisir.",
    },
    "inland:km": {
        "en": "The nearest ocean coastline is {km} km away.",
        "fr": "Le littoral océanique le plus proche est à {km} km.",
        "es": "La costa oceánica más cercana está a {km} km.",
        "ja": "最も近い海岸線まで {km} km あります。",
        "id": "Garis pantai laut terdekat berjarak {km} km.",
    },
    "at_sea": {
        "en": "This place is at sea and this source covers land only.",
        "fr": "Ce lieu est en mer et cette source ne couvre que les terres.",
        "es": "Este lugar está en el mar y esta fuente solo cubre tierra firme.",
        "ja": "この場所は海上で、この情報源は陸地のみを対象としています。",
        "id": "Tempat ini berada di laut dan sumber ini hanya mencakup daratan.",
    },
    "no_country": {
        "en": "This place is in no country, and this source gives national scores.",
        "fr": "Ce lieu n’est dans aucun pays, et cette source donne des scores nationaux.",
        "es": "Este lugar no está en ningún país, y esta fuente da puntuaciones nacionales.",
        "ja": "この場所はどの国にも属さず、この情報源は国別の値のみです。",
        "id": "Tempat ini tidak berada di negara mana pun, dan sumber ini memberi skor nasional.",
    },
}


def message(code: str, lang: str, params: dict[str, Any] | None = None) -> str:
    lang = lang if lang in LANGS else "en"
    params = params or {}
    best: list[str] = []
    sentence = None
    for key, sentences in MESSAGES.items():
        base, _, names = key.partition(":")
        if base != code or not names:
            continue
        wanted = names.split(",")
        # the variant naming the most of the given parameters, whatever the
        # order of the table ("no_station:km" never hides "no_station:km,hours")
        if all(params.get(name) is not None for name in wanted) and len(wanted) > len(best):
            best, sentence = wanted, sentences[lang]
    if sentence is not None:
        return sentence.format(**{name: params[name] for name in best})
    generic = MESSAGES.get(code) or MESSAGES["upstream_parse"]
    return generic[lang]
