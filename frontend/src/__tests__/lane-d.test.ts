/** Lane D (2026-10): the theme switch, the degraded-source detection and the
 * notification path. Small modules, each with a way to go wrong that a reader
 * would only find in the field. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { applyTheme, currentTheme, storedTheme } from '../theme'
import { degradedFeeds, hasIssue } from '../sourceHealth'
import { showNotification } from '../notify'
import { SEVERITY_META } from '../format'
import type { SourceHealth } from '../types'

describe('theme', () => {
  beforeEach(() => {
    localStorage.clear()
    document.head.innerHTML = '<meta name="theme-color" content="#05070a">'
    delete document.documentElement.dataset.theme
  })

  it('is Lunar unless the reader chose Soft, whatever the system says', () => {
    expect(storedTheme()).toBe('lunar')
    localStorage.setItem('sosforge.theme', 'nonsense')
    expect(storedTheme()).toBe('lunar')
  })

  it('applies, remembers, and repaints the browser chrome', () => {
    applyTheme('soft')
    expect(document.documentElement.dataset.theme).toBe('soft')
    expect(currentTheme()).toBe('soft')
    expect(storedTheme()).toBe('soft')
    expect(document.querySelector('meta[name="theme-color"]')?.getAttribute('content')).toBe('#f3ecdf')
    applyTheme('lunar')
    expect(document.querySelector('meta[name="theme-color"]')?.getAttribute('content')).toBe('#05070a')
  })
})

describe('severity glyphs', () => {
  it('give every rank a glyph of its own shape', () => {
    const glyphs = Object.values(SEVERITY_META).map((m) => m.glyph)
    expect(new Set(glyphs).size).toBe(glyphs.length)
    // the two triangles that used to be moderate and severe
    expect(glyphs.filter((g) => g === '▲' || g === '⚠')).toHaveLength(1)
  })
})

describe('a degraded source', () => {
  const base: SourceHealth = {
    name: 'tsunami',
    connected: true,
    last_ok: null,
    last_error: null,
    events_seen: 3,
    errors: 0,
  }

  it('an API without the field reads as not degraded, as it did before', () => {
    expect(degradedFeeds(base)).toBeNull()
    expect(hasIssue(base)).toBe(false)
  })

  it.each([
    [{ degraded: true, failed_feeds: ['PHEB'] }, ['PHEB']],
    [{ degraded: ['PHEB', 'PAAQ'] }, ['PHEB', 'PAAQ']],
    [{ degraded: { failed: ['PAAQ'] } }, ['PAAQ']],
    [{ degraded: true }, []],
    [{ failed_feeds: ['PHEB'] }, ['PHEB']],
  ])('reads %j', (extra, feeds) => {
    const source = { ...base, ...extra } as SourceHealth
    expect(degradedFeeds(source)).toEqual(feeds)
    expect(hasIssue(source)).toBe(true)
  })

  it('a dead source with an error is an issue, a dead one without is not detailed', () => {
    expect(hasIssue({ ...base, connected: false, last_error: 'HTTP 503' })).toBe(true)
    expect(hasIssue({ ...base, connected: false })).toBe(false)
  })
})

describe('showNotification', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('goes through the service worker when there is one (Chrome on Android refuses the constructor)', async () => {
    const shown: unknown[] = []
    class NoConstructor {
      static permission = 'granted'
      constructor() {
        throw new TypeError('Illegal constructor')
      }
    }
    vi.stubGlobal('Notification', NoConstructor)
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: {
        getRegistration: async () => ({
          showNotification: async (title: string, options: unknown) => shown.push([title, options]),
        }),
      },
    })
    expect(await showNotification('Earthquakes -- Tokyo', { body: 'M6', tag: 'e1', url: '/#e/e1' })).toBe(true)
    expect(shown).toEqual([['Earthquakes -- Tokyo', { body: 'M6', tag: 'e1', data: { url: '/#e/e1' } }]])
    delete (navigator as unknown as Record<string, unknown>).serviceWorker
  })

  it('says it failed instead of pretending, when nothing can show it', async () => {
    class NoConstructor {
      static permission = 'granted'
      constructor() {
        throw new TypeError('Illegal constructor')
      }
    }
    vi.stubGlobal('Notification', NoConstructor)
    expect(await showNotification('x', { body: 'y', tag: 'z' })).toBe(false)
  })

  it('shows nothing without the permission', async () => {
    const made = vi.fn()
    class Denied {
      static permission = 'denied'
      constructor() {
        made()
      }
    }
    vi.stubGlobal('Notification', Denied)
    expect(await showNotification('x', { body: 'y', tag: 'z' })).toBe(false)
    expect(made).not.toHaveBeenCalled()
  })
})
