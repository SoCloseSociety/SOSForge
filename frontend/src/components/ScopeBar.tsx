import { useStore } from '../store'
import { effectiveScope } from '../scope'

/** Near me / World, the count that keeps far danger in sight, and the honest
 * state of the location request.
 *
 * The count is PERMANENT in Near me, zero included: a tsunami two oceans
 * away is exactly what a regional view would otherwise hide, and "nothing
 * severe elsewhere" is information too. Tapping it switches to World.
 *
 * What is severe and carries no position at all is counted apart, "N without
 * a location": calling it "elsewhere" claimed a place we do not know (review
 * of PR #5).
 *
 * Both counts are ONE control, because both open World. The far-danger part
 * is permanent, zero included. The unlocated part keeps its own "?" glyph
 * and its full label, and shows only when there is one. On a phone the
 * control takes one line per count when the label fits, and a longer label
 * wraps inside it (344 px, French at 360): two pills beside the switch
 * made a three-row, 173 px bar, and no event was left above the fold of a
 * 320x568 phone in Near me (tools/responsive.py --near, 2026-10-27).
 */
export function ScopeBar({
  severeElsewhere,
  unlocated = 0,
}: {
  severeElsewhere: number
  unlocated?: number
}) {
  const scope = useStore((s) => s.scope)
  const here = useStore((s) => s.here)
  const place = useStore((s) => s.place)
  const setPlace = useStore((s) => s.setPlace)
  const locate = useStore((s) => s.locate)
  const setScope = useStore((s) => s.setScope)
  const requestNearMe = useStore((s) => s.requestNearMe)
  // unknown until the snapshot is whole: before it, "nothing severe
  // elsewhere" would be a claim about events not received yet
  const loading = useStore((s) => s.snapshotRemaining > 0 || !s.received)
  // the link is down: "nothing severe elsewhere" is not known any more
  const offline = useStore((s) => s.received && !s.connected)
  const t = useStore((s) => s.t)
  const showing = effectiveScope(scope, here, place)

  // Why the world is showing although the reader wants Near me: said in
  // place, once, without a prompt of its own.
  const reason =
    scope === 'near' && showing === 'world'
      ? locate === 'asking'
        ? t('scope.asking')
        : locate === 'locating'
          ? t('scope.locating')
          : locate === 'denied'
            ? t('scope.denied')
            : locate === 'timeout'
              ? t('scope.timeout')
              : locate === 'insecure'
                ? t('scope.insecure')
                : locate === 'unavailable'
                  ? t('scope.unavailable')
                  : null
      : null

  // while the snapshot arrives, the count is a floor ("2+"), and "nothing
  // elsewhere" would be a claim about unseen events
  const elsewhereLabel =
    severeElsewhere > 0
      ? t('scope.elsewhere', { n: `${severeElsewhere}${loading ? '+' : ''}` })
      : loading
        ? t('scope.elsewhere.loading')
        : offline
          ? t('scope.elsewhere.offline')
          : t('scope.elsewhere.none')
  const unlocatedLabel = unlocated > 0 ? t('scope.unlocated', { n: `${unlocated}${loading ? '+' : ''}` }) : null

  return (
    // data-here: the coarse position the view is centred on, readable by the
    // browser drill (tools/location_e2e.py); the page holds it anyway
    <div
      className="scope-bar"
      data-here={here ? `${here.lat},${here.lon}` : undefined}
      data-place={place ? `${place.lat},${place.lon}` : undefined}
    >
      <div className="segmented scope-switch" role="group" aria-label={t('scope.label')}>
        <button type="button" aria-pressed={showing === 'near'} onClick={requestNearMe}>
          <span aria-hidden="true">⌖</span> {t('scope.near')}
        </button>
        <button type="button" aria-pressed={showing === 'world'} onClick={() => setScope('world')}>
          <span aria-hidden="true">◍</span> {t('scope.world')}
        </button>
        {/* the place picked in the search, named: the feed is around it */}
        {place ? (
          <button
            type="button"
            className="scope-place"
            aria-pressed={showing === 'place'}
            onClick={() => setScope('place')}
          >
            <span aria-hidden="true">⌖</span> <span className="scope-place-name">{place.name}</span>
          </button>
        ) : null}
      </div>
      {place ? (
        <button
          type="button"
          className="btn btn-quiet btn-icon"
          aria-label={t('scope.place.clear', { place: place.name })}
          title={t('scope.place.clear', { place: place.name })}
          onClick={() => setPlace(null)}
        >
          ✕
        </button>
      ) : null}
      {showing !== 'world' ? (
        <button
          type="button"
          className={`scope-elsewhere${severeElsewhere > 0 || unlocated > 0 ? ' has-severe' : ''}`}
          // Two counts, heard apart: "2 severe elsewhere, 1 severe without a
          // location". The name is the two visible labels and the separator,
          // in this order. A visually hidden ", " between the parts was
          // measured: Chrome named it "17 severe elsewhere , 9 ..." (it pads
          // the inline-flex parts), jsdom "elsewhere,1 severe" (it trims each
          // part), 2026-10-27.
          aria-label={unlocatedLabel ? `${elsewhereLabel}${t('scope.counts.sep')}${unlocatedLabel}` : undefined}
          onClick={() => setScope('world')}
        >
          <span className={`scope-count${severeElsewhere > 0 ? ' has-severe' : ''}`}>
            <span className="state-glyph" aria-hidden="true">
              {severeElsewhere > 0 ? '▲' : '○'}
            </span>
            <span>{elsewhereLabel}</span>
          </span>
          {unlocatedLabel ? (
            <span className="scope-count has-severe">
              <span className="state-glyph" aria-hidden="true">
                ?
              </span>
              <span>{unlocatedLabel}</span>
            </span>
          ) : null}
        </button>
      ) : null}
      {reason ? (
        <p className="scope-reason" role="status">
          {reason}
        </p>
      ) : null}
    </div>
  )
}
