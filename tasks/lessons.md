# SOSForge -- lessons

Mistakes made, root cause, and the rule that prevents a repeat.

## 1. A Zustand selector that derives an array kills the app

**Mistake.** `const visible = useStore(selectVisible)` where `selectVisible`
does a `.filter()`. **Completely blank page**, no visible error in the UI, and
both the build and `tsc` stay green.

**Root cause.** Under React 19, `useSyncExternalStore` compares snapshots by
identity. A new array on every call means perpetual change, an infinite loop,
and the component never mounts.

**Rule.** A `useStore` selector returns a **stable** slice only (a primitive or
an existing reference). Every derivation goes through `useMemo` in the component.

## 2. Never conclude "the UI works" without looking at it

**Mistake.** Backend validated with curl, `tsc` green, build green. The app was
not mounting at all.

**Rule.** Any UI work ends with a screenshot of the running app and a read of the
console errors (`--enable-logging=stderr --v=1` in headless). A blank page is a
silent failure: the console is the only place it speaks.

## 3. A rendering dependency must not be able to kill the product

**Mistake.** `new maplibregl.Map(...)` throws without WebGL, and the uncaught
exception tore down the whole React tree: the alert feed disappeared because of
a basemap.

**Rule.** Any hardware-dependent library init (WebGL, WebAudio, WebRTC) sits
inside a `try/catch` with a fallback. On an emergency product, the data comes
before the ornament.

## 4. "New to my buffer" is not "just happened"

**Mistake.** The first GDACS cycle pushed ~96 alerts several days old, all
announced as live (halo, flashing, sound).

**Root cause.** Freshness was inferred from the store's action (`new`) instead of
the event's actual age.

**Rule.** Freshness is computed on the **event's** timestamp, never on its
arrival time. The server decides (`breaking`), the client obeys.

## 5. A "live" feed sorts by event date, not arrival date

**Mistake.** A tsunami bulletin three days old, re-polled on every cycle, sat at
the top of the live feed.

**Rule.** Arrival order is an implementation detail of polling. What the user
reads is a chronology of events.

## 6. An aggregating source must be filtered before being displayed

**Mistake.** GDACS integrated as-is: 397 entries of which 344 were green
wildfires and droughts open for a year, drowning earthquakes and tsunamis.

**Rule.** Every aggregating source arrives with an explicit, configurable
relevance rule. Here: high severity always kept, the rest only if freshly
published.

## 7. An XML field can carry its value in an attribute

**Mistake.** `float(gdacs:severity.text)` on `"Magnitude 5.8M, Depth:54.7km"`:
magnitude lost every time. The real value is in the `value` attribute.

**Rule.** Before writing a normalizer, dump the real payload and read the
**attributes** as much as the text. No schema can be guessed: conventions differ
from one source to the next (negative depth at EMSC, positive at USGS; epoch ms
at USGS, ISO at EMSC).

## 8. A test fixture must come from the real source

**Mistake.** A tsunami Atom fixture written by hand with real XHTML elements; it
failed a parser that worked fine on the real feed (where the XHTML arrives
escaped). An hour spent debugging the wrong side.

**Rule.** Fixtures are verbatim excerpts of captured responses. When two shapes
exist in the wild, parse the **normalized** shape (here: the stripped text), not
the markup.

## 9. An aggregator must never report healthy when everything is dead

**Mistake.** `TsunamiSource` called `health.ok()` after its loop over both
centres, even when both had failed. The UI showed the tsunami alert source green
while it was receiving nothing.

**Rule.** A multi-feed source counts its successes. Zero successes means no
`ok()`. On an emergency product, a health probe that lies is worse than none.

## 10. A sliding window is sized in TIME, not in number of entries

**Mistake.** The deduper kept 800 entries. Alerts re-emitted on every polling
cycle (~146/minute) emptied it in 5.5 minutes, while USGS publishes its solution
5 to 15 minutes after the EMSC push.

**Rule.** When a window must cover a duration, bound it by time. And only put in
it what can actually match: non-earthquakes had no business being there.

## 11. Watch route order when a converter swallows slashes

**Mistake.** `/api/events/{event_id:path}` declared before
`/api/events/{event_id:path}/nearby`: the generic one matched the second too,
which would never have been reached.

**Rule.** The most specific route is declared first. With `:path`, checking the
order is mandatory, not optional.

## 12. An approximate flag is false information

**Temptation.** Attach every event to a country by bounding box so there is a
flag everywhere.

**Rule.** An earthquake on the high seas belongs to no country. Explicit lookup
table, and **None** when we cannot conclude: the UI shows a globe. On an
emergency product, saying nothing beats saying something wrong.

## 13. An in-house tool can be the right instrument without being the right part

**Observation.** ScrapMe was brought up locally and pointed at five seismic
agencies. It served perfectly to **discover** that those agencies hide JSON
feeds. But putting it in the data path would have added a service, cookie auth,
an async job/poll cycle and credit billing -- to read feeds the backend takes
directly in a hundred lines.

