# SOSForge -- todo

Tracker temps reel (live a la seconde) des seismes, tsunamis et autres catastrophes
naturelles. Produit SuiteForge: backend FastAPI, frontend React 19 + Vite + TS + Zustand.

## Principe

Le "live a la seconde" ne vient PAS d'un polling agressif d'une seule API: il vient
d'un fan-in de sources heterogenes (une source push websocket + des sources polling
rapide) normalisees dans un seul flux, puis fan-out websocket vers les navigateurs.

```
EMSC websocket (push)  \
USGS GeoJSON (poll 5s)  \
NOAA tsunami.gov (poll)  >--> normalize --> dedupe --> store (ring) --> hub --> /ws --> UI
GDACS (poll)            /
USGS/NWS alerts        /
```

## Plan

- [x] Scaffolder l'arbo backend/frontend
- [x] Modele d'evenement normalise (`Event`) commun a toutes les sources
- [x] Ring buffer en memoire + persistance JSONL (pas de Postgres en v1: simplicite)
- [x] Hub websocket (snapshot a la connexion + deltas + heartbeat 1s)
- [x] Adapter EMSC websocket (push, reconnexion exponentielle)
- [x] Adapter USGS GeoJSON (poll, detection des revisions par `updated`)
- [x] Adapter NOAA/NWS tsunami (alertes tsunami)
- [x] Adapter GDACS (cyclones, inondations, volcans, seismes majeurs)
- [x] Dedup inter-sources (EMSC vs USGS: meme seisme, deux ids)
- [x] API REST: /api/events, /api/stats, /api/sources, /healthz
- [x] Frontend: carte MapLibre + feed live + banniere alerte tsunami + son
- [x] Compteur "il y a N s" qui tick a la seconde
- [x] Makefile + docker-compose dev
- [x] Adapter NWS api.weather.gov (alertes US tous aleas)
- [x] Adapter volcans USGS HANS + catalogue Smithsonian pour les coordonnees
- [x] Distinction "vient de se produire" / "vient d'arriver dans le buffer"
- [x] Verification end to end sur donnees reelles

## Review

**Livre.** Backend FastAPI (6 sources, ring buffer, hub websocket) + frontend
React 19 / Vite / Zustand / MapLibre. 26 tests, ruff et tsc verts, docker compose
et Makefile fournis. Aucune cle API n'est requise.

### Verifie en conditions reelles (pas "ca devrait marcher")

- Les 6 sources remontent `connected=true` avec des evenements vus
  (`/api/sources`): emsc, usgs, tsunami, gdacs, nws, volcano.
- Websocket bout en bout: snapshot de 250 evenements a la connexion, 13 ticks en
  12 s (cadence 1/s tenue), et des evenements reellement pousses depuis le
  pipeline vers un client externe.
- Les fixtures des tests sont des extraits **verbatim** de reponses reelles
  (frame EMSC, feature USGS, entry Atom PAAQ, item RSS GDACS, alerte NWS,
  ligne HANS), pas des payloads inventes.
- UI verifiee par capture sur le vrai flux: `docs/screenshot.png`.

### Trois defauts trouves en verifiant, et corriges

1. **Le flux etait trie par ordre d'arrivee.** Un bulletin tsunami vieux de trois
   jours, re-poll a l'instant, se retrouvait en tete du direct. Tri par date
   d'evenement.
2. **GDACS noyait tout**: 397 entrees dont 344 feux verts et des secheresses
   ouvertes depuis un an. Filtre sur la fraicheur de publication, orange et rouge
   toujours conserves.
3. **"Nouveau pour le store" n'est pas "vient de se produire".** Au premier cycle,
   GDACS injectait ~96 alertes anciennes qui clignotaient comme du breaking news.
   Drapeau `breaking` calcule cote serveur sur l'age reel de l'evenement.

### Deux pannes rencontrees pendant le developpement

- Page blanche totale: un selecteur Zustand renvoyant un tableau neuf a chaque
  appel fait boucler `useSyncExternalStore` sous React 19.
- Application detruite par MapLibre quand WebGL est indisponible. L'init est
  desormais dans un `try/catch` avec repli: le flux d'alertes survit a l'absence
  de carte.

### Volontairement hors perimetre

- **Postgres**: inutile pour un tracker qui vit sur la derniere heure. Le ring
  buffer plus le journal JSONL suffisent; `EventStore` isole le jour ou l'historique
  devient un besoin produit.
- **NASA FIRMS (feux)**: latence NRT d'environ 3 h, pas d'identifiant d'evenement
  (des pixels a clusteriser), CSV mondial volumineux. Cela ne repond pas a la
  promesse "temps reel"; GDACS couvre deja les incendies.
- **SeedLink / Raspberry Shake**: des formes d'onde, pas des evenements. Il
  faudrait refaire une detection type GlobalQuake pour en tirer un seisme.
- **JMA, Kandilli, ExpTech**: regionaux, et le vrai temps reel japonais est sous
  contrat. A rouvrir si une couverture Japon devient un besoin.
