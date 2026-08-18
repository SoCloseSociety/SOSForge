/** Internationalization.
 *
 * No library: about sixty strings doesn't justify a 40 KB dependency in a
 * product whose whole promise is responsiveness. The language is detected
 * from the browser, remembered, and overridable by hand.
 *
 * The choice of languages isn't arbitrary: English and Spanish for global
 * coverage, French for the team, and **Japanese + Indonesian** because
 * those are the two populations most exposed to earthquakes and tsunamis on
 * the planet -- and precisely the ones the JMA and BMKG sources serve.
 */

export type Lang = 'fr' | 'en' | 'es' | 'ja' | 'id'

export const LANGS: { code: Lang; label: string; flag: string }[] = [
  { code: 'fr', label: 'Français', flag: '🇫🇷' },
  { code: 'en', label: 'English', flag: '🇬🇧' },
  { code: 'es', label: 'Español', flag: '🇪🇸' },
  { code: 'ja', label: '日本語', flag: '🇯🇵' },
  { code: 'id', label: 'Indonesia', flag: '🇮🇩' },
]

type Dict = Record<string, string>

const fr: Dict = {
  'app.tagline': 'séismes, tsunamis et alertes en direct',
  'app.live': 'EN DIRECT',
  'app.reconnecting': 'RECONNEXION',
  'app.sound.on': 'Son actif',
  'app.sound.off': 'Son coupé',
  'app.sound.title': 'Alerte sonore sur les événements graves',

  'kpi.quakes': 'Séismes (1 h)',
  'kpi.quakes.last': 'dernier {age}',
  'kpi.maxmag': 'Magnitude max (1 h)',
  'kpi.maxmag.sub': 'toutes zones',
  'kpi.tsunami': 'Alertes tsunami',
  'kpi.tsunami.sub': 'bulletins actifs',
  'kpi.tracked': 'Événements suivis',
  'kpi.tracked.sub': '{n} sur la dernière heure',
  'kpi.sources': 'Sources en ligne',
  'kpi.sources.sub': 'agrégation multi-flux',

  'banner.tsunami': 'ALERTE TSUNAMI',
  'banner.major': 'ALERTE MAJEURE',
  'banner.others': '{n} autre(s) alerte(s) en cours',

  'search.placeholder': 'Rechercher une zone...',
  'search.goto': 'Aller à',
  'search.none': 'Aucune zone trouvée',
  'filters.empty.search': 'Aucun événement ne correspond à cette recherche.',
  'arrival.title': 'Ondes sismiques vers {place}',
  'arrival.detail': 'M{mag}, à {km} km -- {place}',
  'arrival.p.in': 'onde P dans {n} s',
  'arrival.p.passed': 'onde P déjà passée',
  'watch.set': 'Surveiller cette zone',
  'watch.on': 'Zone surveillée: {place}',
  'filters.window': 'Fenêtre',
  'filters.minmag': 'Magnitude min',
  'filters.none': 'Aucun evenement de ce type dans la fenetre choisie',
  'filters.empty': 'Aucun événement ne correspond aux filtres.',
  'filters.empty.window': 'Rien dans cette fenêtre. Élargissez la période.',

  'window.15': 'Direct',
  'window.60': '1 h',
  'window.360': '6 h',
  'window.1440': '24 h',
  'window.0': 'Tout',

  'sev.info': 'Info',
  'sev.minor': 'Faible',
  'sev.moderate': 'Modéré',
  'sev.severe': 'Fort',
  'sev.extreme': 'Extrême',

  'kind.earthquake': 'Séismes',
  'kind.tsunami': 'Tsunamis',
  'kind.volcano': 'Volcans',
  'kind.cyclone': 'Cyclones',
  'kind.flood': 'Inondations',
  'kind.wildfire': 'Incendies',
  'kind.storm': 'Tempêtes',
  'kind.heat': 'Chaleur',
  'kind.drought': 'Sécheresses',
  'kind.other': 'Autres',

  'age.now': "à l'instant",
  'age.in.min': 'dans {n} min',
  'age.in.h': 'dans {n} h',
  'age.in.d': 'dans {n} j',
  'age.s': 'il y a {n} s',
  'age.min': 'il y a {n} min',
  'age.h': 'il y a {n} h',
  'age.d': 'il y a {n} j',

  'map.legend': 'Gravité',
  'wave.p': 'onde P (premiere secousse)',
  'wave.s': 'onde S (secousse destructrice)',
  'map.unavailable': 'Carte indisponible sur cet appareil',
  'map.unavailable.detail':
    "WebGL n'a pas pu démarrer. Le flux temps réel, lui, continue de tourner.",
  'map.revised': 'révisé',

  'detail.depth': 'Profondeur',
  'detail.magnitude': 'Magnitude',
  'detail.time': 'Heure UTC',
  'detail.source': 'Source',
  'detail.official': 'Détail officiel',
  'detail.close': 'Fermer',
  'detail.share': 'Copier le lien vers cet événement',

  'live.title': 'Voir la zone en direct',
  'live.cameras': 'Webcams à proximité',
  'live.nocamera': 'Aucune webcam listée dans un rayon de 100 km.',
  'live.nokey': 'Webcams non configurées (clé Windy absente). Les liens ci-dessus fonctionnent.',
  'live.nocoords': 'Cet événement n’a pas de position précise.',
  'live.loading': 'Recherche des vues disponibles...',

  'footer.clients': '{n} client(s) connecté(s)',
  'footer.basemap': 'Fonds de carte OpenStreetMap / CARTO',
}

