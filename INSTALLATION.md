# Installation dans votre projet PharmaX

Votre projet se trouve dans `C:\Users\ZBOOK\Downloads\pharmacie\pharmacie\`.
Le dossier `pharmax_a_installer/` reproduit exactement l'arborescence attendue :
il suffit de le fusionner avec votre projet.

---

## Étape 1 — Sauvegarder d'abord

Dans PowerShell, à la racine du projet :

```powershell
cd C:\Users\ZBOOK\Downloads\pharmacie\pharmacie
Copy-Item -Recurse -Force . ..\pharmacie_sauvegarde_avant_maj
```

Vérifiez surtout que `data\pharmacies.db` est bien dans la copie : c'est votre
base de données, elle contient vos 68 pharmacies et vos gardes.

---

## Étape 2 — Installer les dépendances

```powershell
python -m pip install requests beautifulsoup4 scikit-learn joblib pandas
```

Vérifiez ensuite que tout s'importe avec le **même** interpréteur que celui
qui lance l'application :

```powershell
python -c "import flask, requests, bs4, sklearn, joblib; print('OK')"
```

Si cette commande échoue, votre environnement virtuel n'est pas activé :
lancez `.\venv\Scripts\Activate.ps1` puis recommencez.

---

## Étape 3 — Copier les fichiers

Copiez le **contenu** de `pharmax_a_installer/` dans votre dossier projet, en
acceptant l'écrasement. En PowerShell, depuis le dossier qui contient
`pharmax_a_installer` :

```powershell
Copy-Item -Path .\pharmax_a_installer\* -Destination C:\Users\ZBOOK\Downloads\pharmacie\pharmacie\ -Recurse -Force
```

### Détail de ce qui est copié

| Emplacement | Fichiers | Nature |
|---|---|---|
| Racine | `app.py`, `config.py`, `nlu.py`, `scraper_inam.py`, `requirements.txt` | remplacés |
| Racine | `intent_classifier.py` | **nouveau** |
| `models\` | `intent_classifier.joblib`, `metadata.json` | **nouveau dossier** |
| `templates\` | `_auth_landing.html`, `_topbar.html`, `admin_base.html`, `admin_pharmacies.html`, `admin_scraped.html`, `admin_users.html`, `directory.html`, `password_forgot.html`, `password_reset.html`, `profile.html` | **nouveaux** |
| `templates\` | `base.html`, `chat.html`, `dashboard.html`, `garde.html`, `guards_admin.html`, `index.html`, `login.html`, `pharmacy_form.html`, `admin_analytics.html` | remplacés |
| `static\css\` | `admin.css`, `app-ui.css`, `auth.css`, `topbar.css` | **nouveaux** |
| `static\css\` | `chat.css`, `style.css` | remplacés |
| `static\js\` | `chat.js` | remplacé (une seule ligne modifiée) |

### Fichiers devenus inutiles

Ces gabarits ne sont plus appelés par aucune route. Vous pouvez les supprimer,
ou les laisser sans conséquence :

- `templates\favorites.html` et `templates\notifications.html` — remplacés par `profile.html`
- `templates\pharmacies.html` — remplacé par `directory.html`
- `templates\nearest.html` — la route correspondante redirige désormais

### Fichiers auxquels je n'ai pas touché

`database.py`, `utils.py`, `schema.sql`, `static\js\app.js`,
`templates\map.html`, `templates\navigation.html`, `templates\pharmacy_detail.html`,
et surtout `data\pharmacies.db`.

---

## Étape 4 — Premier lancement

```powershell
python app.py
```

Au démarrage, `app.py` met automatiquement votre base à jour : création des
tables `favorites`, `notifications`, `search_history`, `scraped_guards`,
`scrape_runs`, `password_resets`, et ajout des colonnes `email`, `role`,
`phone`, `status`, `created_at` sur la table `users`. **Vos données existantes
sont conservées.** Le compte `admin` est promu administrateur, et son mot de
passe encore en clair sera converti en empreinte à votre prochaine connexion.

Videz ensuite le cache du navigateur (`Ctrl` + `F5`), sinon les anciennes
feuilles de style restent affichées.

---

## Étape 5 — Vérifier que tout fonctionne

Parcourez cette liste dans l'ordre :

1. **`http://127.0.0.1:5000/`** affiche l'écran vert en deux colonnes.
2. **Onglet « Créer un compte »** : pseudo, e-mail, téléphone, mot de passe et
   confirmation. Créez un compte de test.
