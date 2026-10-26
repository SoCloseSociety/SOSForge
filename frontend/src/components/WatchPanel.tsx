import { useEffect, useState } from 'react'
import { useStore } from '../store'
import { ALARM_RULES, alarmText, type Alarm } from '../alarm'
import { KIND_GLYPH, SEVERITY_META, kindLabel, severityLabel, type T } from '../format'
import { showNotification } from '../notify'
import type { Kind } from '../types'

/** Every kind that can raise a proximity alarm, in the rule table's order. */
const WATCHABLE: Kind[] = ALARM_RULES.map((rule) => rule.kind)

/** What the browser says about notifications, read fresh: a permission can
 * be revoked from the site settings while the tab is open. */
type NotifyState = 'unsupported' | 'default' | 'granted' | 'denied'

function readNotify(): NotifyState {
  if (typeof Notification === 'undefined') return 'unsupported'
  const permission = Notification.permission
  return permission === 'granted' || permission === 'denied' ? permission : 'default'
}

/** The rules for the chosen kinds, as sentences. Built from the same table
 * the alarm reads (`ALARM_RULES`), so what the panel promises is what fires. */
function RuleList({ t, kinds }: { t: T; kinds: readonly Kind[] }) {
  return (
    <ul className="watch-rule-list">
      {ALARM_RULES.filter((rule) => kinds.includes(rule.kind)).map((rule) => (
        <li key={rule.kind}>
          {rule.kind === 'earthquake'
            ? t('watch.what.felt', {
                kind: kindLabel(t, rule.kind),
                severity: severityLabel(t, rule.minSeverity),
              })
            : t('watch.what.reach', {
                kind: kindLabel(t, rule.kind),
                severity: severityLabel(t, rule.minSeverity),
                km: rule.reachKm,
              })}
        </li>
      ))}
    </ul>
  )
}

/** Location watch: alerts that concern where you actually are.
 *
 * A funnel, every step opt-in and saying what it does:
 *  1. the PLACE -- the browser asks for the location, and it never leaves the
 *     device (every distance is computed in this page; the server is never
 *     told where anyone is). The search bar arms a watch too;
 *  2. WHAT TO WATCH -- the kinds that may raise an alarm here, all on by
 *     default. Tsunamis cannot be switched off;
 *  3. NOTIFICATIONS -- why they are worth allowing, BEFORE the browser prompt:
 *     a prompt with no reason is a prompt people refuse, and a refusal is
 *     permanent in most browsers. Skipped when the browser already decided;
 *  4. the CONFIRMATION, with its honest states said in place: notifications
 *     blocked or unsupported, sound off, and the one no web page can escape
 *     -- a closed tab cannot alert anyone.
 *
 * This panel SHOWS the alarms; it does not ring them. The ringing lives in
 * `useWatchAlarms`, mounted at the top of the app, because this panel is not
 * mounted at all on a phone while the filter sheet is closed.
 */
