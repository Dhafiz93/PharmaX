"""Vérification automatique de l'installation de PharmaX.

À lancer depuis la racine du projet, à côté de `app.py` :

    python verifier_installation.py

Le script parcourt l'application comme le ferait un utilisateur, sans démarrer
de serveur ni ouvrir de navigateur. Il crée un compte de test, l'utilise, puis
efface tout ce qu'il a créé. Vos pharmacies, vos gardes et vos comptes existants
ne sont jamais modifiés.
"""

import io
import json
import logging
import os
import re
import sys
import unittest.mock as mock
from datetime import date, timedelta

# --- Le planificateur de collecte ne doit pas se déclencher pendant les tests ---
try:
    import config
    config.AUTO_SCRAPE_ENABLED = False
except Exception:
    pass

VERT, ROUGE, JAUNE, GRIS, RAZ = "\033[92m", "\033[91m", "\033[93m", "\033[90m", "\033[0m"
if os.name == "nt" and not os.environ.get("WT_SESSION"):
    VERT = ROUGE = JAUNE = GRIS = RAZ = ""   # ancienne console Windows

resultats = {"ok": 0, "echec": 0, "avertissement": 0}


def titre(texte):
    print("\n" + "=" * 62)
    print("  " + texte)
    print("=" * 62)


def verifier(libelle, condition, detail="", critique=True):
    if condition:
        resultats["ok"] += 1
        print(f"  {VERT}OK{RAZ}    {libelle}")
    elif critique:
        resultats["echec"] += 1
        print(f"  {ROUGE}ECHEC{RAZ} {libelle}")
        if detail:
            print(f"        {GRIS}{detail}{RAZ}")
    else:
        resultats["avertissement"] += 1
        print(f"  {JAUNE}ATTENTION{RAZ} {libelle}")
        if detail:
            print(f"        {GRIS}{detail}{RAZ}")
    return bool(condition)


# ===========================================================================
titre("1. Dépendances Python")
# ===========================================================================

for module, obligatoire, role in [
    ("flask", True, "serveur web"),
    ("requests", True, "collecte INAM"),
    ("bs4", True, "analyse des pages INAM"),
    ("sklearn", False, "classifieur d'intention"),
    ("joblib", False, "chargement du modèle"),
]:
    try:
        __import__(module)
        verifier(f"{module} installé ({role})", True)
    except ImportError:
        verifier(f"{module} installé ({role})", False,
                 f"python -m pip install {module}", critique=obligatoire)


# ===========================================================================
titre("2. Fichiers attendus")
# ===========================================================================

for chemin, obligatoire in [
    ("app.py", True), ("config.py", True), ("nlu.py", True),
    ("intent_classifier.py", True), ("scraper_inam.py", True),
    ("database.py", True), ("data/pharmacies.db", True),
    ("templates/_topbar.html", True), ("templates/_auth_landing.html", True),
    ("templates/profile.html", True), ("templates/directory.html", True),
    ("templates/admin_base.html", True), ("templates/admin_scraped.html", True),
    ("templates/password_forgot.html", True),
    ("static/css/topbar.css", True), ("static/css/app-ui.css", True),
    ("static/css/admin.css", True), ("static/css/auth.css", True),
    ("models/intent_classifier.joblib", False),
    ("models/metadata.json", False),
]:
    verifier(f"{chemin}", os.path.exists(chemin),
             "fichier manquant", critique=obligatoire)


# ===========================================================================
titre("3. Démarrage de l'application et migration de la base")
# ===========================================================================

logging.disable(logging.CRITICAL)   # on masque les logs pendant les tests
try:
    from app import app
    from database import get_db_connection
    verifier("app.py démarre sans erreur", True)
except Exception as exc:
    verifier("app.py démarre sans erreur", False, str(exc))
    print(f"\n{ROUGE}Arrêt : l'application ne démarre pas.{RAZ}")
    sys.exit(1)

conn = get_db_connection()
tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
for table in ["pharmacies", "guard_schedules", "users", "favorites", "notifications",
              "search_history", "scraped_guards", "scrape_runs", "password_resets"]:
    verifier(f"table `{table}`", table in tables, "migration incomplète")

colonnes = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
for colonne in ["email", "role", "phone", "status", "created_at"]:
    verifier(f"colonne users.{colonne}", colonne in colonnes)

