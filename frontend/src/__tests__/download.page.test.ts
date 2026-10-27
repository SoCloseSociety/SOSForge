// @vitest-environment jsdom
/** public/download/: the page says only what the published release says.
 *
 * The REAL page and the REAL download.js, in jsdom. RELEASE is verbatim the
 * latest.json that tools/publish_apk.py wrote from sosforge-v0.1.1.apk
 * (2026-10-27). The checks that need a layout engine (nothing overflows at
 * 280 px and 200 % text) were measured in Chrome, not here.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const fs = (await import(/* @vite-ignore */ ['node', 'fs'].join(':'))) as {
  readFileSync(path: string, encoding: string): string
}
const root = (globalThis as unknown as { process: { cwd(): string } }).process.cwd()
const html = fs.readFileSync(`${root}/public/download/index.html`, 'utf8')

const RELEASE = {
  app: 'co.soclose.sosforge',
  version: '0.1.1',
  version_code: 6,
  url: '/download/sosforge-v0.1.1.apk',
  size: 38946728,
  sha256: '971ebc3200517b0f541413e0338bf5a1ddd39721937b87a4bb94f1ff34a8b22f',
  cert_sha256: '610d345a896dfc71bd0ddc4122668c9ffcce0e16bc495d6ed4f573812a98bdf8',
  min_sdk: 26,
  released: '2026-10-27',
  notes: {
    en: "The server clock no longer reads late behind a queued message: in 0.1.0 every 'N s ago' could be 4 to 7 s too young on a slow network.",
    fr: "L'horloge du serveur ne lit plus en retard derrière un message resté en file : en 0.1.0, chaque « il y a N s » pouvait être 4 à 7 s trop jeune sur un réseau lent.",
  },
}

type Answer = { status: number; body?: unknown; length?: string }

async function openPage(latest: Answer, apk: Answer = { status: 200, length: String(RELEASE.size) }, lang = 'en-US') {
  document.documentElement.removeAttribute('data-lang')
  document.documentElement.innerHTML = html.replace(/^[\s\S]*?<html[^>]*>/, '').replace(/<\/html>[\s\S]*$/, '')
  vi.stubGlobal('navigator', { languages: [lang], language: lang })
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const a = url === '/download/latest.json' ? latest : apk
    const headers = new Headers(a.length ? { 'Content-Length': a.length } : {})
    const body = init?.method === 'HEAD' || a.body === undefined ? null : JSON.stringify(a.body)
    return new Response(body, { status: a.status, headers })
  })
  vi.stubGlobal('fetch', fetchMock)
  vi.resetModules()
  // @ts-expect-error -- a plain script, not a typed module
  await import('../../public/download/download.js')
  await vi.waitFor(() => {
    const state = visible('[data-field="state"]')
    expect(state && !/Reading|Lecture/.test(state.textContent ?? '')).toBe(true)
  })
  return fetchMock
}

function article(): HTMLElement {
  const lang = document.documentElement.getAttribute('data-lang')
  return document.querySelector(`article[data-lang="${lang}"]`)!
}

function visible(selector: string): HTMLElement | null {
  const el = article().querySelector<HTMLElement>(selector)
  return el && !el.closest('[hidden]') ? el : null
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the static page carries no release figure', () => {
  it('names no version, size or date anywhere in its markup', () => {
    const text = html.replace(/<script[\s\S]*?<\/script>/g, '')
    expect(text).not.toMatch(/\b\d+\.\d+\.\d+\b/) // no version
    expect(text).not.toMatch(/\d+([.,]\d)?\s?(MB|Mo)\b/) // no size
    expect(text).not.toContain('softwareVersion')
    expect(text).not.toMatch(/[0-9a-f]{64}/) // no hash
  })
})

describe('a verified release', () => {
  it('shows exactly what latest.json says, once the file is confirmed on the server', async () => {
    const fetchMock = await openPage({ status: 200, body: RELEASE })
    expect(visible('[data-field="version"]')!.textContent).toBe('0.1.1')
    expect(visible('[data-field="size"]')!.textContent).toBe('38.9 MB')
    expect(visible('[data-field="requires"]')!.textContent).toBe('Android 8.0 or newer')
    expect(visible('[data-field="sha256"]')!.textContent).toBe(RELEASE.sha256)
    expect(visible('[data-field="cert"]')!.textContent).toBe(RELEASE.cert_sha256)
    const link = visible('[data-field="download"]') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe(RELEASE.url)
    // the HEAD that confirms the file is really there
    expect(fetchMock).toHaveBeenCalledWith(RELEASE.url, expect.objectContaining({ method: 'HEAD' }))
    const ld = [...document.querySelectorAll('script[type="application/ld+json"]')]
      .map((s) => JSON.parse(s.textContent ?? '{}'))
      .find((d) => d['@type'] === 'MobileApplication')
    expect(ld).toMatchObject({
      softwareVersion: '0.1.1',
      operatingSystem: 'Android',
      downloadUrl: 'https://sosforge.soclose.co/download/sosforge-v0.1.1.apk',
      datePublished: '2026-10-27',
    })
    for (const invented of ['aggregateRating', 'review', 'interactionStatistic']) expect(ld).not.toHaveProperty(invented)
  })

  it('reads in French for a French browser', async () => {
    await openPage({ status: 200, body: RELEASE }, undefined, 'fr-FR')
    expect(document.documentElement.lang).toBe('fr')
    expect(visible('[data-field="size"]')!.textContent).toBe('38,9 Mo')
    expect(visible('[data-field="notes"]')!.textContent).toBe(RELEASE.notes.fr)
  })
})

describe('anything short of a verified release', () => {
  it('says the download is not available yet when there is no latest.json', async () => {
    await openPage({ status: 404 })
    expect(visible('[data-field="state"]')!.textContent).toMatch(/not available yet/)
    expect(visible('[data-field="download"]')).toBeNull()
    expect(visible('[data-field="version"]')).toBeNull()
    expect([...document.querySelectorAll('script[type="application/ld+json"]')].some((s) => s.textContent?.includes('MobileApplication'))).toBe(false)
  })

  it('holds the download back when the file is not the announced size', async () => {
    await openPage({ status: 200, body: RELEASE }, { status: 200, length: '12345' })
    expect(visible('[data-field="state"]')!.textContent).toMatch(/12345 bytes/)
    expect(visible('[data-field="download"]')).toBeNull()
  })

  it('holds the download back when latest.json is not the contract', async () => {
    await openPage({ status: 200, body: { ...RELEASE, sha256: 'not a hash' } })
    expect(visible('[data-field="state"]')!.textContent).toMatch(/malformed/)
    expect(visible('[data-field="download"]')).toBeNull()
  })
})