const en: Dict = {
  'app.tagline': 'earthquakes, tsunamis and alerts, live',
  'app.live': 'LIVE',
  'app.reconnecting': 'RECONNECTING',
  'app.sound.on': 'Sound on',
  'app.sound.off': 'Sound off',
  'app.sound.title': 'Audio alert on severe events',

  'kpi.quakes': 'Earthquakes (1 h)',
  'kpi.quakes.last': 'latest {age}',
  'kpi.maxmag': 'Max magnitude (1 h)',
  'kpi.maxmag.sub': 'worldwide',
  'kpi.tsunami': 'Tsunami alerts',
  'kpi.tsunami.sub': 'active bulletins',
  'kpi.tracked': 'Tracked events',
  'kpi.tracked.sub': '{n} in the last hour',
  'kpi.sources': 'Sources online',
  'kpi.sources.sub': 'multi-feed aggregation',

  'banner.tsunami': 'TSUNAMI ALERT',
  'banner.major': 'MAJOR ALERT',
  'banner.others': '{n} other alert(s) ongoing',

  'search.placeholder': 'Search an area...',
  'search.goto': 'Go to',
  'search.none': 'No area found',
  'filters.empty.search': 'No event matches this search.',
  'arrival.title': 'Seismic waves inbound to {place}',
  'arrival.detail': 'M{mag}, {km} km away -- {place}',
  'arrival.p.in': 'P wave in {n} s',
  'arrival.p.passed': 'P wave already passed',
  'watch.set': 'Watch this area',
  'watch.on': 'Watching: {place}',
  'filters.window': 'Window',
  'filters.minmag': 'Min magnitude',
  'filters.none': 'No event of this kind in the selected window',
  'filters.empty': 'No event matches the filters.',
  'filters.empty.window': 'Nothing in this window. Widen the period.',

  'window.15': 'Live',
  'window.60': '1 h',
  'window.360': '6 h',
  'window.1440': '24 h',
  'window.0': 'All',

  'sev.info': 'Info',
  'sev.minor': 'Minor',
  'sev.moderate': 'Moderate',
  'sev.severe': 'Severe',
  'sev.extreme': 'Extreme',

  'kind.earthquake': 'Earthquakes',
  'kind.tsunami': 'Tsunamis',
  'kind.volcano': 'Volcanoes',
  'kind.cyclone': 'Cyclones',
  'kind.flood': 'Floods',
  'kind.wildfire': 'Wildfires',
  'kind.storm': 'Storms',
  'kind.heat': 'Heat',
  'kind.drought': 'Droughts',
  'kind.other': 'Other',

  'age.now': 'just now',
  'age.in.min': 'in {n} min',
  'age.in.h': 'in {n} h',
  'age.in.d': 'in {n} d',
  'age.s': '{n} s ago',
  'age.min': '{n} min ago',
  'age.h': '{n} h ago',
  'age.d': '{n} d ago',

  'map.legend': 'Severity',
  'wave.p': 'P wave (first shaking)',
  'wave.s': 'S wave (damaging shaking)',
  'map.unavailable': 'Map unavailable on this device',
  'map.unavailable.detail': 'WebGL could not start. The live feed keeps running.',
  'map.revised': 'revised',

  'detail.depth': 'Depth',
  'detail.magnitude': 'Magnitude',
  'detail.time': 'Time UTC',
  'detail.source': 'Source',
  'detail.official': 'Official detail',
  'detail.close': 'Close',
  'detail.share': 'Copy link to this event',

  'live.title': 'See the area live',
  'live.cameras': 'Nearby webcams',
  'live.nocamera': 'No webcam listed within 100 km.',
  'live.nokey': 'Webcams not configured (no Windy key). The links above still work.',
  'live.nocoords': 'This event has no precise location.',
  'live.loading': 'Looking for available views...',

  'footer.clients': '{n} client(s) connected',
  'footer.basemap': 'Basemap OpenStreetMap / CARTO',
}

