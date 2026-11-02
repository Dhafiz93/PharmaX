# PharmaAI Lomé

Application Flask avec une base de données SQLite pour l’identification, la recherche et la localisation en temps réel des pharmacies les plus proches.

## Fonctions principales

- interface conversationnelle moderne inspirée des assistants IA ;
- compréhension de demandes simples en français ;
- recherche par nom et par quartier ;
- recherche des pharmacies de garde par date ;
- géolocalisation de l’utilisateur depuis le navigateur ;
- calcul des trois pharmacies les plus proches avec la formule de Haversine ;
- accès direct à l’appel, aux détails et à l’itinéraire ;
- carte Leaflet et navigation ;
- administration des pharmacies et du calendrier de garde ;
- stockage exclusif dans SQLite ;
- journalisation des conversations dans la table `chat_logs`.

## Installation

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# Linux/macOS
source venv/bin/activate

pip install -r requirements.txt
python app.py
```

Ouvrir ensuite : `http://127.0.0.1:5000`

## Administration

- URL : `/login`
- utilisateur de démonstration : `admin`
- mot de passe de démonstration : `admin123`

Changez ces identifiants et la clé secrète dans `config.py` avant une mise en production.

## Base de données

Le fichier SQLite est situé dans `data/pharmacies.db`. Il contient 37 pharmacies géolocalisées issues du projet initial. Les gardes doivent être actualisées dans l’interface d’administration à partir de la liste officielle.

## Exemples de questions

- Quelle est la pharmacie la plus proche de moi ?
- Trouve une pharmacie à Agoè.
- Quelles pharmacies sont de garde aujourd’hui ?
- Combien de pharmacies sont enregistrées ?
- Donne-moi une pharmacie à Agoè-Nyivé 1.