nb_pharmacies = conn.execute("SELECT COUNT(*) FROM pharmacies").fetchone()[0]
nb_admins = conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
verifier(f"{nb_pharmacies} pharmacie(s) en base", nb_pharmacies > 0,
         "la base semble vide")
verifier(f"{nb_admins} compte(s) administrateur", nb_admins > 0,
         "aucun administrateur : vous ne pourrez pas ouvrir la console")
conn.close()


# ===========================================================================
titre("4. Pages accessibles sans compte")
# ===========================================================================

invite = app.test_client()
for url, libelle in [("/", "page d'accueil"), ("/assistant", "assistant"),
                     ("/navigation", "guidage GPS"), ("/login", "connexion"),
                     ("/register", "inscription"),
                     ("/mot-de-passe-oublie", "mot de passe oublié")]:
    code = invite.get(url).status_code
    verifier(f"{libelle} ({url})", code == 200, f"code HTTP {code}")

titre("5. Pages fermées aux visiteurs sans compte")

for url in ["/pharmacies", "/profil", "/carte", "/garde", "/admin"]:
    code = invite.get(url).status_code
    verifier(f"{url} redirige le visiteur", code == 302, f"code HTTP {code}")


# ===========================================================================
titre("6. Création de compte et espace utilisateur")
# ===========================================================================

PSEUDO = "verif_pharmax_temp"
EMAIL = "verif_pharmax_temp@exemple.test"
MDP = "motdepasse123"

conn = get_db_connection()
conn.execute("DELETE FROM users WHERE username = ?", (PSEUDO,))
conn.commit()
conn.close()

client = app.test_client()

reponse = client.post("/register", data={
    "username": PSEUDO, "email": EMAIL, "phone": "+228 90 00 00 00",
    "full_name": "Compte De Verification",
    "password": MDP, "password_confirm": "different"}, follow_redirects=True)
verifier("mots de passe différents refusés",
         "ne correspondent pas" in reponse.get_data(as_text=True))

reponse = client.post("/register", data={
    "username": PSEUDO, "email": EMAIL,
    "password": MDP, "password_confirm": MDP}, follow_redirects=True)
verifier("téléphone obligatoire à l'inscription",
         "obligatoires" in reponse.get_data(as_text=True))

reponse = client.post("/register", data={
    "username": PSEUDO, "email": EMAIL, "phone": "+228 90 00 00 00",
    "full_name": "Compte De Verification",
    "password": MDP, "password_confirm": MDP}, follow_redirects=True)
verifier("inscription complète acceptée",
         "Favoris" in reponse.get_data(as_text=True))

for url, libelle in [("/pharmacies", "annuaire"), ("/profil", "profil"),
                     ("/garde", "pharmacies de garde"), ("/carte", "carte")]:
    code = client.get(url).status_code
    verifier(f"{libelle} accessible avec un compte", code == 200, f"code HTTP {code}")

verifier("console d'administration fermée à cet utilisateur",
         client.get("/admin").status_code == 302)

conn = get_db_connection()
ligne = conn.execute("SELECT id, name FROM pharmacies ORDER BY name LIMIT 1").fetchone()
conn.close()

if ligne:
    reponse = client.post(f"/favoris/basculer/{ligne['id']}",
                          headers={"X-Requested-With": "fetch"})
    donnees = reponse.get_json() or {}
    verifier("ajout d'un favori (bouton cœur)", donnees.get("active") is True)
    verifier("le favori apparaît dans le profil",
             ligne["name"] in client.get("/profil").get_data(as_text=True))

client.post("/api/chat", json={"message": "pharmacie de garde la plus proche"})
verifier("la recherche est enregistrée dans l'historique",
         "plus proche" in client.get("/profil").get_data(as_text=True))


# ===========================================================================
titre("7. Assistant conversationnel")
# ===========================================================================

