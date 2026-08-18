import { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'

interface Place {
  name: string
  lat: number
  lon: number
  type: string | null
  bbox: number[] | null
}

/** Area search.
 *
 * It deliberately does two things, because the user doesn't always know
 * which one they want: typing "Tokyo" immediately **filters** the feed to
 * whatever mentions Tokyo (instant, local), and offers below to **go to**
 * Tokyo on the map, even if no event is currently happening there. The
 * second case is the more useful one in a real situation: you want to look
 * at a specific area.
 *
 * Geocoding goes through our backend (`/api/geocode`), which keeps to the
 * one-request-per-second rate imposed by Nominatim -- a direct call from
 * each tab would violate it.
 */
export function SearchBar() {
  const query = useStore((s) => s.filters.query)
  const setQuery = useStore((s) => s.setQuery)
  const setFocus = useStore((s) => s.setFocus)
  const t = useStore((s) => s.t)

  const [places, setPlaces] = useState<Place[] | null>(null)
  const [open, setOpen] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const term = query.trim()
    // Clear on EVERY input change: otherwise, during the 450 ms wait plus
    // network time, pressing Enter right after typing "lyon" would still go
    // to the results for "paris".
    setPlaces(null)
    setOpen(false)
    if (term.length < 3) {
      return
    }
    // debounce: we don't geocode on every keystroke, we wait for typing to
    // settle. Local filtering, on the other hand, stays instant.
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      fetch(`/api/geocode?q=${encodeURIComponent(term)}`, { signal: controller.signal })
        .then((r) => r.json())
        .then((data: { results: Place[] }) => {
          setPlaces(data.results ?? [])
          setOpen(true)
        })
        .catch(() => setPlaces(null))
    }, 450)

    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [query])

  const goTo = (place: Place) => {
    // a city is viewed up close, a country from afar: the bbox returned by
    // Nominatim tells us which of the two was just requested
    let zoom = 9
    if (place.bbox && place.bbox.length === 4) {
      const [south, north, west, east] = place.bbox
      const span = Math.max(Math.abs(north - south), Math.abs(east - west))
      zoom = span > 20 ? 3 : span > 5 ? 5 : span > 1 ? 7 : 10
    }
    setFocus({ lat: place.lat, lon: place.lon, zoom, name: place.name })
    setOpen(false)
    inputRef.current?.blur()
  }

  return (
    <div className="search">
      <span className="search-icon" aria-hidden="true">
        ⌕
      </span>
      <input
        ref={inputRef}
        type="search"
        value={query}
        placeholder={t('search.placeholder')}
        aria-label={t('search.placeholder')}
        onChange={(e) => setQuery(e.target.value)}
        onFocus={() => places && setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            setOpen(false)
            if (!query) inputRef.current?.blur()
          }
          if (e.key === 'Enter' && places?.length) goTo(places[0])
        }}
      />
      {query ? (
        <button
          type="button"
          className="search-clear"
          onClick={() => {
            setQuery('')
            setPlaces(null)
          }}
          aria-label={t('detail.close')}
        >
          ✕
        </button>
      ) : null}

      {open && places ? (
        <ul className="search-results">
          {places.length === 0 ? (
            <li className="search-none">{t('search.none')}</li>
          ) : (
            places.slice(0, 5).map((place) => (
              <li key={`${place.lat},${place.lon}`}>
                <button type="button" onClick={() => goTo(place)}>
                  <strong>{t('search.goto')}</strong>
                  <span>{place.name}</span>
                </button>
              </li>
            ))
          )}
        </ul>
      ) : null}
    </div>
  )
}
