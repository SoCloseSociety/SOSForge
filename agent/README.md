# SOSForge local agent

A background process that watches the SOSForge feed and tells this machine
when something is happening **near it**. No browser, no tray icon, no account.

## What it costs

Measured on this machine, connected to the live feed for 90 seconds:

| | |
|---|---|
| Resident memory | **22 MB**, flat |
| CPU | **0.0 - 0.3 %** |
| Network | one websocket, ~1 small JSON frame per second |
| Disk | one config file, one zone cache. No database |

There is no polling loop and no timer: the process sleeps inside `recv()` and
the kernel wakes it when a frame arrives. That is the whole design.

## Install

```bash
python3 -m pip install websockets           # the only dependency

cd agent
PYTHONPATH=. python3 -m sosforge_agent setup          # guesses your position from your IP
PYTHONPATH=. python3 -m sosforge_agent setup --lat 48.8566 --lon 2.3522 --country FR   # or be exact

PYTHONPATH=. python3 -m sosforge_agent test           # one test notification
PYTHONPATH=. python3 -m sosforge_agent check          # what it WOULD have said over the last 24 h
PYTHONPATH=. python3 -m sosforge_agent run            # listen until stopped
```

Then, to have it run all the time:

```bash
./install-macos.sh        # launchd service, starts at login, background priority
./uninstall-macos.sh      # removes it
```

On Linux the same `run` command works under a systemd user unit; on Windows,
under Task Scheduler at logon. Notifications use `notify-send` and the
PowerShell toast API respectively.

## `check` before you trust it

`check` replays the real last 24 hours of the feed through your exact settings
and prints what would have reached you. Use it to pick a threshold, and use it
again after changing one. It is the only honest way to answer "will this thing
be useful or annoying", and it has already caught two bad rules in this file's
own history:

- "notify on any extreme alert with no coordinates" rang **79 times in a day**
  for someone in Los Angeles -- marine warnings in South Carolina, Texas
  counties, Canadian parks. Alerts without geometry needed a locator, and the
  only one they carry is the country.
- Country alone was still too coarse for a country the size of the United
  States, which pushed the fix upstream: the server now resolves the UGC zones
  NWS publishes, and **11% of US alerts had a position before, 100% after**.
- What remains are alerts with no position anywhere in the payload -- most of
  the WMO aggregate. Those are off by default (`zone_alerts`), because country
  is the only locator they carry and that is fine for France and useless for
  the United States: measured on one real day, **Los Angeles 63, Paris 2**, and
  severity separates nothing (all 63 were "extreme" too). `check` prints your
  own number so the choice is informed rather than guessed.

## What wakes you, and what does not

| Situation | Behaviour |
|---|---|
| The 300 events already in the feed when it connects | Silent. That is history, not news |
| The same event revised (an early warning re-issued, a magnitude corrected) | Silent after the first |
| A small quake far away | Silent -- the radius grows with magnitude, from ~25 km at M2.5 to ~1500 km at M8 |
| A large quake far away | Rings. A M7.8 four hundred kilometres away is felt, and on a coast it is the thing you needed to hear |
| A swarm producing forty qualifying events | At most `max_per_hour` (12 by default). A machine that buzzes forty times gets muted, and then protects nobody |
| An alert with no coordinates | Silent by default -- see below |
| A preliminary solution | Rings, and says "preliminary, may be revised" |

## Configuration

`~/.config/sosforge/agent.json`

```json
{
  "lat": 48.8566,
  "lon": 2.3522,
  "label": "Paris",
  "country_code": "FR",
  "url": "wss://sosforge.soclose.co/ws",
  "min_severity": "moderate",
  "max_distance_km": 300.0,
  "max_per_hour": 12,
  "sound": true
}
```

Point `url` at `ws://127.0.0.1:8300/ws` to run it against a local backend.

## Privacy

The position stays in that file. It is compared locally against a feed that is
public to everyone; nothing about you is sent anywhere. `setup` without
`--lat/--lon` asks ipapi.co once for a rough position and you can skip it
entirely by passing your own coordinates.