const es: Dict = {
  'app.tagline': 'sismos, tsunamis y alertas en directo',
  'app.live': 'EN DIRECTO',
  'app.reconnecting': 'RECONECTANDO',
  'app.sound.on': 'Sonido activo',
  'app.sound.off': 'Sonido apagado',
  'app.sound.title': 'Alerta sonora en eventos graves',

  'kpi.quakes': 'Sismos (1 h)',
  'kpi.quakes.last': 'último {age}',
  'kpi.maxmag': 'Magnitud máx. (1 h)',
  'kpi.maxmag.sub': 'todo el mundo',
  'kpi.tsunami': 'Alertas de tsunami',
  'kpi.tsunami.sub': 'boletines activos',
  'kpi.tracked': 'Eventos seguidos',
  'kpi.tracked.sub': '{n} en la última hora',
  'kpi.sources': 'Fuentes en línea',
  'kpi.sources.sub': 'agregación multiflujo',

  'banner.tsunami': 'ALERTA DE TSUNAMI',
  'banner.major': 'ALERTA MAYOR',
  'banner.others': '{n} otra(s) alerta(s) en curso',

  'search.placeholder': 'Buscar una zona...',
  'search.goto': 'Ir a',
  'search.none': 'Ninguna zona encontrada',
  'filters.empty.search': 'Ningún evento coincide con esta búsqueda.',
  'arrival.title': 'Ondas sísmicas hacia {place}',
  'arrival.detail': 'M{mag}, a {km} km -- {place}',
  'arrival.p.in': 'onda P en {n} s',
  'arrival.p.passed': 'onda P ya pasó',
  'watch.set': 'Vigilar esta zona',
  'watch.on': 'Zona vigilada: {place}',
  'filters.window': 'Ventana',
  'filters.minmag': 'Magnitud mín.',
  'filters.none': 'Ningun evento de este tipo en la ventana elegida',
  'filters.empty': 'Ningún evento coincide con los filtros.',
  'filters.empty.window': 'Nada en esta ventana. Amplíe el periodo.',

  'window.15': 'Directo',
  'window.60': '1 h',
  'window.360': '6 h',
  'window.1440': '24 h',
  'window.0': 'Todo',

  'sev.info': 'Info',
  'sev.minor': 'Leve',
  'sev.moderate': 'Moderado',
  'sev.severe': 'Fuerte',
  'sev.extreme': 'Extremo',

  'kind.earthquake': 'Sismos',
  'kind.tsunami': 'Tsunamis',
  'kind.volcano': 'Volcanes',
  'kind.cyclone': 'Ciclones',
  'kind.flood': 'Inundaciones',
  'kind.wildfire': 'Incendios',
  'kind.storm': 'Tormentas',
  'kind.heat': 'Calor',
  'kind.drought': 'Sequías',
  'kind.other': 'Otros',

  'age.now': 'ahora mismo',
  'age.in.min': 'en {n} min',
  'age.in.h': 'en {n} h',
  'age.in.d': 'en {n} d',
  'age.s': 'hace {n} s',
  'age.min': 'hace {n} min',
  'age.h': 'hace {n} h',
  'age.d': 'hace {n} d',

  'map.legend': 'Gravedad',
  'wave.p': 'onda P (primera sacudida)',
  'wave.s': 'onda S (sacudida destructiva)',
  'map.unavailable': 'Mapa no disponible en este dispositivo',
  'map.unavailable.detail': 'WebGL no pudo iniciarse. El flujo en directo sigue funcionando.',
  'map.revised': 'revisado',

  'detail.depth': 'Profundidad',
  'detail.magnitude': 'Magnitud',
  'detail.time': 'Hora UTC',
  'detail.source': 'Fuente',
  'detail.official': 'Detalle oficial',
  'detail.close': 'Cerrar',
  'detail.share': 'Copiar enlace a este evento',

  'live.title': 'Ver la zona en directo',
  'live.cameras': 'Webcams cercanas',
  'live.nocamera': 'Ninguna webcam listada en 100 km.',
  'live.nokey': 'Webcams no configuradas (falta la clave Windy). Los enlaces siguen funcionando.',
  'live.nocoords': 'Este evento no tiene una posición precisa.',
  'live.loading': 'Buscando vistas disponibles...',

  'footer.clients': '{n} cliente(s) conectado(s)',
  'footer.basemap': 'Mapa base OpenStreetMap / CARTO',
}

