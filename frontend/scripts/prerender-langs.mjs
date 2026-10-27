/**
 * Writes one indexable HTML page per language, after the Vite build.
 *
 * The product ships five languages and had exactly one findable page, in
 * English, on one URL. Someone in Sendai searching 地震 速報, or in Jakarta
 * searching gempa sekarang, could never match it: there was no Japanese or
 * Indonesian text anywhere on the domain for a crawler to index, because the
 * translation happens in JavaScript after the page loads and crawlers are
 * indexing the HTML.
 *
 * This is not server-side rendering and does not pretend to be. Each page is
 * the same application shell with a translated head and a translated
 * <noscript> body, at its own URL, cross-linked with hreflang. The live events
 * are still rendered by the client -- what changes is that the PRODUCT is
 * findable in five languages instead of one.
 */
import { readFileSync, writeFileSync, mkdirSync, statSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const dist = join(here, '..', 'dist')
const seo = JSON.parse(readFileSync(join(here, '..', 'src', 'seo.json'), 'utf8'))
const ORIGIN = 'https://sosforge.soclose.co'
const LANGS = Object.keys(seo)
const DEFAULT = 'en'

/** Where a given language lives. English keeps the root: it is the existing
 * canonical URL and moving it would throw away everything already indexed. */
const pathFor = (lang) => (lang === DEFAULT ? '/' : `/${lang}/`)

/** The alternates block, identical on every page -- that is what tells a
 * search engine these are translations of one another rather than five
 * competing pages. */
function alternates() {
  const links = LANGS.map(
    (lang) => `    <link rel="alternate" hreflang="${lang}" href="${ORIGIN}${pathFor(lang)}" />`,
  )
  links.push(`    <link rel="alternate" hreflang="x-default" href="${ORIGIN}/" />`)
  return links.join('\n')
}

function escapeHtml(text) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

/** og:locale wants language_TERRITORY; a bare "fr" is not one. */
const OG_LOCALE = { en: 'en_US', fr: 'fr_FR', es: 'es_ES', ja: 'ja_JP', id: 'id_ID' }

/** The line that points at the Android app, in the page's own language.
 * Kept here rather than in src/seo.json (another lane's file); es, ja and id
 * need a native reader, like the rest of those dictionaries. */
const APP_LINE = {
  en: '<a href="/download/">SOSForge for Android</a>: the same live feed as an app that watches your places in the background and notifies you.',
  fr: '<a href="/download/">SOSForge pour Android</a> : le même flux en direct, en application qui surveille vos lieux en arrière-plan et vous prévient.',
  es: '<a href="/download/">SOSForge para Android</a>: el mismo flujo en directo, en una aplicación que vigila sus lugares en segundo plano y le avisa.',
  ja: '<a href="/download/">Android 版 SOSForge</a>：同じライブフィードを、登録した場所をバックグラウンドで見守り通知するアプリで。',
  id: '<a href="/download/">SOSForge untuk Android</a>: umpan langsung yang sama, dalam aplikasi yang memantau tempat Anda di latar belakang dan memberi tahu Anda.',
}

/** A replacement that MUST happen. These used to be silent no-ops when
 * index.html changed shape: og:locale was never replaced at all (the tag did
 * not exist), and a renamed paragraph would have shipped English text on the
 * Japanese page without a word. Now the build stops instead. */
function replaceOnce(html, pattern, replacement, what) {
  if (!pattern.test(html)) throw new Error(`prerender: ${what} not found in dist/index.html`)
  return html.replace(pattern, replacement)
}

function pageFor(lang, template) {
  const copy = seo[lang]
  const url = `${ORIGIN}${pathFor(lang)}`
  let html = template

  html = replaceOnce(html, /<html lang="en">/, `<html lang="${lang}">`, 'html lang')
  html = replaceOnce(html, /<title>[^<]*<\/title>/, `<title>${escapeHtml(copy.title)}</title>`, 'title')
  html = replaceOnce(
    html,
    /(<meta\s+name="description"\s+content=")[^"]*(")/s,
    `$1${escapeHtml(copy.description)}$2`,
    'description',
  )
  html = replaceOnce(
    html,
    /<link rel="canonical" href="[^"]*" \/>/,
    `<link rel="canonical" href="${url}" />\n${alternates()}`,
    'canonical',
  )
  html = replaceOnce(html, /(<meta property="og:url" content=")[^"]*(")/, `$1${url}$2`, 'og:url')
  html = replaceOnce(
    html,
    /(<meta property="og:title" content=")[^"]*(")/,
    `$1${escapeHtml(copy.title)}$2`,
    'og:title',
  )
  html = replaceOnce(
    html,
    /(<meta\s+property="og:description"\s+content=")[^"]*(")/s,
    `$1${escapeHtml(copy.social)}$2`,
    'og:description',
  )
  html = replaceOnce(
    html,
    /(<meta name="twitter:title" content=")[^"]*(")/,
    `$1${escapeHtml(copy.title)}$2`,
    'twitter:title',
  )
  html = replaceOnce(
    html,
    /(<meta\s+name="twitter:description"\s+content=")[^"]*(")/s,
    `$1${escapeHtml(copy.social)}$2`,
    'twitter:description',
  )
  html = replaceOnce(
    html,
    /(<meta property="og:locale" content=")[^"]*(")/,
    `$1${OG_LOCALE[lang]}$2`,
    'og:locale',
  )

  // The text inside #root: what a crawler that does not run JavaScript reads,
  // and what everyone sees until the app mounts. Its heading and first
  // paragraph carry the words people actually search for, so they must be in
  // the page's own language.
  html = replaceOnce(html, /<h1>[^<]*<\/h1>/, `<h1>${escapeHtml(copy.heading)}</h1>`, 'h1')
  html = replaceOnce(
    html,
    /(<p data-seo="intro">)[\s\S]*?(<\/p>)/,
    `$1${escapeHtml(copy.intro)}$2`,
    'intro paragraph',
  )
  html = replaceOnce(
    html,
    /(<strong data-seo="needs-js">)[\s\S]*?(<\/strong>)/,
    `$1${escapeHtml(copy.needsJs)}$2`,
    'needs-JavaScript note',
  )
  html = replaceOnce(html, /<h2>[^<]*<\/h2>/, `<h2>${escapeHtml(copy.sourcesHeading)}</h2>`, 'h2')
  html = replaceOnce(
    html,
    /(<p data-seo="app">)[\s\S]*?(<\/p>)/,
    `$1${APP_LINE[lang]}$2`,
    'app paragraph',
  )
  html = replaceOnce(html, /<em>[\s\S]*?<\/em>/, `<em>${escapeHtml(copy.disclaimer)}</em>`, 'disclaimer')

  return html
}

const template = readFileSync(join(dist, 'index.html'), 'utf8')

for (const lang of LANGS) {
  const html = pageFor(lang, template)
  if (lang === DEFAULT) {
    writeFileSync(join(dist, 'index.html'), html)
  } else {
    mkdirSync(join(dist, lang), { recursive: true })
    writeFileSync(join(dist, lang, 'index.html'), html)
  }
}

// The sitemap has to list them, or the alternates are the only signal.
const today = new Date().toISOString().slice(0, 10)
const urls = LANGS.map((lang) => {
  const alt = LANGS.map(
    (other) =>
      `    <xhtml:link rel="alternate" hreflang="${other}" href="${ORIGIN}${pathFor(other)}" />`,
  ).join('\n')
  return `  <url>
    <loc>${ORIGIN}${pathFor(lang)}</loc>
    <lastmod>${today}</lastmod>
    <changefreq>hourly</changefreq>
    <priority>${lang === DEFAULT ? '1.0' : '0.9'}</priority>
${alt}
  </url>`
}).join('\n')

// The download page: one URL, both of its languages on it (no hreflang --
// the SPA serves a language per URL, that page does not). Its lastmod is the
// day its source last changed, not the build day: git rewrites a file's
// mtime only when the file changes, and Docker's COPY keeps it.
const downloadSource = join(here, '..', 'public', 'download', 'index.html')
const downloadModified = statSync(downloadSource).mtime
const downloadLastmod = (downloadModified > new Date() ? new Date() : downloadModified).toISOString().slice(0, 10)
const download = `  <url>
    <loc>${ORIGIN}/download/</loc>
    <lastmod>${downloadLastmod}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>`

writeFileSync(
  join(dist, 'sitemap.xml'),
  `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:xhtml="http://www.w3.org/1999/xhtml">
${urls}
${download}
</urlset>
`,
)

console.log(`prerendered ${LANGS.length} languages: ${LANGS.join(', ')}`)
