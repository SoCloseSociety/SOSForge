# SOSForge -- lessons

Erreurs commises, cause racine, regle qui evite la repetition.

## 1. Un selecteur Zustand qui derive un tableau tue l'application

**Erreur.** `const visible = useStore(selectVisible)` ou `selectVisible` fait un
`.filter()`. Page **entierement blanche**, aucune erreur visible dans l'UI, et le
build comme `tsc` restent verts.

**Cause racine.** Sous React 19, `useSyncExternalStore` compare les snapshots par
identite. Un nouveau tableau a chaque appel = changement perpetuel = boucle
infinie, et le composant ne monte jamais.

**Regle.** Un selecteur `useStore` ne rend qu'une tranche **stable** (primitive ou
reference existante). Toute derivation passe par `useMemo` dans le composant.

## 2. Ne jamais conclure "l'UI marche" sans l'avoir regardee

**Erreur.** Backend valide par curl, `tsc` vert, build vert. L'application ne
montait pas du tout.

**Cause racine.** Aucune de ces verifications n'execute le rendu dans un vrai
navigateur.

**Regle.** Toute UI se termine par une capture d'ecran de l'app qui tourne, et par
la lecture des erreurs console (`--enable-logging=stderr --v=1` en headless).
Une page blanche est une panne silencieuse: les erreurs console sont le seul
endroit ou elle parle.

## 3. Une dependance de rendu ne doit pas pouvoir tuer le produit

**Erreur.** `new maplibregl.Map(...)` leve sans WebGL et l'exception non rattrapee
demontait tout l'arbre React: le flux d'alertes disparaissait a cause d'un fond
de carte.

**Regle.** Toute initialisation de bibliotheque dependante du materiel (WebGL,
WebAudio, WebRTC) est dans un `try/catch` avec un repli. Sur un produit d'urgence,
la donnee passe avant l'agrement.

## 4. "Nouveau pour mon buffer" n'est pas "vient de se produire"

**Erreur.** Le premier cycle GDACS poussait ~96 alertes vieilles de plusieurs
jours, toutes annoncees comme du direct (halo, clignotement, son).

**Cause racine.** La fraicheur etait deduite de l'action du store (`new`) au lieu
de l'age reel de l'evenement.

**Regle.** La fraicheur se calcule sur l'horodatage de **l'evenement**, jamais sur
son heure d'arrivee. Le serveur tranche (`breaking`), le client obeit.

## 5. Un flux "live" se trie par date d'evenement, pas d'arrivee

**Erreur.** Un bulletin tsunami vieux de trois jours, re-poll a chaque cycle,
occupait la tete du direct.

**Regle.** L'ordre d'arrivee est un detail d'implementation du polling. Ce que
l'utilisateur lit, c'est une chronologie d'evenements.

## 6. Une source agregatrice doit etre filtree avant d'etre affichee

**Erreur.** GDACS integre tel quel: 397 entrees dont 344 feux verts et des
secheresses ouvertes depuis un an, qui noyaient seismes et tsunamis.

**Regle.** Toute source agregatrice arrive avec une regle de pertinence
explicite et configurable. Ici: gravite elevee toujours conservee, le reste
seulement s'il vient d'etre publie.

## 7. Un champ XML peut porter sa valeur dans un attribut

**Erreur.** `float(gdacs:severity.text)` sur `"Magnitude 5.8M, Depth:54.7km"`:
magnitude systematiquement perdue. La vraie valeur est dans l'attribut `value`.

**Regle.** Avant d'ecrire un normalizer, dumper le payload reel et lire les
**attributs** autant que le texte. Aucun schema ne se devine: les conventions
divergent d'une source a l'autre (profondeur negative chez EMSC, positive chez
USGS; epoch ms chez USGS, ISO chez EMSC).

## 8. Une fixture de test doit venir de la vraie source

**Erreur.** Fixture Atom tsunami ecrite a la main avec de vrais elements XHTML;
elle a fait echouer un parseur qui, lui, fonctionnait sur le flux reel (ou le
XHTML arrive echappe). Une heure a debugger le mauvais cote.

**Regle.** Les fixtures sont des extraits verbatim de reponses capturees. Quand
deux formes existent dans la nature, on parse la forme **normalisee** (ici: le
texte debalise), pas le balisage.


## 9. Un agregateur ne doit jamais se declarer sain quand tout est mort

**Erreur.** `TsunamiSource` appelait `health.ok()` apres sa boucle sur les deux
centres, meme quand les deux avaient echoue. L'interface affichait la source
d'alerte tsunami en vert alors qu'elle ne recevait plus rien.

**Regle.** Une source multi-feed compte ses succes. Zero succes = pas de `ok()`.
Sur un produit d'urgence, une sonde de sante qui ment est pire que pas de sonde.

## 10. Une fenetre glissante se dimensionne en TEMPS, pas en nombre d'entrees

**Erreur.** Le deduper gardait 800 entrees. Les alertes re-emises a chaque cycle
de polling (~146/minute) la vidaient en 5,5 minutes, alors que l'USGS publie sa
solution 5 a 15 minutes apres le push EMSC.

**Regle.** Quand une fenetre doit couvrir une duree, elle se borne par le temps.
Et on n'y met que ce qui peut reellement matcher: les non-seismes n'avaient rien
a y faire.

## 11. Attention a l'ordre des routes quand un converter avale les slashs