const ja: Dict = {
  'app.tagline': '地震・津波・災害警報のライブ配信',
  'app.live': 'ライブ',
  'app.reconnecting': '再接続中',
  'app.sound.on': '音声オン',
  'app.sound.off': '音声オフ',
  'app.sound.title': '重大な事象で音声警報',

  'kpi.quakes': '地震（1時間）',
  'kpi.quakes.last': '最新 {age}',
  'kpi.maxmag': '最大マグニチュード（1時間）',
  'kpi.maxmag.sub': '全世界',
  'kpi.tsunami': '津波警報',
  'kpi.tsunami.sub': '有効な情報',
  'kpi.tracked': '追跡中の事象',
  'kpi.tracked.sub': '直近1時間で {n} 件',
  'kpi.sources': '稼働中の情報源',
  'kpi.sources.sub': '複数フィード統合',

  'banner.tsunami': '津波警報',
  'banner.major': '重大警報',
  'banner.others': '他に {n} 件の警報が継続中',

  'search.placeholder': '地域を検索...',
  'search.goto': '移動',
  'search.none': '地域が見つかりません',
  'filters.empty.search': 'この検索に一致する事象はありません。',
  'arrival.title': '{place} へ地震波が接近中',
  'arrival.detail': 'M{mag}、{km} km -- {place}',
  'arrival.p.in': 'P波まで {n} 秒',
  'arrival.p.passed': 'P波は通過済み',
  'watch.set': 'この地域を監視',
  'watch.on': '監視中: {place}',
  'filters.window': '期間',
  'filters.minmag': '最小マグニチュード',
  'filters.none': '選択した期間にこの種類の事象はありません',
  'filters.empty': '条件に一致する事象はありません。',
  'filters.empty.window': 'この期間には何もありません。期間を広げてください。',

  'window.15': 'ライブ',
  'window.60': '1時間',
  'window.360': '6時間',
  'window.1440': '24時間',
  'window.0': 'すべて',

  'sev.info': '情報',
  'sev.minor': '軽微',
  'sev.moderate': '中程度',
  'sev.severe': '強い',
  'sev.extreme': '極めて甚大',

  'kind.earthquake': '地震',
  'kind.tsunami': '津波',
  'kind.volcano': '火山',
  'kind.cyclone': '熱帯低気圧',
  'kind.flood': '洪水',
  'kind.wildfire': '林野火災',
  'kind.storm': '暴風',
  'kind.heat': '高温',
  'kind.drought': '干ばつ',
  'kind.other': 'その他',

  'age.now': 'たった今',
  'age.in.min': '{n} 分後',
  'age.in.h': '{n} 時間後',
  'age.in.d': '{n} 日後',
  'age.s': '{n} 秒前',
  'age.min': '{n} 分前',
  'age.h': '{n} 時間前',
  'age.d': '{n} 日前',

  'map.legend': '深刻度',
  'wave.p': 'P波（初期微動）',
  'wave.s': 'S波（主要動）',
  'map.unavailable': 'この端末では地図を利用できません',
  'map.unavailable.detail': 'WebGL を起動できませんでした。ライブ配信は継続しています。',
  'map.revised': '更新',

  'detail.depth': '深さ',
  'detail.magnitude': 'マグニチュード',
  'detail.time': '時刻（UTC）',
  'detail.source': '情報源',
  'detail.official': '公式情報',
  'detail.close': '閉じる',
  'detail.share': 'この事象へのリンクをコピー',

  'live.title': '現地のライブ映像',
  'live.cameras': '近くのウェブカメラ',
  'live.nocamera': '半径100km以内にウェブカメラはありません。',
  'live.nokey': 'ウェブカメラ未設定（Windy キーなし）。上のリンクは利用できます。',
  'live.nocoords': 'この事象には正確な位置がありません。',
  'live.loading': '利用可能な映像を検索中...',

  'footer.clients': '接続中のクライアント {n} 台',
  'footer.basemap': '地図データ OpenStreetMap / CARTO',
}

