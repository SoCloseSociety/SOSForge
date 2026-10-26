import { useEffect, useState } from "react";
import { eventUrl } from "../deeplink";
import { currentPhase, guidanceFor } from "../guidance";
import { useStore } from "../store";
import {
  SEVERITY_META,
  SOURCE_LABEL,
  countryName,
  flagEmoji,
  formatAge,
  kindLabel,
  severityLabel,
} from "../format";
import type {
  GuidanceLine,
  GuidancePhase as Phase,
  KindGuidance,
  SosEvent,
} from "../types";

/** A translated string for a server-provided link, or the server's own text
 * when the dictionary has no entry (`t` returns the key itself in that case). */
function linkText(t: (key: string) => string, key: string, fallback: string): string {
  const value = t(key);
  return value === key ? fallback : value;
}

interface NearbyLink {
  id: string;
  label: string;
  detail: string;
  url: string;
}

interface Camera {
  id: string;
  title: string;
  city: string | null;
  country: string | null;
  status: string | null;
  thumbnail: string | null;
  url: string | null;
}

interface Nearby {
  found: boolean;
  links: NearbyLink[];
  cameras: Camera[];
  cameras_configured?: boolean;
}

/** One phase of the prevention guidance: a few lines, each with the official
 * page it comes from. The link is part of the line, not a footnote: this
 * product relays authorities, it does not give advice of its own. */
