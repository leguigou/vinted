# Vinted Alerts

Interface locale pour surveiller des recherches Vinted depuis ton ordinateur et recevoir les nouveaux articles sur Telegram.

## Installation

1. Installe Python 3.11 ou plus recent depuis https://www.python.org/downloads/.
2. Ouvre PowerShell dans ce dossier.
3. Optionnel mais recommande : choisis le mot de passe admin initial.

```powershell
$env:VINTED_ALERTS_ADMIN_PASSWORD = "change-moi"
```

4. Lance l'application :

```powershell
python app.py
```

5. Ouvre http://127.0.0.1:8790 dans ton navigateur.

Tu peux aussi lancer l'application avec `start.bat` sous Windows.

En ecoute locale uniquement, le compte admin initial est `admin` / `admin123`. Change ce mot de passe des la premiere connexion. L'application refuse de demarrer sur une adresse publique avec ce mot de passe par defaut. Si `VINTED_ALERTS_ADMIN_PASSWORD` est definie au demarrage, elle cree ou remet a jour le mot de passe du compte admin cible.

## Installation comme application PWA

Vinted Alerts peut etre installe depuis le navigateur comme une application.

- En local, ouvre `http://127.0.0.1:8790`.
- Sur un serveur distant, utilise une URL HTTPS via ton domaine Dokploy ou ton reverse proxy.
- Dans Chrome ou Edge, clique sur l'icone d'installation dans la barre d'adresse, ou va dans le menu du navigateur puis `Installer Vinted Alerts`.

La PWA garde l'interface en cache pour demarrer plus vite, mais les appels `/api` restent toujours en direct pour eviter les donnees perimees.

## Lancement avec Docker

```powershell
$env:VINTED_ALERTS_ADMIN_PASSWORD = "un-mot-de-passe-long-et-unique"
docker compose up --build
```

L'interface sera disponible sur http://127.0.0.1:8787.

Tu peux aussi creer un fichier `.env` a cote de `docker-compose.yml` :

```powershell
Copy-Item .env.example .env
```

Puis remplace le mot de passe dans ce fichier :

```text
VINTED_ALERTS_ADMIN_PASSWORD=un-mot-de-passe-long-et-unique
```

Variables d'environnement utiles :

- `VINTED_ALERTS_HOST` : adresse d'ecoute, par defaut `127.0.0.1`.
- `VINTED_ALERTS_PORT` : port HTTP, par defaut `8790`.
- `VINTED_ALERTS_DB_PATH` : chemin de la base SQLite.
- `VINTED_ALERTS_ADMIN_USERNAME` : utilisateur admin cible, par defaut `admin`.
- `VINTED_ALERTS_ADMIN_PASSWORD` : mot de passe admin. Si la variable est definie, elle est appliquee au demarrage.
- `VINTED_ALERTS_SESSION_TTL_SECONDS` : duree maximale d'une session, `604800` secondes (7 jours) par defaut.
- `VINTED_ALERTS_SECURE_COOKIE` : mets `true` lorsque l'application est accessible derriere une URL HTTPS.
- `VINTED_ALERTS_MAX_JSON_BODY_BYTES` : taille maximale d'un corps JSON, `65536` octets par defaut.
- `VINTED_ALERTS_LOGIN_ATTEMPT_LIMIT` : nombre maximal d'echecs de connexion dans la fenetre de limitation, `5` par defaut.
- `VINTED_ALERTS_LOGIN_ATTEMPT_WINDOW_SECONDS` : fenetre de limitation des connexions, `300` secondes par defaut.
- `VINTED_ALERTS_FETCH_API_ENABLED` : active l'appel a une API distante pour les requetes Vinted (`true`, `1`, `yes` ou `on`).
- `VINTED_ALERTS_FETCH_API_URL` : URL du service API de fetch, par exemple `https://maison.example.com:8797`.
- `VINTED_ALERTS_FETCH_API_TOKEN` : token Bearer partage avec le service API de fetch. `VINTED_FETCH_API_TOKEN` est aussi accepte comme alias cote extranet.
- `VINTED_FETCH_API_MAX_JSON_BODY_BYTES` : taille maximale d'une requete recue par l'API de fetch, `16384` octets par defaut.