const id: Dict = {
  'app.tagline': 'gempa, tsunami dan peringatan secara langsung',
  'app.live': 'LANGSUNG',
  'app.reconnecting': 'MENYAMBUNG ULANG',
  'app.sound.on': 'Suara aktif',
  'app.sound.off': 'Suara mati',
  'app.sound.title': 'Peringatan suara untuk kejadian berat',

  'kpi.quakes': 'Gempa (1 jam)',
  'kpi.quakes.last': 'terakhir {age}',
  'kpi.maxmag': 'Magnitudo maks. (1 jam)',
  'kpi.maxmag.sub': 'seluruh dunia',
  'kpi.tsunami': 'Peringatan tsunami',
  'kpi.tsunami.sub': 'buletin aktif',
  'kpi.tracked': 'Kejadian terpantau',
  'kpi.tracked.sub': '{n} dalam satu jam terakhir',
  'kpi.sources': 'Sumber aktif',
  'kpi.sources.sub': 'agregasi banyak sumber',

  'banner.tsunami': 'PERINGATAN TSUNAMI',
  'banner.major': 'PERINGATAN BESAR',
  'banner.others': '{n} peringatan lain sedang berlangsung',

  'search.placeholder': 'Cari wilayah...',
  'search.goto': 'Ke',
  'search.none': 'Wilayah tidak ditemukan',
  'filters.empty.search': 'Tidak ada kejadian yang cocok dengan pencarian ini.',
  'arrival.title': 'Gelombang gempa menuju {place}',
  'arrival.detail': 'M{mag}, {km} km -- {place}',
  'arrival.p.in': 'gelombang P dalam {n} detik',
  'arrival.p.passed': 'gelombang P sudah lewat',
  'watch.set': 'Pantau wilayah ini',
  'watch.on': 'Dipantau: {place}',
  'filters.window': 'Rentang',
  'filters.minmag': 'Magnitudo min.',
  'filters.none': 'Tidak ada kejadian jenis ini pada rentang terpilih',
  'filters.empty': 'Tidak ada kejadian yang cocok dengan filter.',
  'filters.empty.window': 'Tidak ada apa pun pada rentang ini. Perlebar periodenya.',

  'window.15': 'Langsung',
  'window.60': '1 jam',
  'window.360': '6 jam',
  'window.1440': '24 jam',
  'window.0': 'Semua',

  'sev.info': 'Info',
  'sev.minor': 'Ringan',
  'sev.moderate': 'Sedang',
  'sev.severe': 'Kuat',
  'sev.extreme': 'Ekstrem',

  'kind.earthquake': 'Gempa',
  'kind.tsunami': 'Tsunami',
  'kind.volcano': 'Gunung api',
  'kind.cyclone': 'Siklon',
  'kind.flood': 'Banjir',
  'kind.wildfire': 'Kebakaran',
  'kind.storm': 'Badai',
  'kind.heat': 'Panas ekstrem',
  'kind.drought': 'Kekeringan',
  'kind.other': 'Lainnya',

  'age.now': 'baru saja',
  'age.in.min': 'dalam {n} menit',
  'age.in.h': 'dalam {n} jam',
  'age.in.d': 'dalam {n} hari',
  'age.s': '{n} detik lalu',
  'age.min': '{n} menit lalu',
  'age.h': '{n} jam lalu',
  'age.d': '{n} hari lalu',

  'map.legend': 'Tingkat bahaya',
  'wave.p': 'gelombang P (guncangan awal)',
  'wave.s': 'gelombang S (guncangan merusak)',
  'map.unavailable': 'Peta tidak tersedia di perangkat ini',
  'map.unavailable.detail': 'WebGL gagal dijalankan. Aliran langsung tetap berjalan.',
  'map.revised': 'direvisi',

  'detail.depth': 'Kedalaman',
  'detail.magnitude': 'Magnitudo',
  'detail.time': 'Waktu UTC',
  'detail.source': 'Sumber',
  'detail.official': 'Rincian resmi',
  'detail.close': 'Tutup',
  'detail.share': 'Salin tautan ke kejadian ini',

  'live.title': 'Lihat lokasi secara langsung',
  'live.cameras': 'Webcam terdekat',
  'live.nocamera': 'Tidak ada webcam dalam radius 100 km.',
  'live.nokey': 'Webcam belum dikonfigurasi (kunci Windy tidak ada). Tautan di atas tetap bisa dipakai.',
  'live.nocoords': 'Kejadian ini tidak memiliki posisi yang pasti.',
  'live.loading': 'Mencari tampilan yang tersedia...',

  'footer.clients': '{n} klien terhubung',
  'footer.basemap': 'Peta dasar OpenStreetMap / CARTO',
}

