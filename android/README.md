# SOSForge for Android

The native app of SOSForge: Kotlin + Jetpack Compose, no WebView anywhere. Package
`co.soclose.sosforge`, minSdk 26, targetSdk 36, the GStatsForge 0.2.4 pins (AGP 8.13.2, Kotlin
2.2.21, Compose BOM 2026.06.01), plus MapLibre Native 13.6.1 and WorkManager 2.11.0.

The product rule holds here too: **the feed never lies about its own freshness.** The LIVE pill is
driven only by the tick watchdog (`core/live/Watchdog.kt`, a port of `frontend/src/live.ts`), every
age is computed on the server clock, a cached feed shows its age and is never called live, and a
network error says "unreachable, retrying", never "the server is down".

## What is in it

- **First-run funnel** (re-enterable from Settings > Run setup again): welcome, your places (my
  location with a rationale BEFORE the system prompt, or a search through `/api/geocode`; up to ten
  places, each with its radius), what to alert you about (kinds and a severity floor, sane defaults),
  notifications (POST_NOTIFICATIONS on 13+, rationale first, a refusal is a state with a way out),
  background watch (live watch, then the battery exemption; skippable), done.
- **Live**: the world feed, newest EVENT first, severity glyph + word, ages on the server clock,
  window and kind filters (the window never cuts a running alert), the "near your places" line.
  From 780 dp the open event sits beside the list.
- **Map**: MapLibre Native, the web's OpenFreeMap basemap (`dark` in Lunaire, `positron` in Doux),
  markers by severity, the places drawn with their radius. If the style cannot load, a plain
  background; if the map cannot start at all, a fallback and the feed keeps running.
- **Places**: the watch zones, each with what is happening inside it.
- **Sources**: `/api/sources` every 30 s while visible, merged with the ticks; the last error behind
  "Show more".
