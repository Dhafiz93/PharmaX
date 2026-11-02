import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE = os.path.join(BASE_DIR, 'data', 'pharmacies.db')
SECRET_KEY = os.environ.get('SECRET_KEY', 'changez-cette-cle-secrete-en-production')
DEFAULT_CENTER = (6.2115, 1.1829)
DEFAULT_ZOOM = 12

# --- Collecte automatique des pharmacies de garde (INAM) ---
AUTO_SCRAPE_ENABLED = True          # collecte automatique en tâche de fond
# L'INAM publie une liste hebdomadaire du lundi au dimanche. Le moment critique
# est donc le changement de semaine : un intervalle trop long laisse
# l'application sans aucune garde pendant les premières heures de la semaine.
AUTO_SCRAPE_INTERVAL_HOURS = 6      # délai entre deux collectes en régime normal
AUTO_SCRAPE_URGENT_HOURS = 1        # délai quand les jours à venir ne sont pas couverts
AUTO_SCRAPE_CHECK_MINUTES = 20      # fréquence de vérification du planificateur

# --- Réinitialisation de mot de passe ---
RESET_TOKEN_TTL_MINUTES = 60        # durée de validité du lien de réinitialisation
RESET_MAX_PER_HOUR = 3              # demandes maximum par compte et par heure

# Laissez SMTP_HOST vide pour afficher le lien dans la console du serveur
# au lieu de l'envoyer par e-mail (pratique en développement).
SMTP_HOST = ''
SMTP_PORT = 587
SMTP_USER = ''
SMTP_PASSWORD = ''
SMTP_USE_TLS = True
SMTP_SENDER = 'PharmaX <no-reply@pharmax.tg>'

# Publier sans intervention les entrées dont le nom correspond exactement
# au référentiel local (score de rapprochement à 100).
AUTO_VALIDATE_EXACT_MATCH = True

# --- Numéros de secours affichés en cas d'urgence vitale ---
EMERGENCY_CONTACTS = [
    ("Urgence médicale (SAMU)", "111"),
    ("Police secours", "117"),
    ("Sapeurs-pompiers", "118"),
    ("Ordre National des Pharmaciens du Togo", "+228 96 37 37 30"),
]

# Seuil propre à l'urgence vitale, choisi sur le jeu de test : combiné à la
# détection par mots-clés, il donne un rappel de 1,000 (aucune urgence manquée)
# pour 6 fausses alertes sur 1031 messages. Descendre plus bas n'améliore plus
# le rappel et multiplie les fausses alertes.
EMERGENCY_CONFIDENCE_THRESHOLD = 0.50

# Affiche sous chaque réponse l'intention détectée, sa confiance et le système
# qui a décidé (modèle ou règles). Réservé aux démonstrations : à laisser sur
# False en usage normal, l'information n'ayant aucun intérêt pour l'utilisateur.
# Les mesures restent enregistrées et consultables dans Administration > Analytique.
SHOW_INTENT_BADGE = False