for message, attendu, libelle in [
    ("Bonjour", "greeting", "salutation"),
    ("pharmacie de garde à Agoè", "guard", "recherche de garde"),
    ("la pharmacie de garde la plus proche de moi", "nearest_guard", "plus proche"),
    ("combien de pharmacies sont de garde", "stats", "statistiques"),
    ("guide moi jusqu'à la pharmacie", "route", "itinéraire"),
    ("tu sers à quoi", "help", "aide"),
    ("phamarcie de gard a Be svp", "guard", "faute de frappe comprise"),
]:
    donnees = client.post("/api/chat", json={"message": message}).get_json()
    verifier(f"{libelle} → {attendu}", donnees.get("intent") == attendu,
             f"intention obtenue : {donnees.get('intent')}")


# ===========================================================================
titre("8. Détection des urgences vitales")
# ===========================================================================

for message in ["Au secours mon frère s'est évanoui",
                "accident de la route il y a du sang",
                "mon bébé ne respire plus",
                "il a été mordu par un serpent"]:
    donnees = client.post("/api/chat", json={"message": message}).get_json()
    detectee = donnees.get("intent") == "emergency" and donnees.get("emergency")
    verifier(f"« {message[:38]}… » → urgence", detectee)

donnees = client.post("/api/chat", json={"message": "au secours accident grave"}).get_json()
reponse_urgence = donnees.get("reply", "")
for numero in ["111", "117", "118", "96 37 37 30"]:
    verifier(f"le numéro {numero} est affiché", numero in reponse_urgence)
verifier("aucune pharmacie n'est proposée pendant une urgence",
         not donnees.get("pharmacies"))

for message in ["pharmacie urgence Agoè", "pharmacie de garde cette nuit"]:
    donnees = client.post("/api/chat", json={"message": message}).get_json()
    verifier(f"pas de fausse alerte sur « {message} »",
             donnees.get("intent") != "emergency")


# ===========================================================================
titre("9. Classifieur d'intention")
# ===========================================================================

try:
    import intent_classifier
    infos = intent_classifier.model_info()
    if infos.get("available"):
        verifier(f"modèle chargé ({infos.get('model')})", True)
        verifier(f"F1-macro annoncé : {infos.get('test_f1_macro', 0):.3f}",
                 infos.get("test_f1_macro", 0) > 0.8)
        verifier(f"{len(infos.get('labels', []))} intentions reconnues",
                 "emergency" in infos.get("labels", []))
    else:
        verifier("modèle d'intention chargé", False,
                 f"{infos.get('error')} — l'application fonctionne avec les mots-clés",
                 critique=False)

    import nlu
    verifier("repli sur les mots-clés opérationnel",
             nlu.detect_intent_rules("pharmacie de garde") == "guard")
except Exception as exc:
    verifier("module intent_classifier", False, str(exc), critique=False)


# ===========================================================================
titre("10. Console d'administration")
# ===========================================================================

conn = get_db_connection()
admin = conn.execute("SELECT username FROM users WHERE role='admin' LIMIT 1").fetchone()
conn.close()

admin_client = app.test_client()
connecte = False
if admin:
    reponse = admin_client.post("/login",
                                data={"username": admin["username"], "password": "admin123"})
    connecte = reponse.status_code == 302 and "/admin" in (reponse.headers.get("Location") or "")

if connecte:
    verifier(f"connexion administrateur (@{admin['username']})", True)
    for url, libelle in [("/admin", "tableau de bord"),
                         ("/admin/pharmacies", "gestion des pharmacies"),
                         ("/admin/scraped", "données scrapées"),
                         ("/admin/guards", "gardes"),
                         ("/admin/users", "utilisateurs"),
                         ("/admin/analytics", "analytique")]:
        code = admin_client.get(url).status_code
        verifier(f"{libelle} ({url})", code == 200, f"code HTTP {code}")
else:
    verifier("connexion administrateur", False,
             "mot de passe différent de admin123 : testez la console à la main",
             critique=False)


# ===========================================================================
titre("11. Collecte des pharmacies de garde")
# ===========================================================================