3. **Avec ce compte** : l'annuaire s'ouvre, le cœur bascule sans recharger la
   page, le profil affiche les trois onglets Alertes / Favoris / Recherches.
4. **Déconnectez-vous** : l'assistant et le guidage restent accessibles, la
   carte et l'annuaire affichent un cadenas.
5. **Connexion `admin` / `admin123`** : la console s'ouvre sur `/admin`.
6. **Données scrapées** → « Lancer une collecte ». Les correspondances exactes
   sont publiées automatiquement, les autres attendent votre validation.
7. **Dans l'assistant**, tapez `pharmacie de garde à Agoè` puis
   `Au secours, accident, il ne respire plus` : le second message doit afficher
   les numéros de secours sur fond rouge.
8. **`/admin/analytics`** : le bloc « Compréhension des messages (NLU) » indique
   le modèle chargé et ses scores. S'il affiche un avertissement, c'est que
   scikit-learn n'est pas installé ou que `models\` est absent — l'application
   fonctionne alors avec les règles à mots-clés seules.

---

## Réglages dans `config.py`

| Réglage | Effet |
|---|---|
| `AUTO_SCRAPE_ENABLED` | active la collecte automatique en tâche de fond |
| `AUTO_SCRAPE_INTERVAL_HOURS` | délai entre deux collectes (24 h par défaut) |
| `AUTO_VALIDATE_EXACT_MATCH` | publie sans validation les correspondances exactes |
| `EMERGENCY_CONTACTS` | numéros de secours et leur ordre d'affichage |
| `EMERGENCY_CONFIDENCE_THRESHOLD` | sensibilité de la détection d'urgence (0.50) |
| `RESET_TOKEN_TTL_MINUTES` | durée de validité du lien de réinitialisation |
| `SMTP_HOST` et suivants | envoi réel des e-mails de réinitialisation |

Tant que `SMTP_HOST` est vide, le lien de réinitialisation s'affiche dans la
console du serveur au lieu d'être envoyé — pratique pour tester.

---

## En cas de problème

**`TemplateNotFound: _topbar.html`** — un gabarit commençant par un tiret bas
n'a pas été copié. Vérifiez la présence de `_topbar.html` et `_auth_landing.html`
dans `templates\`.

**`ImportError: cannot import name 'AUTO_SCRAPE_ENABLED'`** — `config.py` n'a
pas été remplacé, ou l'a été après `app.py`. Recopiez-le.

**`BuildError: Could not build url for endpoint '...'`** — `app.py` n'est pas à
jour par rapport aux gabarits. Recopiez `app.py`.

**Le design n'a pas changé** — cache du navigateur. `Ctrl` + `F5`, ou
`Ctrl` + `Shift` + `Suppr` pour vider le cache.

**La collecte renvoie « 0 nouvelle entrée »** — regardez le journal des
collectes sous le bouton : le planificateur automatique a probablement déjà
importé la liste quelques instants plus tôt.

**Les 404 sur `/sw.js` et `/favicon.ico`** dans la console sont normaux et sans
effet : votre application ne définit ni service worker ni icône d'onglet.

---

## Réentraîner le modèle plus tard

Le dossier `ml/` contient `generer_dataset.py`, `dataset_5000.csv` et le
notebook. Pour produire un nouveau modèle :

```powershell
cd ml
python generer_dataset.py
jupyter notebook entrainement_classifieur_intention.ipynb
```

Puis copiez les fichiers `intent_classifier.joblib` et `metadata.json` produits
dans le dossier `models\` de l'application.
