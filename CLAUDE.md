# CLAUDE.md -- SOSForge

Guide produit. Autoritaire pour l'interieur de SOSForge; le `CLAUDE.md` de
SuiteForge reste autoritaire pour les conventions inter-produits.

> Jamais d'em dash. Utiliser `--`.

## Ce que c'est

Un tracker temps reel des seismes, tsunamis, volcans et alertes catastrophes.
Dix-neuf sources publiques agregees en un flux normalise, diffuse par websocket
avec un battement d'une seconde. FastAPI + React 19 / Vite / TypeScript / Zustand /
MapLibre. Aucune cle API: tout est public.

## La regle qui gouverne le produit

**Le flux ne doit jamais mentir sur sa propre fraicheur.** Un tracker d'urgence
qui affiche des donnees figees en pretendant etre "en direct" est pire qu'un
tracker eteint. Concretement:

- le serveur emet un `tick` chaque seconde; le client qui n'en recoit plus
  pendant 15 s se declare deconnecte et reconnecte;
- les ages ("il y a 12 s") sont calcules sur l'horloge **serveur** (`clockSkew`),
  jamais sur celle du navigateur;
- `/api/sources` expose l'etat reel de chaque source, y compris sa derniere
  erreur, et le pied de page l'affiche en permanence;
- une panne partielle (une source morte, WebGL absent) degrade, elle n'eteint pas.

## Fichiers critiques -- plan ecrit dans `tasks/todo.md` avant d'y toucher

| Fichier | Pourquoi |
|---|---|
| `backend/app/pipeline.py` | le chemin unique de tout evenement. Un bug ici touche tout le produit |
| `backend/app/dedupe.py` | regroupement inter-sources. Trop laxiste: des seismes disparaissent. Trop strict: doublons partout |
| `backend/app/store/ring.py` | seul point de stockage, gere aussi les revisions |
| `backend/app/hub.py` | fan-out websocket, et la politique d'ejection des clients lents |
| `backend/app/sources/*.py` | chaque normalizer est cale sur un schema reel: ne jamais "corriger" un champ sans avoir revu le payload de la source |
| `frontend/src/live.ts` | reconnexion + watchdog: c'est ce qui garantit la promesse "live" |

## Pieges verifies sur les vraies sources (ne pas les re-decouvrir)

- **EMSC**: `geometry.coordinates[2]` est une elevation **negative** (`-10.0`);
  `properties.depth` est positive en km. Timestamps ISO. `id` == `properties.unid`.
- **USGS**: `time` et `updated` en **epoch millisecondes**; `geometry.coordinates[2]`
  est une profondeur **positive**. Convention inverse de l'EMSC.
- **GDACS**: `gdacs:severity` porte la valeur numerique dans l'**attribut** `value`
  (le texte est humain: "Magnitude 5.8M, Depth:54.7km"). L'unite change selon le
  type: M, km/h, ha. Un evenement a plusieurs episodes: la cle stable est `eventid`.
  Le serveur met regulierement 60 s a repondre 1.2 Mo: timeout large obligatoire.
- **tsunami.gov**: la categorie (Information / Watch / Advisory / Warning) et la
  magnitude sont dans le HTML du `summary`, servi tantot echappe tantot en vrais
  elements (qui ressortent prefixes par le namespace Atom). On regexe donc sur le
  texte **debalise**, jamais sur le balisage. Le `link rel="self"` de PHEB pointe
  a tort vers PAAQ: ne pas s'y fier.
- **NWS**: User-Agent identifiant obligatoire; `limit` renvoie 400 sur
  `/alerts/active`; `geometry` est souvent `null` (zones UGC) -- l'alerte doit
  rester exploitable sans position.
- **HANS volcans**: aucune coordonnee dans la reponse. Elles viennent du catalogue
  Holocene du Smithsonian, joint sur `vnum` == `Volcano_Number`.

## Frontend

- **Zustand + React 19**: ne jamais passer a `useStore` un selecteur qui construit
  un nouvel objet ou tableau. `useSyncExternalStore` boucle a l'infini et le
  composant ne monte jamais (page blanche, sans erreur visible dans l'UI). Les
  derivations passent par `useMemo` sur des tranches stables.
- **Couleur de gravite**: palette "status" reservee (`info`, `minor`, `moderate`,
  `severe`, `extreme`). Elle ne sert jamais a distinguer une serie, et elle ne
  porte jamais le sens seule: glyphe + libelle l'accompagnent partout.
- **MapLibre**: l'initialisation est dans un `try/catch`. Sans WebGL, la carte
  affiche un repli et le flux continue.

## Piege d'exploitation partage avec les autres produits SuiteForge

**Ne jamais arreter cette API par motif de process.** Tous les produits de la
suite lancent litteralement `uvicorn app.main:app`: un
`pkill -f "uvicorn app.main:app"` tue aussi ScanGithub (`:8894`) et les autres
qui tournent sur la meme machine. Cette erreur a deja coute a une session
voisine trois balayages GitHub en cours, et l'a envoyee chercher la panne dans
son propre code. Utiliser `make stop-api` (qui vise le port 8300) ou
`lsof -ti tcp:8300 | xargs kill`.

## Commandes

```bash
make install      # venv backend + npm install
make dev-api      # API :8300
make dev-web      # UI :5273
make test         # tests des normalizers sur payloads reels
make lint typecheck
make stop-api     # arret par PORT, jamais par motif de process
make smoke        # etat live des sources
make up           # docker, UI sur :8380
```

## Verification: ce qui compte comme "fait"

Une source n'est pas "implementee" tant qu'elle n'a pas tourne **contre l'API
reelle** et que `make smoke` ne la montre pas `up` avec des evenements vus. Un
test unitaire sur un payload invente ne prouve rien ici: les fixtures de
`tests/test_parsers.py` sont des extraits verbatim de reponses reelles, et c'est
la seule forme de fixture acceptee dans ce produit.