- **Settings**: language (en, fr, es, ja, id), theme (Lunaire / Doux / system), four text sizes,
  quiet hours, the live watch, the server address, and diagnostics read from the system (notification
  permission, the POST_NOTIFICATION appops mode, each channel's importance, the window measured in dp
  and the navigation it chose, the socket, the server's features, the watch service).
- **Event detail**: what / where / when, distance from each place, the agency's CAP instruction and
  description, before / during / after guidance with official links (lane B `/api/guidance`, cached on
  disk for the offline case), contributing sources, share (the web's `#e/<id>` link), show on map.

## Alerting

All the rules are pure Kotlin in `core/alert/AlertRules.kt` and `Ledger.kt`, tested on the JVM against
the verbatim capture (`AlertRulesTest`).

| Level | Channel | Importance | When |
|---|---|---|---|
| Extreme near you | `sos_extreme_near` | high, alarm sound, never held | extreme, or a tsunami flag, inside a place's reach |
| Severe near you | `sos_severe_near` | default | severe inside a place's reach |
| Lower alerts | `sos_digest` | low, silent, grouped | moderate / minor / info above the owner's floor |
| Watch status | `sos_watch_status` | low, silent | the foreground service's own notification |

- **Reach** = the place's radius, with ONE exception: a tsunami reaches at least 1000 km.
- **News** = the server said so (`breaking`, lane F's `escalated`) or the age rule: a point event at
  most 1 h old on the server clock (the web's `ALARM_MAX_AGE_S`); a running alert while it runs; never
  an event past its own `expires` (shown as "Ended").
- **History**: only the first full read after install is history. Every later snapshot (a reconnect)
  is judged id by id, so what happened during an outage still rings.
- **Never twice**: by cluster key (two agencies' solutions of one quake are one alert). A revision
  updates the notification in place, silently; a rank above the highest it rang for rings again
  (an event first seen below the floor rings when it escalates). A revision that no longer concerns
  the owner, a purge (a cancelled EEW), or a CAP all-clear removes it, and that key never rings again.
- **Quiet hours** hold everything except extreme-near-you; an extreme alert posting during quiet hours
  drops the held ones (they stay in the feed); the rest posts when quiet hours end.
- **No position, no ring**: distance cannot decide (lesson 24).
- **After a reconnect**, a posted alert missing from the snapshot is checked with
  `GET /api/events/{id}`; only a 404 cancels it.

Measured on the real capture, replayed as a reconnect (a 300 km zone): Tokyo 0 rings, Los Angeles 2,
Athens 2, all four server-detected swarms (moderate, the silent digest channel), 89 to 160 km away.

## Background watch, and the foreground service type

Two layers:

1. **Live watch** (opt-in): a foreground service whose only job is to keep the process, and so the
   one socket, alive. Its silent notification says the truth every 3 s: live, reconnecting, server
   busy, or no network. Stop is in the notification.
2. **Regular check**: WorkManager every 15 min (Android's floor) whenever a place exists. If the
   socket is live it does nothing; otherwise it reads `/api/events` (lane B: the owner's zones, a
   cursor, an ETag, so a quiet poll is a 304; legacy: the latest 300 events filtered on the phone)
   through the same alert engine. It also tries to bring the live watch back if it died.

The app says plainly which mode is in force (Settings, the funnel's last step), including "Live
watch is NOT running" when Android refused or killed it.

**Foreground service type: `specialUse`.** Researched against the Android 14 / 15 rules:

| Type | Why not / why |
|---|---|
| `dataSync` | Android 15 caps it at **6 h per 24 h** (then `onTimeout`, and no restart until the app is opened), forbids starting it from `BOOT_COMPLETED`, and the Android docs warn it may be deprecated. A disaster watch that stops after six hours is a lie. |
| `remoteMessaging` | For messages between people's devices. Not this. |
| `location` | Needs continuous location use and the location permission in the background. We read the position once, at setup. |
| `shortService` | About 3 minutes. |
| **`specialUse`** | No time limit, may start from `BOOT_COMPLETED`. Requires `FOREGROUND_SERVICE_SPECIAL_USE` and the `PROPERTY_SPECIAL_USE_FGS_SUBTYPE` explanation (in the manifest). |

The trade-off: on Google Play, `specialUse` needs a Play Console declaration (description, possibly a
video) and a review, which may be refused. Today the APK is sideloaded, so nothing blocks it. If Play
refuses it, the honest fallback is FCM high-priority push (needs a push sender on the backend and
Google Play services), with the regular check kept as the floor. `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS`
is also a Play-restricted permission (lint `BatteryLife` says so); it is what lets the worker restart
the watch from the background on 12+.

## Contract with the backend

Built to lane B's v2 (read from its source; production answered 404 on `/api/version` on 2026-10-26,
so the app runs the legacy path there today):

- `GET /api/version` -> `{api, features[]}`; a 404 means the plain contract.
- `ws_scope`: while only the watch runs, `/ws?zones=lat,lon,radius_km;...&min_severity=&kinds=`. In
  the foreground the socket is always the plain world feed (Live and Map show the world).
- `events_cursor` + `events_zone` + `events_etag`: the worker's incremental poll, `removed` ids cancel.
- `guidance`: `/api/guidance?lang=`, read tolerantly (`GuidanceReader`), cached per language.
- The client ALWAYS filters again, whatever the server did.
- A socket refused with 1013 (or a refused handshake: 403 / 429 / 503, the per-address cap) shows
  "server busy, retrying" and retries with jitter, never sooner than 5 s; a socket stuck connecting
  is abandoned after 10 s.

## Commands

```bash
./build.sh                                              # tests + lint + signed release APK into dist/, then the gate
tools/check-release.sh dist/sosforge-v0.1.0.apk         # targetSdk, version, permission allowlist, 16 KB, signer, hosts
./gradlew --no-daemon :app:testDebugUnitTest            # unit tests
./gradlew --no-daemon :app:testDebugUnitTest -Psos.screens=true --tests '*Screens*'   # PNGs in app/build/screens/
```

JDK 21 (`/opt/homebrew/opt/openjdk@21`). The release key lives outside the repo
(`~/.soclose/android/sosforge.properties` + `sosforge-release.jks`, created by `build.sh` on first run,
certificate digest recorded in `sosforge.cert.sha256`). Bump `versionCode` in `version.properties`
for every build that leaves the machine. Never commit a key, a password, `local.properties` or
build outputs (`.gitignore`).

## Design

Sentinel House tokens from `SentinelHouse/08-sentinel-home/sentinel_home/ui/charte.css` (Lunaire,
Doux), Plex Sans and Plex Mono, mint as the thread and as "ok / live" only. The SOSForge severity
palette keeps the web's hues in both themes and moves only the lightness; every severity word passes
WCAG AA on every surface of both themes and on its own pill (measured in `RulesTest`, printed on each
run). Each severity has a glyph of a different shape (ring, square, triangle, diamond, octagon) plus
its word. Width frontiers: 600 dp (bottom bar below, left rail above) and 780 dp (two columns above).