## API de fetch Vinted separee

Le script `vinted_fetch_api.py` lance un petit service HTTP independant de l'extranet. Il sert a recevoir une demande depuis un VPS, puis a interroger Vinted depuis la machine qui heberge ce service. C'est donc l'IP de cette machine, par exemple ton ordinateur ou un Raspberry Pi a la maison, qui sera vue par Vinted.

Le service expose :

- `GET /health` : verification simple que le service tourne.
- `POST /api/vinted/json` : appelle l'API catalogue Vinted et renvoie le JSON brut. Le body doit contenir `{"url":"https://www.vinted.fr/..."}` ou une URL API `/api/v2/catalog/items`.

Protection :

- Le header `Authorization: Bearer TON_TOKEN` est obligatoire.
- Le service refuse les URLs qui ne pointent pas vers `https://*.vinted.fr/api/v2/catalog/items`.
- Si le service est expose sur Internet, mets de preference un reverse proxy HTTPS devant lui et limite l'acces au port dans ton pare-feu.

### Lancer le service API a la maison

Windows PowerShell :

```powershell
$env:VINTED_FETCH_API_TOKEN = "un-token-long-et-secret"
$env:VINTED_FETCH_API_HOST = "0.0.0.0"
$env:VINTED_FETCH_API_PORT = "8797"
python vinted_fetch_api.py
```

Ou avec le lanceur Windows :

```powershell
.\start-fetch-api.bat
```

Le lanceur Windows charge automatiquement `fetch-api.env` si le fichier existe. Copie `fetch-api.env.example` vers `fetch-api.env`, puis renseigne les valeurs :

```text
VINTED_FETCH_API_TOKEN=un-token-long-et-secret
VINTED_FETCH_API_HOST=0.0.0.0
VINTED_FETCH_API_PORT=8797
VINTED_FETCH_API_LOG_PATH=fetch-api.log
VINTED_FETCH_API_RESTART_EXISTING=true
```

Le fichier `fetch-api.env` contient ton secret local et n'est pas versionne par Git. Les appels sont affiches dans la console et ajoutes dans `fetch-api.log`.
Avec `VINTED_FETCH_API_RESTART_EXISTING=true`, le lanceur Windows ferme automatiquement le processus qui ecoute deja sur `VINTED_FETCH_API_PORT` avant de relancer l'API. Mets `false` pour desactiver ce comportement.

Linux / Raspberry Pi :

```bash
export VINTED_FETCH_API_TOKEN="un-token-long-et-secret"
export VINTED_FETCH_API_HOST="0.0.0.0"
export VINTED_FETCH_API_PORT="8797"
python3 vinted_fetch_api.py
```

Ou avec le lanceur Linux :

```bash
chmod +x start-fetch-api.sh
export VINTED_FETCH_API_TOKEN="un-token-long-et-secret"
./start-fetch-api.sh
```

### Configurer l'extranet sur le VPS

Sur le serveur qui lance `app.py`, active la delegation :

```bash
export VINTED_ALERTS_FETCH_API_ENABLED=true
export VINTED_ALERTS_FETCH_API_URL="https://maison.example.com:8797"
export VINTED_ALERTS_FETCH_API_TOKEN="un-token-long-et-secret"
python3 app.py
```

Sous PowerShell :

```powershell
$env:VINTED_ALERTS_FETCH_API_ENABLED = "true"
$env:VINTED_ALERTS_FETCH_API_URL = "https://maison.example.com:8797"
$env:VINTED_ALERTS_FETCH_API_TOKEN = "un-token-long-et-secret"
python app.py
```

## Connexion Telegram etape par etape

