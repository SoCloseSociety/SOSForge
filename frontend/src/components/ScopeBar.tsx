import { useStore } from '../store'
import { effectiveScope } from '../scope'

/** Near me / World, the count that keeps far danger in sight, and the honest
 * state of the location request.
 *
 * The count is PERMANENT in Near me, zero included: a tsunami two oceans
 * away is exactly what a regional view would otherwise hide, and "nothing
 * severe elsewhere" is information too. Tapping it switches to World.
 */
export function ScopeBar({ severeElsewhere }: { severeElsewhere: number }) {
  const scope = useStore((s) => s.scope)
  const here = useStore((s) => s.here)
  const locate = useStore((s) => s.locate)
  const setScope = useStore((s) => s.setScope)
  const requestNearMe = useStore((s) => s.requestNearMe)
  // unknown until the snapshot is whole: before it, "nothing severe
  // elsewhere" would be a claim about events not received yet
  const loading = useStore((s) => s.snapshotRemaining > 0 || !s.received)
  const t = useStore((s) => s.t)
  const showing = effectiveScope(scope, here)

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

  return (
    // data-here: the coarse position the view is centred on, readable by the
    // browser drill (tools/location_e2e.py); the page holds it anyway
    <div className="scope-bar" data-here={here ? `${here.lat},${here.lon}` : undefined}>
      <div className="segmented scope-switch" role="group" aria-label={t('scope.label')}>
        <button type="button" aria-pressed={showing === 'near'} onClick={requestNearMe}>
          <span aria-hidden="true">⌖</span> {t('scope.near')}
        </button>
        <button type="button" aria-pressed={showing === 'world'} onClick={() => setScope('world')}>
          <span aria-hidden="true">◍</span> {t('scope.world')}
        </button>
      </div>
      {showing === 'near' ? (
        <button
          type="button"
          className={`scope-elsewhere${severeElsewhere > 0 ? ' has-severe' : ''}`}
          onClick={() => setScope('world')}
        >
          <span className="state-glyph" aria-hidden="true">
            {severeElsewhere > 0 ? '▲' : '○'}
          </span>
          <span>
            {/* while the snapshot arrives, the count is a floor ("2+"), and
                "nothing elsewhere" would be a claim about unseen events */}
            {severeElsewhere > 0
              ? t('scope.elsewhere', { n: `${severeElsewhere}${loading ? '+' : ''}` })
              : loading
                ? t('scope.elsewhere.loading')
                : t('scope.elsewhere.none')}
          </span>
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