const DICTS: Record<Lang, Dict> = { fr, en, es, ja, id }

const STORAGE_KEY = 'sosforge.lang'

export function detectLang(): Lang {
  // `detectLang` runs when the store is created, so at module load time. A
  // localStorage access that throws (iframe with blocked cookies, certain
  // private modes) would cause exactly what this product dreads the most: a
  // blank page on startup.
  let stored: Lang | null = null
  try {
    stored = localStorage.getItem(STORAGE_KEY) as Lang | null
  } catch {
    stored = null
  }
  if (stored && stored in DICTS) return stored
  for (const candidate of navigator.languages ?? [navigator.language]) {
    const code = candidate.slice(0, 2).toLowerCase() as Lang
    if (code in DICTS) return code
  }
  // English by default: a disaster tracker is read by anyone
  return 'en'
}

export function persistLang(lang: Lang): void {
  try {
    localStorage.setItem(STORAGE_KEY, lang)
  } catch {
    /* private mode: the language won't survive a reload, that's all */
  }
}

/** Translates, with `{key}` interpolation. A missing string falls back to
 * English, then to its own key -- never to blank. */
export function translate(lang: Lang, key: string, vars?: Record<string, string | number>): string {
  const value = DICTS[lang]?.[key] ?? DICTS.en[key] ?? key
  if (!vars) return value
  return value.replace(/\{(\w+)\}/g, (match, name) => String(vars[name] ?? match))
}