try:
    import scraper_inam

    debut = date.today() + timedelta(days=200)
    debut = debut.replace(day=5)
    fin = debut.replace(day=11)
    mois = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
            "août", "septembre", "octobre", "novembre", "décembre"][fin.month - 1]
    page = (f"<p>Liste des pharmacies du {debut.day} au {fin.day} {mois} {fin.year}</p>"
            "<table><tbody>"
            "<tr><td>PHARMACIES</td><td>TELEPHONES</td><td>EMPLACEMENTS</td></tr>"
            "<tr><td>PHARMACIE TEST VERIFICATION</td><td>☎90 00 00 00</td><td>Quartier test</td></tr>"
            "</tbody></table>")

    analyse = scraper_inam.parse_guard_page(page)
    verifier("lecture du tableau de la page INAM",
             len(analyse["entries"]) == 1)
    verifier("lecture de la période de garde",
             len(analyse["period_dates"]) == 7,
             f"{len(analyse['period_dates'])} jour(s) lus au lieu de 7")

    if connecte:
        reponse = admin_client.post("/admin/scraped/run", follow_redirects=True)
        texte = reponse.get_data(as_text=True)
        reussi = "Collecte terminée" in texte
        verifier("collecte réelle sur le site de l'INAM", reussi,
                 "site injoignable ou mise en page modifiée — "
                 "regardez le journal des collectes dans la console",
                 critique=False)
except Exception as exc:
    verifier("module scraper_inam", False, str(exc), critique=False)


# ===========================================================================
titre("12. Mot de passe oublié")
# ===========================================================================

journal = io.StringIO()
handler = logging.StreamHandler(journal)
logging.disable(logging.NOTSET)
app.logger.addHandler(handler)
reponse = client.post("/mot-de-passe-oublie", data={"identifier": EMAIL},
                      follow_redirects=True)
app.logger.removeHandler(handler)

verifier("message neutre affiché",
         "Si un compte correspond" in reponse.get_data(as_text=True))

lien = re.search(r"(/reinitialiser/[A-Za-z0-9_\-]+)", journal.getvalue())
if lien:
    verifier("lien de réinitialisation généré", True)
    code = client.get(lien.group(1)).status_code
    verifier("le lien ouvre le formulaire", code == 200)
    reponse = client.post(lien.group(1),
                          data={"password": "nouveaumdp1", "password_confirm": "nouveaumdp1"},
                          follow_redirects=True)
    verifier("nouveau mot de passe enregistré",
             "Mot de passe mis à jour" in reponse.get_data(as_text=True))
    verifier("le lien ne resert pas une seconde fois",
             "invalide ou a expiré" in client.get(lien.group(1), follow_redirects=True).get_data(as_text=True))
else:
    verifier("lien de réinitialisation généré", False,
             "si SMTP est configuré, le lien est parti par e-mail : c'est normal",
             critique=False)
logging.disable(logging.CRITICAL)


# ===========================================================================
titre("13. Nettoyage des données de test")
# ===========================================================================

try:
    conn = get_db_connection()
    ligne = conn.execute("SELECT id FROM users WHERE username = ?", (PSEUDO,)).fetchone()
    if ligne:
        identifiant = ligne["id"]
        for table in ["favorites", "notifications", "search_history", "password_resets"]:
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (identifiant,))
        conn.execute("DELETE FROM users WHERE id = ?", (identifiant,))
    conn.commit()
    conn.close()
    verifier("compte et données de test supprimés", True)
except Exception as exc:
    verifier("nettoyage", False, str(exc), critique=False)


# ===========================================================================
titre("Résultat")
# ===========================================================================

total = resultats["ok"] + resultats["echec"] + resultats["avertissement"]
print(f"  {VERT}{resultats['ok']} réussis{RAZ}"
      f" · {ROUGE}{resultats['echec']} échecs{RAZ}"
      f" · {JAUNE}{resultats['avertissement']} avertissements{RAZ}"
      f"  (sur {total} vérifications)")

if resultats["echec"] == 0:
    print(f"\n  {VERT}L'installation est fonctionnelle.{RAZ}")
    print("  Les avertissements signalent des fonctions optionnelles "
          "(modèle d'intention, réseau) qui n'empêchent pas l'application de tourner.")
else:
    print(f"\n  {ROUGE}Des vérifications ont échoué.{RAZ}")
    print("  Reprenez la section INSTALLATION.md correspondante avant de continuer.")

print("\n  Il reste à vérifier à la main, dans un navigateur :")
print("    - la géolocalisation et le guidage GPS sur la carte")
print("    - l'affichage sur téléphone (menu, barre du bas, tiroir latéral)")
print("    - l'envoi réel des e-mails si vous avez configuré SMTP")

sys.exit(1 if resultats["echec"] else 0)