function GuidancePhase({
  title,
  lines,
  sourceLabel,
}: {
  title: string;
  lines: GuidanceLine[];
  sourceLabel: string;
}) {
  if (lines.length === 0) return null;
  return (
    <div className="guidance-phase">
      <h5>{title}</h5>
      <ul>
        {lines.map((line, index) => (
          <li key={`${line.source_url}-${index}`}>
            <span>{line.text}</span>{" "}
            <a href={line.source_url} target="_blank" rel="noreferrer">
              {line.source ? line.source : sourceLabel} ↗
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}

const PHASES: Phase[] = ["before", "during", "after"];

/** Before / during / after, opened on the phase that matters NOW for this
 * event (`currentPhase`): a warning ahead of its onset opens on "before", a
 * running alert on "during", an old quake on "after". The other two phases
 * wait behind "Show more" -- three lists at once buried the one that applies. */
function Guidance({
  guidance,
  event,
  now,
}: {
  guidance: KindGuidance;
  event: SosEvent;
  now: number;
}) {
  const t = useStore((s) => s.t);
  const [all, setAll] = useState(false);
  const present = PHASES.filter((phase) => guidance[phase].length > 0);
  if (present.length === 0) return null;
  const wanted = currentPhase(event, now);
  const first = present.includes(wanted) ? wanted : present[0];
  const rest = present.filter((phase) => phase !== first);
  const shown = all ? [first, ...rest] : [first];
  return (
    <section className="live-guidance" aria-labelledby="live-guidance-title">
      <h4 id="live-guidance-title">{t("guidance.title")}</h4>
      {shown.map((phase) => (
        <GuidancePhase
          key={phase}
          title={t(`guidance.${phase}`)}
          lines={guidance[phase]}
          sourceLabel={t("guidance.source")}
        />
      ))}
      {rest.length > 0 ? (
        <button
          type="button"
          className="btn btn-quiet btn-more"
          aria-expanded={all}
          onClick={() => setAll((v) => !v)}
        >
          {all ? t("more.hide") : t("more.show")}
        </button>
      ) : null}
    </section>
  );
}

/** Card for the selected event + access to live views of the area.
 *
 * Links are computed server-side and work without any key. Webcams only
 * show up if a Windy key is configured: the absence of a key must not give
 * the impression the area has nothing to show, hence the explicit message.
 */
export function LivePanel({ event, now }: { event: SosEvent; now: number }) {
  const t = useStore((s) => s.t);
  const lang = useStore((s) => s.lang);
  const select = useStore((s) => s.select);
  const [nearby, setNearby] = useState<Nearby | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  /** Prevention guidance for this kind, when the API serves it. `null` is
   * the ordinary case on an API without the feature, and the panel then
   * shows exactly what it showed before the feature existed. */
  const [guidance, setGuidance] = useState<KindGuidance | null>(null);

  useEffect(() => {
    if (event.lat === null || event.lon === null) {
      setNearby(null);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    // The previous event's answer describes the previous event. Keeping it on
    // screen while the new one loads would attribute one area's webcams to
    // another -- so the panel goes back to "loading" and says nothing it
    // cannot yet source.
    setNearby(null);
    setLoading(true);
    fetch(`/api/events/${encodeURIComponent(event.id)}/nearby`, {
      signal: controller.signal,
    })
      .then((r) => r.json())
      .then((data: Nearby) => setNearby(data))
      // An abort rejects on the NEXT microtask, by which time the effect for
      // the new selection has already set `loading`. Without this guard the
      // dead request's handlers wrote over the live one's state, the render
      // fell through to the non-loading branch with `nearby === null`, and the
      // panel announced "no Windy key configured" -- a statement about the
      // deployment -- for the whole duration of a request that was running
      // perfectly well.
      .catch(() => {
        if (!controller.signal.aborted) setNearby(null);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [event.id, event.lat, event.lon]);

  // Guidance is per kind and per language, cached in guidance.ts: switching
  // between two earthquakes costs nothing. A stale answer for a previous kind
  // must not land on this one, hence the cancellation flag.
  useEffect(() => {
    let cancelled = false;
    setGuidance(null);
    void guidanceFor(lang, event.kind).then((found) => {
      if (!cancelled) setGuidance(found);
    });
    return () => {
      cancelled = true;
    };
  }, [lang, event.kind]);

  const severity = SEVERITY_META[event.severity];

  return (
    <aside className="live-panel" aria-label={event.place || event.title}>
      <header>
        <div className="live-head">
          <p className="overline">
            {kindLabel(t, event.kind)}
            {countryName(lang, event.country_code)
              ? ` · ${countryName(lang, event.country_code)}`
              : ""}
          </p>
          <h3>
            <span className="flag" aria-hidden="true">
              {flagEmoji(event.country_code) ?? "🌐"}
            </span>{" "}
            {event.place || event.title}
          </h3>
          <p className="live-meta">
            <span className={`sev sev-${event.severity}`}>
              <span className="glyph" aria-hidden="true">{severity.glyph}</span>{" "}
              {severityLabel(t, event.severity)}
            </span>
            {" · "}
            {formatAge(t, (now - Date.parse(event.time)) / 1000)}
          </p>
        </div>
        <span className="live-actions">
          <button
            type="button"
            className="btn btn-quiet btn-icon"
            onClick={() => {
              void navigator.clipboard?.writeText(eventUrl(event.id));
              setCopied(true);
              window.setTimeout(() => setCopied(false), 2000);
            }}
            title={t("detail.share")}
            aria-label={t("detail.share")}
          >
            {copied ? "✓" : "⧉"}
          </button>
          <button
            type="button"
            className="btn btn-quiet btn-icon"
            onClick={() => select(null)}
            aria-label={t("detail.close")}
          >
            ✕
          </button>
        </span>
      </header>

      {/* ABOVE everything else, deliberately. This is what the issuing agency
          asked people to do, and someone reading this panel while inside the
          warning area needs it before the magnitude, before the depth, before
          the source. Rendered as a text node -- never as markup: it is text
          from feeds we do not control. */}
      {event.instruction ? (
        <section className="live-todo">
          <h4>{t("detail.todo")}</h4>
          <p>{event.instruction}</p>
        </section>
      ) : null}

      {/* The only MEASUREMENT in a tsunami bulletin: a gauge saw this. It is
          the difference between a cancelled advisory and Tohoku. */}
      {event.wave_max_m !== null ? (
        <section className="live-wave">
          <h4>{t("detail.wave")}</h4>
          <p>
            <strong>{event.wave_max_m.toFixed(2)} m</strong>
            {event.wave_max_site ? ` -- ${event.wave_max_site}` : ""}
          </p>
        </section>
      ) : null}

      {event.wave_eta ? (
        <p className="live-eta">
          {t("detail.eta")}: <strong>{event.wave_eta}</strong>
          {event.wave_eta_site ? ` -- ${event.wave_eta_site}` : ""}
        </p>
      ) : null}

      <dl className="live-facts">
        {event.magnitude !== null ? (
          <>
            <dt>{t("detail.magnitude")}</dt>
            <dd>
              {event.magnitude} {event.mag_type ?? ""}
            </dd>
          </>
        ) : null}
        {event.depth_km !== null ? (
          <>
            <dt>{t("detail.depth")}</dt>
            <dd>{Math.round(event.depth_km)} km</dd>
          </>
        ) : null}
        <dt>{t("detail.time")}</dt>
        <dd>{event.time.slice(11, 19)}</dd>
        {/* Two different measurements, never averaged: one is what people
            reported feeling, the other is what the model estimates. */}
        {event.intensity_cdi !== null ? (
          <>
            <dt>{t("detail.felt.reported")}</dt>
            <dd>{event.intensity_cdi}</dd>
          </>
        ) : null}
        {event.intensity_mmi !== null ? (
          <>
            <dt>{t("detail.felt.modelled")}</dt>
            <dd>{event.intensity_mmi}</dd>
          </>
        ) : null}
        <dt>{t("detail.source")}</dt>
        <dd>{SOURCE_LABEL[event.source] ?? event.source}</dd>
      </dl>

      {/* Below the instruction, and collapsed: it is context, not an
          instruction, and it can run to a thousand characters. */}
      {event.description ? (
        <details className="live-about">
          <summary>{t("detail.about")}</summary>
          <p>{event.description}</p>
        </details>
      ) : null}

      {event.url ? (
        <a
          className="live-official"
          href={event.url}
          target="_blank"
          rel="noreferrer"
        >
          {t("detail.official")} ↗
        </a>
      ) : null}

      {/* Prevention, when the API serves it (feature-detected in
          guidance.ts), under the official instruction: the instruction is
          about THIS alert, the guidance is about the hazard in general. An
          API without the feature leaves this out and the panel is exactly
          what it was before. Each line carries the authority it comes from. */}
      {guidance ? (
        <Guidance key={event.id} guidance={guidance} event={event} now={now} />
      ) : null}

      <h4>{t("live.title")}</h4>
      {event.lat === null || event.lon === null ? (
        <p className="live-note">{t("live.nocoords")}</p>
      ) : loading && !nearby ? (
        <p className="live-note">{t("live.loading")}</p>
      ) : (
        <>
          <ul className="live-links">
            {(nearby?.links ?? []).map((link) => (
              <li key={link.id}>
                <a href={link.url} target="_blank" rel="noreferrer">
                  {/* The server labels its links in English; the dictionary
                      carries the four it knows, keyed by id, so a French
                      panel no longer mixes "Satellite imagery" under a
                      French heading. An unknown id keeps the server text. */}
                  <strong>{linkText(t, `live.link.${link.id}`, link.label)}</strong>
                  <span>{linkText(t, `live.link.${link.id}.detail`, link.detail)}</span>
                </a>
              </li>
            ))}
          </ul>

          <h4>{t("live.cameras")}</h4>
          {nearby?.cameras?.length ? (
            <ul className="live-cams">
              {nearby.cameras.map((cam) => (
                <li key={cam.id}>
                  <a href={cam.url ?? "#"} target="_blank" rel="noreferrer">
                    {cam.thumbnail ? (
                      <img src={cam.thumbnail} alt="" loading="lazy" />
                    ) : null}
                    <span>{cam.title}</span>
                  </a>
                </li>
              ))}
            </ul>
          ) : (
            <p className="live-note">
              {nearby?.cameras_configured
                ? t("live.nocamera")
                : t("live.nokey")}
            </p>
          )}
        </>
      )}
    </aside>
  );
}
