/** Two themes, from the Sentinel House design language: Lunar (dark, the
 * default) and Soft (light, for a sunlit kitchen or a phone outdoors).
 *
 * The choice lives on `<html data-theme>` and in localStorage. Lunar is the
 * default whatever the system says: an emergency tracker is read at night in
 * a dark room more often than not, and the map is dark in both themes. The
 * reader picks Soft explicitly, from the header.
 */
export type Theme = 'lunar' | 'soft'

const STORAGE_KEY = 'sosforge.theme'

/** The browser chrome colour next to the page (address bar, task switcher):
 * `--e-fond` of each theme, as a literal because the meta tag cannot read a
 * CSS variable. */
const CHROME: Record<Theme, string> = { lunar: '#05070a', soft: '#f3ecdf' }

export function storedTheme(): Theme {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw === 'soft' ? 'soft' : 'lunar'
  } catch {
    return 'lunar'
  }
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme
  const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')
  if (meta) meta.content = CHROME[theme]
  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    /* private mode: the theme will not survive a reload, that is all */
  }
}

/** The theme the page is showing right now (what `applyTheme` last set). */
export function currentTheme(): Theme {
  return document.documentElement.dataset.theme === 'soft' ? 'soft' : 'lunar'
}

export function applyStoredTheme(): void {
  applyTheme(storedTheme())
}
