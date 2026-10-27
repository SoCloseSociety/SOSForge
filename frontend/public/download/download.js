/**
 * The download page's only script (served as is, no build step).
 *
 * Everything this page says about the release comes from
 * /download/latest.json, which tools/publish_apk.py reads back from the
 * published APK itself, and is then checked against the server: a HEAD on
 * the APK must answer 200 with the announced size. Nothing is written in the
 * HTML that could disagree with the file -- no version, no size, no date. If
 * latest.json is missing, the page says the download is not available yet;
 * if it is unreadable or does not match the file, the download is held back
 * and the page says why. It never shows a placeholder.
 *
 * Runs in <head>, before the first paint: the language and the theme are set
 * on <html> at once (no flash of both languages), the rest waits for the DOM.
 */
;(function () {
  'use strict'

  var ORIGIN = 'https://sosforge.soclose.co'
  var LATEST = '/download/latest.json'
  var LANGS = ['en', 'fr']

  // Android release for each API level (developer.android.com, "API levels").
  var ANDROID = {
    21: '5.0', 22: '5.1', 23: '6.0', 24: '7.0', 25: '7.1', 26: '8.0', 27: '8.1',
    28: '9', 29: '10', 30: '11', 31: '12', 32: '12L', 33: '13', 34: '14', 35: '15', 36: '16',
  }

  var TEXT = {
    en: {
      loading: 'Reading the current release...',
      ok: 'Checked just now: this file is on the server, at the announced size.',
      unavailable: 'The download is not available yet: no release has been published.',
      http: 'The release information could not be read (HTTP {status}). Reload to try again.',
      invalid: 'The release information on the server is malformed, so the download is held back.',
      offline: 'The server could not be reached to read the current release. Check the connection and reload.',
      missing: 'The release is announced but its file is missing on the server, so the download is held back.',
      mismatch: 'The file on the server is {actual} bytes, not the announced {size}, so the download is held back.',
      button: 'Download SOSForge {version} ({size})',
      requires: 'Android {android} or newer',
      requiresLine: 'Android {android} or newer (API level {api}).',
      unit: 'MB',
    },
    fr: {
      loading: 'Lecture de la version actuelle...',
      ok: "Vérifié à l'instant : ce fichier est sur le serveur, à la taille annoncée.",
      unavailable: "Le téléchargement n'est pas encore disponible : aucune version n'a été publiée.",
      http: 'Les informations de version sont illisibles (HTTP {status}). Rechargez pour réessayer.',
      invalid: 'Les informations de version du serveur sont malformées : le téléchargement est suspendu.',
      offline: 'Le serveur est injoignable, la version actuelle ne peut pas être lue. Vérifiez la connexion et rechargez.',
      missing: 'La version est annoncée mais son fichier manque sur le serveur : le téléchargement est suspendu.',
      mismatch: 'Le fichier du serveur fait {actual} octets, pas les {size} annoncés : le téléchargement est suspendu.',
      button: 'Télécharger SOSForge {version} ({size})',
      requires: 'Android {android} ou plus récent',
      requiresLine: 'Android {android} ou plus récent (niveau d’API {api}).',
      unit: 'Mo',
    },
  }

  function pickLang() {
    // The app's own choice first (same key as src/i18n.ts), when it is one
    // of this page's two languages; then the browser's; English otherwise.
    try {
      var stored = localStorage.getItem('sosforge.lang')
      if (LANGS.indexOf(stored) >= 0) return stored
    } catch (e) {
      /* storage blocked: fall through */
    }
    var wanted = navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language || '']
    for (var i = 0; i < wanted.length; i++) {
      var code = String(wanted[i]).slice(0, 2).toLowerCase()
      if (LANGS.indexOf(code) >= 0) return code
    }
    return 'en'
  }

  function setLang(lang) {
    var root = document.documentElement
    root.lang = lang
    root.setAttribute('data-lang', lang)
    var buttons = document.querySelectorAll('[data-set-lang]')
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].setAttribute('aria-pressed', String(buttons[i].getAttribute('data-set-lang') === lang))
    }
  }

  var root = document.documentElement
  root.classList.add('js')
  try {
    // Lunar is the app's default whatever the system says, and the app
    // stores 'lunar' on every visit: only an explicit Soft is a choice.
    if (localStorage.getItem('sosforge.theme') === 'soft') root.setAttribute('data-theme', 'soft')
  } catch (e) {
    /* no stored theme: the system's light/dark decides */
  }
  setLang(pickLang())

  function fill(template, values) {
    return template.replace(/\{(\w+)\}/g, function (_, key) {
      return key in values ? values[key] : ''
    })
  }

  function valid(r) {
    return (
      !!r &&
      r.app === 'co.soclose.sosforge' &&
      typeof r.version === 'string' &&
      /^[0-9][0-9A-Za-z._-]*$/.test(r.version) &&
      Number.isInteger(r.version_code) &&
      typeof r.url === 'string' &&
      /^\/download\/sosforge-v[0-9][0-9A-Za-z._-]*\.apk$/.test(r.url) &&
      Number.isInteger(r.size) &&
      r.size > 0 &&
      /^[0-9a-f]{64}$/.test(r.sha256) &&
      /^[0-9a-f]{64}$/.test(r.cert_sha256) &&
      Number.isInteger(r.min_sdk) &&
      /^\d{4}-\d{2}-\d{2}$/.test(r.released) &&
      !!r.notes &&
      typeof r.notes === 'object'
    )
  }

  function megabytes(bytes, lang) {
    var n = new Intl.NumberFormat(lang, { minimumFractionDigits: 1, maximumFractionDigits: 1 })
    return n.format(bytes / 1e6) + ' ' + TEXT[lang].unit
  }

  function each(article, field, fn) {
    var nodes = article.querySelectorAll('[data-field="' + field + '"]')
    for (var i = 0; i < nodes.length; i++) fn(nodes[i])
  }

  function articles() {
    return document.querySelectorAll('article[data-lang]')
  }

  /** One state for the whole page; `release` only when it was verified. */
  function render(state, values, release) {
    var list = articles()
    for (var i = 0; i < list.length; i++) {
      var article = list[i]
      var lang = article.getAttribute('data-lang')
      var t = TEXT[lang]
      each(article, 'state', function (el) {
        el.hidden = false
        el.textContent = fill(t[state], values || {})
        el.setAttribute('data-tone', state === 'ok' ? 'ok' : state === 'loading' ? '' : 'warn')
      })
      var ok = state === 'ok' && release
      each(article, 'facts', function (el) {
        el.hidden = !ok
      })
      each(article, 'details', function (el) {
        el.hidden = !ok
      })
      each(article, 'download', function (el) {
        // Held back in every state but a verified one: a link we cannot
        // vouch for is not offered (the no-JavaScript link stays for readers
        // without this script, who were told where to check it).
        el.hidden = !ok && state !== 'loading'
      })
      each(article, 'requires-line', function (el) {
        // Its static text points at latest.json, which says nothing when
        // there is no release to describe.
        el.hidden = !ok && state !== 'loading'
      })
      if (!ok) continue

      var android = ANDROID[release.min_sdk]
      var size = megabytes(release.size, lang)
      each(article, 'version', function (el) {
        el.textContent = release.version
      })
      each(article, 'size', function (el) {
        el.textContent = size
        el.title = new Intl.NumberFormat(lang).format(release.size) + (lang === 'fr' ? ' octets' : ' bytes')
      })
      each(article, 'released', function (el) {
        el.textContent = new Intl.DateTimeFormat(lang, { dateStyle: 'long', timeZone: 'UTC' }).format(
          new Date(release.released + 'T00:00:00Z'),
        )
      })
      each(article, 'requires', function (el) {
        el.textContent = android ? fill(t.requires, { android: android }) : 'API ' + release.min_sdk
      })
      each(article, 'requires-line', function (el) {
        el.textContent = android
          ? fill(t.requiresLine, { android: android, api: release.min_sdk })
          : 'API ' + release.min_sdk
      })
      each(article, 'download', function (el) {
        el.href = release.url
        el.textContent = fill(t.button, { version: release.version, size: size })
      })
      each(article, 'sha256', function (el) {
        el.textContent = release.sha256
      })
      each(article, 'cert', function (el) {
        el.textContent = release.cert_sha256
      })
      each(article, 'sha-file', function (el) {
        el.href = release.url + '.sha256'
      })
      var notes = release.notes[lang] || release.notes.en || ''
      each(article, 'notes', function (el) {
        el.textContent = notes
      })
      each(article, 'notes-block', function (el) {
        el.hidden = !notes
      })
    }
  }

  /** The application, as structured data, from the verified release only. */
  function describe(release) {
    var android = ANDROID[release.min_sdk]
    var data = {
      '@context': 'https://schema.org',
      '@type': 'MobileApplication',
      '@id': ORIGIN + '/download/#app',
      name: 'SOSForge',
      url: ORIGIN + '/download/',
      operatingSystem: 'Android',
      softwareRequirements: android ? 'Android ' + android + ' or newer' : 'Android API ' + release.min_sdk,
      applicationCategory: 'UtilitiesApplication',
      softwareVersion: release.version,
      datePublished: release.released,
      fileSize: (release.size / 1e6).toFixed(1) + 'MB',
      downloadUrl: ORIGIN + release.url,
      isAccessibleForFree: true,
      offers: { '@type': 'Offer', price: '0', priceCurrency: 'USD' },
      publisher: { '@id': 'https://soclose.co/#organization' },
    }
    if (release.notes.en) data.releaseNotes = release.notes.en
    var script = document.createElement('script')
    script.type = 'application/ld+json'
    script.setAttribute('data-from', LATEST)
    script.textContent = JSON.stringify(data)
    document.head.appendChild(script)
  }

  async function load() {
    render('loading')
    var response
    try {
      response = await fetch(LATEST, { cache: 'no-cache', headers: { Accept: 'application/json' } })
    } catch (e) {
      return render('offline')
    }
    if (response.status === 404) return render('unavailable')
    if (!response.ok) return render('http', { status: response.status })
    var release
    try {
      release = await response.json()
    } catch (e) {
      return render('invalid')
    }
    if (!valid(release)) return render('invalid')

    // The announcement is not the file. Ask the server for the file itself.
    var head
    try {
      head = await fetch(release.url, { method: 'HEAD', cache: 'no-cache' })
    } catch (e) {
      return render('offline')
    }
    if (!head.ok) return render('missing')
    var length = head.headers.get('Content-Length')
    if (length !== null && Number(length) !== release.size) {
      return render('mismatch', { actual: length, size: release.size })
    }
    render('ok', null, release)
    describe(release)
  }

  function init() {
    var buttons = document.querySelectorAll('[data-set-lang]')
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].addEventListener('click', function (event) {
        setLang(event.currentTarget.getAttribute('data-set-lang'))
      })
    }
    setLang(document.documentElement.getAttribute('data-lang') || 'en')
    var details = document.querySelectorAll('details[data-field="details"]')
    for (var j = 0; j < details.length; j++) {
      details[j].addEventListener('toggle', function (event) {
        var summary = event.currentTarget.querySelector('summary')
        summary.textContent = event.currentTarget.open ? summary.dataset.less : summary.dataset.more
      })
    }
    load()
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init)
  else init()
})()
