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
