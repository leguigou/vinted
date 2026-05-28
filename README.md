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

5. Ouvre http://127.0.0.1:8787 dans ton navigateur.

Tu peux aussi lancer l'application avec `start.bat` ou `run.bat` sous Windows.

Par defaut, le compte admin est `admin` / `admin123`. Si `VINTED_ALERTS_ADMIN_PASSWORD` est definie au demarrage, elle cree ou remet a jour le mot de passe du compte admin cible.

## Lancement avec Docker

```powershell
docker compose up --build
```

L'interface sera disponible sur http://127.0.0.1:8787.

Variables d'environnement utiles :

- `VINTED_ALERTS_HOST` : adresse d'ecoute, par defaut `127.0.0.1`.
- `VINTED_ALERTS_PORT` : port HTTP, par defaut `8787`.
- `VINTED_ALERTS_DB_PATH` : chemin de la base SQLite.
- `VINTED_ALERTS_ADMIN_USERNAME` : utilisateur admin cible, par defaut `admin`.
- `VINTED_ALERTS_ADMIN_PASSWORD` : mot de passe admin. Si la variable est definie, elle est appliquee au demarrage.
- `VINTED_ALERTS_FETCH_API_ENABLED` : active l'appel a une API distante pour les requetes Vinted (`true`, `1`, `yes` ou `on`).
- `VINTED_ALERTS_FETCH_API_URL` : URL du service API de fetch, par exemple `https://maison.example.com:8797`.
- `VINTED_ALERTS_FETCH_API_TOKEN` : token Bearer partage avec le service API de fetch. `VINTED_FETCH_API_TOKEN` est aussi accepte comme alias cote extranet.

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
```

Le fichier `fetch-api.env` contient ton secret local et n'est pas versionne par Git. Les appels sont affiches dans la console et ajoutes dans `fetch-api.log`.

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

## Notes

- La base SQLite locale est creee dans `vinted_alerts.db`.
- Garde la fenetre PowerShell ouverte pour continuer la surveillance sans Docker.
- Evite les intervalles trop courts. Une verification toutes les 2 a 5 minutes est plus raisonnable.
- Si Vinted change son API ou bloque les requetes automatisees, l'interface affichera l'erreur dans la recherche concernee.