**Rule.** Use a tool for what it is good at, and be able to say it does not
belong in the final product. Say it plainly to the tool's owner too.

## 14. A guard that is too broad cuts the value it protects

**Mistake.** Rejecting future timestamps, added against drifting clocks, also
rejected weather warnings -- published BEFORE they start, which is precisely
their point. A Spanish orange warning would have appeared two minutes after the
danger began instead of two hours before.

**Rule.** Before adding a filter, list what LEGITIMATE things it cuts. The useful
distinction here was not "future or past" but "point-in-time event or ongoing
alert".

## 15. A wrong position is far worse than a missing one

**Mistake.** `parse_iso6709` accepted `+3237.5+13040.7` (degrees-minutes) and
returned `lat=3237.5`. Nothing downstream bounded the value: only the accident of
the ingestion horizon kept that point off the map, off the globe.

**Rule.** Every coordinate parser bounds its result (|lat| <= 90, |lon| <= 180)
and rejects rather than passing it on. An event without a position shows in the
feed; an event in the wrong place lies.

## 16. A relaying source is not the source

**Mistake.** The JMA relays distant earthquakes (a M7.7 in Indonesia). We stamped
them `country="Japan"`: in production, an Indonesian earthquake wore a Japanese
flag.

**Rule.** Separate "who publishes" from "where it happens". A national feed often
contains foreign events, and the bulletin type says so.

## 17. A default filter can erase what it never targeted

**Mistake.** The sweep of silent alerts also purged earthquakes: a source
normally stops mentioning them as soon as they leave its publication window. The
store kept only seven hours of history while the UI offers 24 h and "all". And
replaying the journal reset the silence counter, which hid the problem at every
restart.

**Rule.** A sweep explicitly targets what it must remove (`ongoing`), and a
replay restores state as it was, clocks included.

## 18. Never stop a service by process pattern on a shared machine

**Mistake.** I restarted the API about fifteen times with
`pkill -f "uvicorn app.main:app"`. Every SuiteForge product runs exactly that
command line, so I killed ScanGithub's API (`:8894`) every single time, taking
its in-flight GitHub sweeps with it. Worse, I drew a false conclusion and passed
it on: I blamed those deaths on swap exhaustion. The swap was indeed nearly full,
but that is not what killed them -- I did.

**Rule.** A service is stopped by its PORT or its PID, never by a command-line
pattern others share (`make stop-api`). And when a neighbour's failure coincides
with my own actions, look for my responsibility BEFORE naming an external cause:
a plausible and comfortable explanation is not evidence.

## 19. The same substring bug hit two products on the same evening

**Observation.** My hazard classifier turned "Flash Flood" into a volcanic alert,
because "Flash" contains "ash". That same evening, the ScanGithub audit found its
root cause: its lexicon triggered the "documentation" facet on the word "rate"
because "rate" is inside "curated", and "dataset" on "open" via "open data". Two
products, two teams, one mechanism.

**First fix, wrong.** I switched both classifiers to **whole words**. The false
positive disappeared, the tests passed, I shipped.

**What measuring showed.** The neighbouring session insisted on measuring
before/after on real data instead of observing it in production. Across 2525 real
alerts, the whole-word rule **lost 621 alerts** -- "Forestfire", "Thunderstorms",
"Rainstorm" are compound or inflected forms no whole word finds. I had traded one
false positive for 621 false negatives, and eyeballing had not caught it.

**Final rule, measured.** Length floor: substring for patterns of four characters
or more, whole word below. Zero loss, and the 22 "Flash Flood" alerts correctly
reclassified. That was the neighbouring session's exact diagnosis
("substring without a floor on alias length"), sharper than mine.

**The lesson behind the lesson.** Never fix a classification problem by narrowing
detection without measuring what the narrowing costs. A green test on hand-picked
cases proves nothing about recall: you have to count, on real data, how many
items go from "correctly classified" to "unclassified".

## 20. I deployed to the wrong machine because I stopped searching too early

**Mistake.** `sosforge.soclose.co` pointed at 212.227.202.92 while every other
product pointed at the hub. I grepped for DNS credentials with a pattern anchored
to the START of the variable name, found nothing, concluded there was no
automated DNS access, and deployed on the hub -- telling the owner the DNS was
wrong.

**Root cause.** The DNS was right. 212.227.202.92 is the fleet's **helper VPS**,
listed in `~/.ssh/config` as `helper-vps` and documented in the shared memory. My
narrow grep and my hurry produced a confident wrong answer.

**Rule.** Before declaring something impossible, search wide, not narrow: an
unanchored grep, the SSH config, the shared memory, the fleet docs. And when an
address does not match my expectation, the first hypothesis is that my map is
incomplete, not that the world is misconfigured.
