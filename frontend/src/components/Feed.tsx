import { useEffect, useRef, useState, type JSX } from "react";
import { useStore } from "../store";
import {
  SEVERITY_META,
  SOURCE_LABEL,
  badge,
  countryName,
  flagEmoji,
  formatAge,
  kindLabel,
  severityLabel,
} from "../format";
import type { SosEvent } from "../types";

interface Props {
  events: SosEvent[];
  /** current server timestamp, refreshed every second */
  now: number;
  emptyKey: string;
}

function Row({ event, now }: { event: SosEvent; now: number }) {
  const selected = useStore((s) => s.selected === event.id);
  const fresh = useStore((s) => s.fresh.has(event.id));
  const select = useStore((s) => s.select);
  const t = useStore((s) => s.t);
  const lang = useStore((s) => s.lang);
  const severity = SEVERITY_META[event.severity];
  const flag = flagEmoji(event.country_code);
  const chip = badge(event);
  const age = (now - Date.parse(event.time)) / 1000;

  return (
    <button
      type="button"
      className={`item${fresh ? " fresh" : ""}`}
      // `aria-selected` is only valid on option/tab/row/gridcell: on a button
      // screen readers ignore it, so opening an event announced no state at
      // all. `aria-pressed` is the toggle that this actually is.
      aria-pressed={selected}
      onClick={() => select(selected ? null : event.id)}
    >
      <span className="mag" style={{ color: severity.text }}>
        {chip.value}
        {chip.unit ? <small>{chip.unit}</small> : null}
      </span>

      <span>
        <span className="place">
          <span
            className="flag"
            title={countryName(lang, event.country_code) ?? ""}
          >
            {flag ?? "🌐"}
          </span>
          {event.place || event.title}
        </span>
        <span className="meta">
          <span className={`sev sev-${event.severity}`}>
            <span className="glyph" aria-hidden="true">
              {severity.glyph}
            </span>
            {severityLabel(t, event.severity)}
          </span>
          <span className="tag">{kindLabel(t, event.kind)}</span>
          <span>{SOURCE_LABEL[event.source] ?? event.source}</span>
          {/* What to DO, in one word. A closed CAP vocabulary carried by
              essentially every NWS and Meteoalarm alert -- the only one of the
              new fields dense enough to earn a place in the list, and the only
              thing here that tells a reader inside the warning area what is
              being asked of them. */}
          {/* Only the vocabulary the dictionary knows. CAP also carries
              "None" and "AllClear", and a server that let one through
              printed the raw key `response.none` on every row concerned. */}
          {event.response_type && RESPONSE_GLYPH[event.response_type] ? (
            <span
              className={`tag tag-do tag-do-${event.response_type.toLowerCase()}`}
            >
              {RESPONSE_GLYPH[event.response_type]}{" "}
              {t(`response.${event.response_type.toLowerCase()}`)}
            </span>
          ) : null}
          {/* Say it before the number is believed. An automatic solution
              minutes old is routinely off by up to a magnitude unit, and a
              screenshot of the wrong figure travels much further than the
              correction. "Revised" after the fact is not the same promise. */}
          {event.preliminary ? (
            <span
              className="tag tag-preliminary"
              title={t("tag.preliminary.why")}
            >
              {t("tag.preliminary")}
            </span>
          ) : null}
          {event.revision > 0 ? (
            <span className="tag">{t("map.revised")}</span>
          ) : null}
          {event.felt_reports ? (
            <span className="tag" title={t("tag.felt.why")}>
              {t("tag.felt", { n: event.felt_reports })}
            </span>
          ) : null}
        </span>
      </span>

      <span className="age">{formatAge(t, age)}</span>
    </button>
  );
}

/** Says ONE sentence when an event arrives, and is otherwise silent.
 *
 * Separated from the list on purpose: the list's rows carry ages that change
 * every second, and a live region wrapping them kept the polite queue
 * permanently saturated with "14 s ago... 15 s ago", drowning the very
 * announcements it existed for.
 */
function NewEventAnnouncer({ events }: { events: SosEvent[] }): JSX.Element {
  const t = useStore((s) => s.t);
  const newest = events[0];
  const [announced, setAnnounced] = useState<string | null>(null);
  const spoken = useRef<string | null>(null);

  useEffect(() => {
    if (!newest || spoken.current === newest.id) return;
    spoken.current = newest.id;
    setAnnounced(
      t("a11y.newevent", {
        kind: t(`kind.${newest.kind}`),
        place: newest.place,
        severity: t(`sev.${newest.severity}`),
      }),
    );
  }, [newest, t]);

  return (
    <p className="sr-only" role="status" aria-live="polite">
      {announced}
    </p>
  );
}

/** The CAP responseType vocabulary, glyphed. Colour never carries this alone:
 * the word is always next to it. */
const RESPONSE_GLYPH: Record<string, string> = {
  Shelter: "🏠",
  Evacuate: "🚸",
  Avoid: "⛔",
  Prepare: "🎒",
  Execute: "▶",
  Monitor: "👁",
  Assess: "📋",
};

export function Feed({ events, now, emptyKey }: Props) {
  const t = useStore((s) => s.t);
  if (events.length === 0) {
    return <div className="feed-empty">{t(emptyKey)}</div>;
  }
  return (
    // NO aria-live here, and no role="feed".
    //
    // Every row carries an age that re-renders on the one-second tick, so a
    // live region wrapping the list kept the polite queue permanently full of
    // "14 s ago... 15 s ago" -- and the actual new-event announcements it was
    // built for drowned in it. `role="feed"` also requires `article`
    // children, and these are buttons, which broke feed navigation outright.
    //
    // The announcement lives in <NewEventAnnouncer>, which says one sentence
    // when something arrives and is otherwise silent.
    <>
      <NewEventAnnouncer events={events} />
      <div className="feed">
        {events.map((event) => (
          <Row key={event.id} event={event} now={now} />
        ))}
      </div>
    </>
  );
}
