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
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
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

function pageFor(lang, template) {
  const copy = seo[lang]
  const url = `${ORIGIN}${pathFor(lang)}`
  let html = template

  html = html.replace('<html lang="en">', `<html lang="${lang}">`)
  html = html.replace(/<title>[^<]*<\/title>/, `<title>${escapeHtml(copy.title)}</title>`)
  html = html.replace(
    /(<meta\s+name="description"\s+content=")[^"]*(")/s,
    `$1${escapeHtml(copy.description)}$2`,
  )
  html = html.replace(
    /<link rel="canonical" href="[^"]*" \/>/,
    `<link rel="canonical" href="${url}" />\n${alternates()}`,
  )
  html = html.replace(/(<meta property="og:url" content=")[^"]*(")/, `$1${url}$2`)
  html = html.replace(
    /(<meta property="og:title" content=")[^"]*(")/,
    `$1${escapeHtml(copy.title)}$2`,
  )
  html = html.replace(
    /(<meta\s+property="og:description"\s+content=")[^"]*(")/s,
    `$1${escapeHtml(copy.social)}$2`,
  )
  html = html.replace(
    /(<meta name="twitter:title" content=")[^"]*(")/,
    `$1${escapeHtml(copy.title)}$2`,
  )
  html = html.replace(
    /(<meta\s+name="twitter:description"\s+content=")[^"]*(")/s,
    `$1${escapeHtml(copy.social)}$2`,
  )
  html = html.replace(
    /(<meta property="og:locale" content=")[^"]*(")/,
    `$1${lang}$2`,
  )

  // The noscript body: the only text a crawler that does not run JavaScript
  // will ever read. Its heading and first paragraph carry the words people
  // actually search for, so they must be in the page's own language.
  html = html.replace(/<h1>[^<]*<\/h1>/, `<h1>${escapeHtml(copy.heading)}</h1>`)
  html = html.replace(
    /(<noscript>[\s\S]*?<p>\s*)[\s\S]*?(<strong>)[\s\S]*?(<\/strong>)/,
    `$1${escapeHtml(copy.intro)}\n          $2${escapeHtml(copy.needsJs)}$3`,
  )
  html = html.replace(/<h2>[^<]*<\/h2>/, `<h2>${escapeHtml(copy.sourcesHeading)}</h2>`)
  html = html.replace(/<em>[\s\S]*?<\/em>/, `<em>${escapeHtml(copy.disclaimer)}</em>`)

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

writeFileSync(
  join(dist, 'sitemap.xml'),
  `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:xhtml="http://www.w3.org/1999/xhtml">
${urls}
</urlset>
`,
)

console.log(`prerendered ${LANGS.length} languages: ${LANGS.join(', ')}`)
