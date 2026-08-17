import { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'

interface Place {
  name: string
  lat: number
  lon: number
  type: string | null
  bbox: number[] | null
}

/** Recherche de zone.
 *
 * Elle fait deux choses volontairement, parce que l'utilisateur ne sait pas
 * toujours laquelle il veut: taper "Tokyo" **filtre** immediatement le flux sur
 * ce qui mentionne Tokyo (instantane, local), et propose en dessous d'**aller**
 * a Tokyo sur la carte, meme si aucun evenement n'y est en cours. Le second cas
 * est le plus utile en situation reelle: on veut regarder une zone precise.
 *
 * Le geocodage passe par notre backend (`/api/geocode`), qui tient la cadence
 * d'une requete par seconde imposee par Nominatim -- un appel direct depuis
 * chaque onglet la violerait.
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
    // Vider a CHAQUE changement de saisie: sinon, pendant les 450 ms d'attente
    // puis le temps du reseau, presser Entree apres avoir tape "lyon" partait
    // encore vers les resultats de "paris".
    setPlaces(null)
    setOpen(false)
    if (term.length < 3) {
      return
    }
    // debounce: on ne geocode pas a chaque frappe, on attend que la saisie se
    // pose. Le filtrage local, lui, reste instantane.
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
    // une ville se regarde de pres, un pays de loin: la bbox renvoyee par
    // Nominatim dit laquelle des deux on vient de demander
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
