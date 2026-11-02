"""Démonstration : le classifieur d'intention à l'œuvre dans l'application.

À lancer depuis la racine du projet, à côté de `app.py` :

    python tester_classifieur.py

Le script compare, sur les mêmes phrases, ce qu'aurait répondu l'ancien système
à mots-clés et ce que répond réellement l'application aujourd'hui. Il n'écrit
rien dans la base de données.
"""

import logging
import os
import sys

try:
    import config
    config.AUTO_SCRAPE_ENABLED = False       # pas de collecte pendant la démonstration
except Exception:
    pass

logging.disable(logging.CRITICAL)

VERT, ROUGE, GRIS, GRAS, RAZ = "\033[92m", "\033[91m", "\033[90m", "\033[1m", "\033[0m"
if os.name == "nt" and not os.environ.get("WT_SESSION"):
    VERT = ROUGE = GRIS = GRAS = RAZ = ""

import nlu
import intent_classifier

if not intent_classifier.is_available():
    print(f"{ROUGE}Le modèle n'est pas chargé :{RAZ} "
          f"{intent_classifier.model_info().get('error')}")
    print("L'application tourne alors uniquement avec les mots-clés.")
    sys.exit(1)

infos = intent_classifier.model_info()
print(f"{GRAS}Modèle chargé{RAZ} : {infos.get('model')} · "
      f"seuil {infos.get('confidence_threshold')} · "
      f"{len(infos.get('labels', []))} intentions")

# Chaque entrée : (phrase, intention attendue, ce que la phrase illustre)
PHRASES = [
    # --- Salutations en langue locale : aucun mot-clé français ---
    ("Woezon", "greeting", "salutation en mina"),
    ("Efoa ?", "greeting", "salutation en éwé"),
    ("ça donne ?", "greeting", "salutation familière togolaise"),

    # --- Demandes d'aide sans le mot « aide » ---
    ("tu sers à quoi", "help", "demande d'aide reformulée"),
    ("c'est quoi cette appli", "help", "question sur l'application"),
    ("kesk tu peux faire pour moi", "help", "style SMS"),
    ("montre moi les fonctionnalités", "help", "aucun mot-clé de la liste"),
    ("je débute, explique stp", "help", "formulation indirecte"),

    # --- Itinéraire sans les mots « route », « chemin », « guide » ---
    ("emmène moi là-bas", "route", "demande de guidage implicite"),
    ("trace le trajet", "route", "verbe non prévu par les règles"),
    ("le zed doit passer par où", "route", "vocabulaire de la rue"),
    ("montre-moi la direction", "route", "reformulation"),

    # --- Proximité sans l'expression « plus proche » ---
    ("celle qui est pas loin d'ici", "nearest_guard", "proximité implicite"),
    ("y a quelle officine dans mon coin", "nearest_guard", "« officine », « mon coin »"),
    ("prends ma position gps", "nearest_guard", "demande de géolocalisation"),

    # --- Recherche de garde avec fautes et synonymes ---
    ("une officine ouverte à Bè ce soir", "guard", "synonyme de pharmacie"),
    ("phamarcie de gard a Agoe svp", "guard", "fautes de frappe"),
    ("mon ordonnance à retirer ce soir", "guard", "besoin exprimé sans mot-clé"),

    # --- Urgences vitales ---
    ("Au secours mon frère s'est évanoui", "emergency", "urgence vitale"),
    ("mon bébé ne respire plus", "emergency", "urgence vitale"),

    # --- Contre-exemples : ne doivent PAS déclencher l'urgence ---
    ("pharmacie urgence Agoè", "guard", "le mot « urgence » ne suffit pas"),
    ("pharmacie ouverte pour une urgence dentaire", "guard", "urgence non vitale"),
]

print(f"\n{GRAS}Comparaison sur {len(PHRASES)} phrases{RAZ}")
print(f"{GRIS}« Règles » = ce que répondait l'application avant le modèle.{RAZ}\n")

entete = f"{'PHRASE':<46}{'RÈGLES':<15}{'APPLICATION':<15}"
print(GRAS + entete + RAZ)
print("-" * 92)

corriges, corrects, faux = 0, 0, 0
for phrase, attendu, illustration in PHRASES:
    regles = nlu.detect_intent_rules(phrase)
    final, confiance, origine = nlu.detect_intent_detailed(phrase)

    juste = final == attendu
    corrects += juste
    if not juste:
        faux += 1

    marque = ""
    if regles != attendu and juste:
        corriges += 1
        marque = f"{VERT}  ← corrigé par le modèle{RAZ}"
    elif not juste:
        marque = f"{ROUGE}  ← attendu : {attendu}{RAZ}"

    couleur_regles = ROUGE if regles != attendu else GRIS
    print(f"{phrase[:44]:<46}{couleur_regles}{regles:<15}{RAZ}{final:<15}{marque}")
    print(f"{GRIS}   {illustration} · confiance {confiance:.2f} · décidé par : {origine}{RAZ}")

print("-" * 92)
print(f"  {corrects}/{len(PHRASES)} correctement classées par l'application")
print(f"  {VERT}{corriges} phrases que les règles seules rataient{RAZ}")
if faux:
    print(f"  {ROUGE}{faux} erreur(s){RAZ}")

# ---------------------------------------------------------------------------
print(f"\n{GRAS}Réponses réellement produites par l'application{RAZ}")
print(f"{GRIS}Ce sont les textes que vous verrez dans le chat.{RAZ}\n")

from app import app
client = app.test_client()

for phrase in ["Woezon", "tu sers à quoi", "emmène moi là-bas",
               "prends ma position gps", "Au secours mon frère s'est évanoui"]:
    donnees = client.post("/api/chat", json={"message": phrase}).get_json()
    reponse = (donnees.get("reply") or "").replace("\n", " ")
    print(f"  {GRAS}«{phrase}»{RAZ}  → intention : {donnees.get('intent')}")
    print(f"  {GRIS}{reponse[:150]}{'…' if len(reponse) > 150 else ''}{RAZ}\n")

print(f"{GRAS}Pour le démontrer en direct{RAZ}")
print("  Lancez l'application, ouvrez l'assistant, et tapez les phrases ci-dessus.")
print("  Sans le modèle, toutes recevraient la même réponse : la liste des")
print("  pharmacies de garde du jour. Avec le modèle, chacune reçoit la réponse")
print("  qui lui correspond.")
print(f"\n{GRAS}Pour prouver que c'est bien le modèle qui décide{RAZ}")
print("  Renommez models\\intent_classifier.joblib, relancez l'application, et")
print("  retapez « tu sers à quoi » : vous obtiendrez de nouveau la liste des")
print("  pharmacies de garde. Remettez le fichier pour rétablir le comportement.")