**Erreur.** `/api/events/{event_id:path}` declaree avant
`/api/events/{event_id:path}/nearby`: la generique matchait aussi la seconde, qui
n'aurait jamais ete atteinte.

**Regle.** La route la plus specifique se declare en premier. Avec `:path`,
verifier l'ordre est obligatoire, pas optionnel.

## 12. Le drapeau approximatif est une information fausse

**Tentation.** Rattacher chaque evenement a un pays par boite englobante pour
avoir un drapeau partout.

**Regle.** Un seisme en pleine mer n'appartient a aucun pays. Table de
correspondance explicite, et **None** quand on ne peut pas conclure: l'interface
affiche un globe. Sur un produit d'urgence, ne rien dire vaut mieux que dire faux.

## 13. Un outil maison peut etre le bon instrument sans etre la bonne piece

**Constat.** ScrapMe a ete monte en local et lance sur cinq agences sismiques. Il
a parfaitement servi a **decouvrir** que ces agences cachent des flux JSON. Mais
le mettre dans le chemin de donnees aurait ajoute un service, une auth par
cookie, un cycle job/poll asynchrone et une facturation en credits -- pour lire
des flux que le backend prend en direct en cent lignes.

**Regle.** Utiliser un outil pour ce qu'il fait bien, et savoir dire qu'il ne va
pas dans le produit final. Le dire franchement au proprietaire de l'outil aussi.


## 14. Un garde-fou trop large coupe la valeur qu'il protege

**Erreur.** Le rejet des horodatages futurs, ajoute contre les horloges qui
derivent, rejetait aussi les vigilances meteo -- publiees AVANT leur debut, ce
qui est precisement leur interet. Une vigilance orange espagnole apparaissait
deux minutes apres le debut du danger au lieu de deux heures avant.

**Regle.** Avant d'ajouter un filtre, lister ce qu'il coupe de LEGITIME. Ici la
distinction utile n'etait pas "futur ou passe" mais "evenement ponctuel ou
alerte en cours".

## 15. Une position fausse est bien pire qu'une position absente

**Erreur.** `parse_iso6709` acceptait `+3237.5+13040.7` (degres-minutes) et
rendait `lat=3237.5`. Rien en aval ne bornait la valeur: seul le hasard de
l'horizon a empeche ce point d'atterrir hors du globe sur la carte.

**Regle.** Tout parseur de coordonnees borne son resultat (|lat| &lt;= 90,
|lon| &lt;= 180) et rejette au lieu de laisser passer. Un evenement sans position
s'affiche dans le flux; un evenement mal place ment.

## 16. Une source qui relaie n'est pas la source

**Erreur.** La JMA relaie les seismes lointains (un M7.7 indonesien). On leur
collait `country="Japan"`: en production, un seisme indonesien portait le
drapeau japonais.

**Regle.** Distinguer "qui publie" de "ou ca se passe". Un flux national contient
souvent des evenements etrangers, et le type de bulletin le dit.

## 17. Un filtre par defaut peut effacer ce qu'il n'a jamais vise

**Erreur.** Le balayage des alertes muettes purgeait aussi les seismes: la
source cesse normalement d'en parler des qu'ils sortent de sa fenetre de
publication. Le store ne gardait plus que sept heures d'historique alors que
l'interface propose 24 h et "tout". Et le rejeu du journal remettait le compteur
de silence a zero, ce qui masquait le probleme a chaque redemarrage.

**Regle.** Un balayage cible explicitement ce qu'il doit retirer (`ongoing`), et
un rejeu restaure l'etat tel qu'il etait, horloges comprises.


## 18. Ne jamais arreter un service par motif de process sur une machine partagee

**Erreur.** J'ai redemarre l'API une quinzaine de fois avec
`pkill -f "uvicorn app.main:app"`. Tous les produits SuiteForge lancent
exactement cette ligne: j'ai donc tue l'API de ScanGithub (`:8894`) a chaque
fois, emportant ses balayages GitHub en cours. Pire, j'en ai tire une conclusion
fausse et je l'ai transmise: j'ai attribue ces morts a la saturation du swap.
Le swap etait bien sature, mais ce n'est pas lui qui les a tuees -- c'est moi.

**Regle.** Un service s'arrete par son PORT ou son PID, jamais par un motif de
ligne de commande que d'autres partagent (`make stop-api`). Et quand une panne
voisine coincide avec mes propres actions, chercher ma responsabilite AVANT de
designer une cause externe: une explication plausible et confortable n'est pas
une preuve.


## 19. Le meme bug de sous-chaine a frappe deux produits le meme soir

**Constat.** Mon classifieur d'aleas faisait de "Flash Flood" une alerte
volcanique, parce que "Flash" contient "ash". Le meme soir, l'audit de
ScanGithub trouvait sa cause racine: son lexique declenchait la facette
"documentation" sur le mot "rate" parce que "rate" est inclus dans "curated",
et "dataset" sur "open" via "open data". Deux produits, deux equipes, un seul
mecanisme.

**Regle.** Une correspondance de motif sur du texte se fait sur des **mots
entiers** (`re.findall(r"[a-z]+", texte)` puis intersection d'ensembles), jamais
avec `motif in texte`. Un faux positif de ce type est invisible: il ne leve rien,
il classe simplement de travers, et personne ne le voit avant de tomber dessus
par hasard.