export function WatchPanel({ alarms }: { alarms: Alarm[] }) {
  const watch = useStore((s) => s.watch)
  const setWatch = useStore((s) => s.setWatch)
  const soundOn = useStore((s) => s.soundOn)
  const loading = useStore((s) => s.snapshotRemaining > 0)
  const select = useStore((s) => s.select)
  const t = useStore((s) => s.t)

  const [locating, setLocating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notify, setNotify] = useState<NotifyState>(readNotify)
  /** Step 3 follows step 2 in the same visit only. After a reload the reader
   * lands on the confirmation, which still offers notifications inline: a
   * rationale shown again on every visit is nagging, not explaining. */
  const [askNow, setAskNow] = useState(false)
  /** "Change" on the confirmation reopens step 2 on the saved choice. */
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<Set<Kind> | null>(null)
  const [tested, setTested] = useState<'idle' | 'sent' | 'failed'>('idle')
  const [showRules, setShowRules] = useState(false)

  // A permission changed from the site settings must show up here without a
  // reload: re-read it whenever the tab comes back into view.
  useEffect(() => {
    const refresh = () => setNotify(readNotify())
    document.addEventListener('visibilitychange', refresh)
    return () => document.removeEventListener('visibilitychange', refresh)
  }, [])

  const locate = () => {
    if (!navigator.geolocation) {
      setError(t('watch.unavailable'))
      return
    }
    setLocating(true)
    setError(null)
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setWatch({
          lat: position.coords.latitude,
          lon: position.coords.longitude,
          name: t('watch.here'),
        })
        setLocating(false)
      },
      () => {
        // a refusal is a legitimate answer, not an error to shout about
        setError(t('watch.denied'))
        setLocating(false)
      },
      { enableHighAccuracy: false, timeout: 12000, maximumAge: 300000 },
    )
  }

  const askNotifications = async () => {
    if (typeof Notification === 'undefined') return
    try {
      const result = await Notification.requestPermission()
      setNotify(result === 'granted' || result === 'denied' ? result : 'default')
    } catch {
      setNotify(readNotify())
    }
    setAskNow(false)
  }

  const sendTest = async () => {
    const ok = await showNotification('SOSForge', {
      body: t('watch.notify.test.body'),
      tag: 'sosforge-test',
    })
    setTested(ok ? 'sent' : 'failed')
    setNotify(readNotify())
  }

  const clear = () => {
    setWatch(null)
    setAskNow(false)
    setEditing(false)
    setDraft(null)
    setTested('idle')
    setShowRules(false)
  }

  // ---------------------------------------------------------------- step 1
  if (!watch) {
    return (
      <section className="watch card" aria-labelledby="watch-title">
        <p className="overline">{t('watch.step', { n: 1 })}</p>
        <h2 className="card-title" id="watch-title">
          {t('watch.title')}
        </h2>
        <p className="watch-note">{t('watch.privacy')}</p>
        <div className="watch-actions">
          <button type="button" className="btn btn-primary" onClick={locate} disabled={locating}>
            <span aria-hidden="true">⌖</span>
            {locating ? t('watch.locating') : t('watch.use')}
          </button>
        </div>
        {/* No "or search above" line: the search bar sits right above this
            card, and a refused location already says the search works. The
            line cost the feed 30 px of the first screen on a laptop. */}
        {error ? (
          <p className="state state-att" role="status">
            <span className="state-glyph" aria-hidden="true">
              ▲
            </span>
            <span>{error}</span>
          </p>
        ) : null}
      </section>
    )
  }

  const head = (
    <div className="watch-head">
      <span className="watch-place">
        <span aria-hidden="true">⌖</span> {watch.name}
      </span>
      <button
        type="button"
        className="btn btn-quiet btn-icon"
        onClick={clear}
        aria-label={t('watch.clear')}
        title={t('watch.clear')}
      >
        ✕
      </button>
    </div>
  )

  // ---------------------------------------------------------------- step 2
  if (!watch.kinds || editing) {
    const chosen = draft ?? new Set<Kind>(watch.kinds ?? WATCHABLE)
    const toggle = (kind: Kind) => {
      if (kind === 'tsunami') return
      const next = new Set(chosen)
      if (next.has(kind)) next.delete(kind)
      else next.add(kind)
      setDraft(next)
    }
    const confirm = () => {
      // always kept, whatever the set says: see alwaysAlarms() in alarm.ts
      const final = new Set(chosen).add('tsunami')
      setWatch({ ...watch, kinds: WATCHABLE.filter((kind) => final.has(kind)) })
      setDraft(null)
      setEditing(false)
      setAskNow(readNotify() === 'default')
    }
    return (
      <section className="watch card" aria-labelledby="watch-title">
        <p className="overline">{t('watch.step', { n: 2 })}</p>
        {head}
        <h2 className="card-title" id="watch-title">
          {t('watch.what.title')}
        </h2>
        <p className="watch-note">{t('watch.what.summary')}</p>
        <div className="watch-kinds" role="group" aria-labelledby="watch-title">
          {WATCHABLE.map((kind) => {
            const locked = kind === 'tsunami'
            return (
              <button
                type="button"
                key={kind}
                className="chip"
                aria-pressed={locked || chosen.has(kind)}
                aria-disabled={locked || undefined}
                title={locked ? t('watch.what.locked') : undefined}
                onClick={() => toggle(kind)}
              >
                <span aria-hidden="true">{KIND_GLYPH[kind]}</span>
                {kindLabel(t, kind)}
              </button>
            )
          })}
        </div>
        <p className="watch-note">{t('watch.what.tsunami')}</p>
        <button
          type="button"
          className="btn btn-quiet btn-more"
          aria-expanded={showRules}
          onClick={() => setShowRules((v) => !v)}
        >
          {showRules ? t('more.hide') : t('more.show')}
        </button>
        {showRules ? <RuleList t={t} kinds={[...chosen, 'tsunami']} /> : null}
        <div className="watch-actions">
          <button type="button" className="btn btn-primary" onClick={confirm}>
            {t('watch.continue')}
          </button>
        </div>
      </section>
    )
  }

  // ---------------------------------------------------------------- step 3
  if (askNow && notify === 'default') {
    return (
      <section className="watch card" aria-labelledby="watch-title">
        <p className="overline">{t('watch.step', { n: 3 })}</p>
        {head}
        <h2 className="card-title" id="watch-title">
          {t('watch.notify.title')}
        </h2>
        <p className="watch-note">{t('watch.notify.why')}</p>
        <div className="watch-actions">
          <button type="button" className="btn btn-primary" onClick={askNotifications}>
            {t('watch.notify')}
          </button>
          <button type="button" className="btn btn-quiet" onClick={() => setAskNow(false)}>
            {t('watch.notify.skip')}
          </button>
        </div>
      </section>
    )
  }

  // ---------------------------------------------------------- confirmation
  return (
    <section className="watch card" aria-labelledby="watch-title">
      <p className="overline">{t('watch.watching')}</p>
      <div className="watch-head">
        <h2 className="card-title watch-place" id="watch-title">
          <span aria-hidden="true">⌖</span> {watch.name}
        </h2>
        <button
          type="button"
          className="btn btn-quiet"
          onClick={() => {
            setEditing(true)
            setShowRules(false)
          }}
        >
          {t('watch.change')}
        </button>
        <button
          type="button"
          className="btn btn-quiet btn-icon"
          onClick={clear}
          aria-label={t('watch.clear')}
          title={t('watch.clear')}
        >
          ✕
        </button>
      </div>

      {alarms.length === 0 && loading ? (
        // "nothing concerns this area" is a claim about EVERY event: not
        // made while part of the snapshot has not arrived yet
        <p className="state" role="status">
          <span className="state-glyph" aria-hidden="true">
            ○
          </span>
          <span>{t('watch.checking')}</span>
        </p>
      ) : alarms.length === 0 ? (
        <p className="state state-ok">
          <span className="state-glyph" aria-hidden="true">
            ✓
          </span>
          <span>{t('watch.quiet')}</span>
        </p>
      ) : (
        <ul className="watch-alarms">
          {alarms.slice(0, 4).map((alarm) => (
            <li key={alarm.event.id}>
              <button type="button" onClick={() => select(alarm.event.id)}>
                <span className={`sev sev-${alarm.event.severity}`}>
                  <span className="glyph" aria-hidden="true">
                    {SEVERITY_META[alarm.event.severity].glyph}
                  </span>
                  {severityLabel(t, alarm.event.severity)}
                </span>
                <span className="watch-alarm-text">
                  <strong>{kindLabel(t, alarm.event.kind)}</strong> {alarmText(alarm)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* The honest states, in place, one line each. */}
      <ul className="watch-states">
        {notify === 'granted' ? (
          <li className="state state-ok">
            <span className="state-glyph" aria-hidden="true">
              ✓
            </span>
            <span>{t('watch.notify.granted')}</span>
            <button
              type="button"
              className="btn btn-quiet btn-inline"
              onClick={() => void sendTest()}
              disabled={tested === 'sent'}
            >
              {tested === 'sent' ? t('watch.notify.tested') : t('watch.notify.test')}
            </button>
          </li>
        ) : notify === 'denied' ? (
          <li className="state state-att">
            <span className="state-glyph" aria-hidden="true">
              ▲
            </span>
            <span>{t('watch.notify.denied')}</span>
          </li>
        ) : notify === 'unsupported' ? (
          <li className="state state-att">
            <span className="state-glyph" aria-hidden="true">
              ▲
            </span>
            <span>{t('watch.notify.unsupported')}</span>
          </li>
        ) : (
          <li className="state">
            <span className="state-glyph" aria-hidden="true">
              ○
            </span>
            <button type="button" className="btn btn-secondary btn-inline" onClick={askNotifications}>
              {t('watch.notify')}
            </button>
          </li>
        )}
        {tested === 'failed' ? (
          <li className="state state-att" role="status">
            <span className="state-glyph" aria-hidden="true">
              ▲
            </span>
            <span>{t('watch.notify.failed')}</span>
          </li>
        ) : null}
        {!soundOn ? (
          <li className="state state-att">
            <span className="state-glyph" aria-hidden="true">
              ▲
            </span>
            <span>{t('watch.sound.off')}</span>
          </li>
        ) : null}
        <li className="state">
          <span className="state-glyph" aria-hidden="true">
            ○
          </span>
          <span>
            {t('watch.closed')} {t('watch.android')}
          </span>
        </li>
      </ul>
      <button
        type="button"
        className="btn btn-quiet btn-more"
        aria-expanded={showRules}
        onClick={() => setShowRules((v) => !v)}
      >
        {showRules ? t('more.hide') : t('more.show')}
      </button>
      {showRules ? <RuleList t={t} kinds={watch.kinds} /> : null}
    </section>
  )
}