1. Ouvre Telegram sur ton telephone.
2. Cherche `@BotFather`.
3. Envoie `/newbot`.
4. Choisis un nom, puis un identifiant qui finit par `bot`.
5. BotFather te donne un token du style `123456:ABC...`.
6. Colle ce token dans l'interface Vinted Alerts, champ `Token du bot`.
7. Dans Telegram, ouvre une conversation avec ton nouveau bot.
8. Envoie-lui un message, par exemple `start`.
9. Dans l'interface, clique sur `Enregistrer`, puis `Trouver mon Chat ID`.
10. Clique encore sur `Enregistrer` pour sauvegarder le Chat ID.
11. Clique sur `Tester`. Tu dois recevoir un message Telegram.

## Si `Trouver mon Chat ID` ne marche pas

1. Verifie que le token est colle dans l'interface.
2. Clique sur `Trouver mon Chat ID`. Le token est sauvegarde automatiquement.
3. Ouvre ton bot Telegram sur ton telephone.
4. Envoie `/start` ou n'importe quel message au bot.
5. Reviens dans l'interface et reclique sur `Trouver mon Chat ID`.
6. Quand le Chat ID apparait, clique sur `Enregistrer`, puis `Tester`.

Si une erreur Telegram s'affiche avec `webhook`, le bot a probablement deja ete utilise ailleurs. Dans ce cas, ouvre cette URL dans ton navigateur en remplacant `TON_TOKEN` :

```text
https://api.telegram.org/botTON_TOKEN/deleteWebhook
```

Puis renvoie un message au bot et reclique sur `Trouver mon Chat ID`.

## Ajouter une recherche Vinted

1. Va sur Vinted dans ton navigateur.
2. Fais ta recherche avec les filtres voulus.
3. Copie l'URL de la page de recherche.
4. Colle-la dans `URL de recherche Vinted`.
5. Donne un nom a la recherche.
6. Choisis un intervalle raisonnable, par exemple `180` secondes.
7. Clique sur `Ajouter la recherche`.

L'application garde en base les articles deja vus pour eviter les doublons.

L'onglet `Recherches` regroupe toutes les veilles. Le bouton `Rechercher maintenant` lance uniquement la recherche choisie, meme si sa surveillance automatique est en pause. Le bouton global `Verifier maintenant` execute lui aussi toutes les recherches a la demande, sans modifier leur statut actif ou en pause.

Apres un ajout, l'application ouvre directement l'onglet `Recherches`. Pendant une modification, le rafraichissement automatique est suspendu pour conserver le formulaire et son brouillon. L'URL Vinted est presentee sous forme de criteres lisibles ; son adresse complete reste consultable et modifiable dans le volet dedie.

## Gestion des utilisateurs

Un administrateur peut creer des comptes, modifier leur nom et leur role, les activer ou les suspendre, reinitialiser leur mot de passe, fermer toutes leurs sessions et les supprimer. La page affiche aussi leur derniere connexion ainsi que le nombre de recherches et de sessions ouvertes.

- Suspendre un compte ferme ses sessions et met sa surveillance en pause sans supprimer ses recherches.
- Reactiver le compte reprend automatiquement sa surveillance.
- Modifier un mot de passe depuis l'administration ferme toutes les sessions du compte cible.
- Le compte actuellement connecte et le dernier administrateur actif sont proteges contre une suppression, une suspension ou une retrogradation accidentelle.
- Le compte administrateur systeme defini par `VINTED_ALERTS_ADMIN_USERNAME` reste actif et ne peut pas etre renomme ou supprime depuis l'interface.

## Notes

- La base SQLite locale est creee dans `vinted_alerts.db`.
- Garde la fenetre PowerShell ouverte pour continuer la surveillance sans Docker.
- Evite les intervalles trop courts. Une verification toutes les 2 a 5 minutes est plus raisonnable.
- Dans les parametres, `Aleatoire de verification` ajoute jusqu'a ce pourcentage de delai en plus de l'intervalle defini, par tranches de 5 %, avec `5 %` par defaut.
- Si Vinted change son API ou bloque les requetes automatisees, l'interface affichera l'erreur dans la recherche concernee.

## Tests

```powershell
python -m unittest discover -v
```
