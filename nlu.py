import re
import unicodedata
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher

import intent_classifier


def normalize_text(value):
    value = unicodedata.normalize('NFD', value or '')
    value = ''.join(c for c in value if not unicodedata.combining(c))
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9\s\-']", ' ', value)
    return re.sub(r'\s+', ' ', value)


def parse_date_from_message(message):
    text = normalize_text(message)
    today = date.today()
    if 'apres demain' in text: return (today + timedelta(days=2)).isoformat()
    if 'demain' in text: return (today + timedelta(days=1)).isoformat()
    if 'hier' in text: return (today - timedelta(days=1)).isoformat()
    m = re.search(r'\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b', text)
    if m:
        d, mo, y = m.groups(); y = int(y) if y else today.year
        if y < 100: y += 2000
        try: return date(y, int(mo), int(d)).isoformat()
        except ValueError: pass
    return today.isoformat()


# Signaux d'urgence vitale. Volontairement larges : un faux positif coûte un
# message de trop, un faux négatif peut coûter bien plus cher.
# « urgence » seul est exclu : « pharmacie urgence Agoè » désigne une garde.
EMERGENCY_KEYWORDS = [
    'au secours', 'secours', 'accident', 'evanoui', 'evanouie', 's est evanoui',
    'inconscient', 'inconsciente', 'perte de connaissance', 'perdu connaissance',
    'ne respire plus', 'respire plus', 'ne respire pas', 'hemorragie', 'saigne',
    'saignement', 'sang partout', 'vomit du sang', 'convuls', 'epilepsie',
    'crise cardiaque', 'arret cardiaque', 'un avc', 'fait un avc',
    'attaque cerebrale',
    'morsure de serpent', 'mordu par un serpent', 'pique par un serpent',
    'brulure', 'brule gravement', 'empoisonn', 'intoxication', 'a bu de la javel',
    'avale du petrole', 'overdose', 'etouffe', 'suffoque', 'ambulance',
    'pompiers', 'police secours', 'samu', 'urgence vitale', 'coma', 'noyade',
    'accouche', 'accouchement', 'agression', 'en train de mourir',
    'trop de medicaments', 'trop de comprimes', 'a avale des medicaments',
    'a avale du', 'a bu du', 'ne se reveille pas', 'ne bouge plus',
    'danger de mort', 'blesse grave', 'fracture ouverte', 'choc anaphylactique',
    'balle dans', 'renverse par une voiture', 'tombe du toit', 'malaise grave',
    'numero des urgences', 'numero d urgence', 'numero des pompiers',
    'appeler les secours', 'cas grave', 'c est tres grave', 'tres grave',
]


def looks_like_emergency(message):
    """Détection par mots-clés d'une situation d'urgence vitale."""
    text = normalize_text(message)
    return any(mot in text for mot in EMERGENCY_KEYWORDS)


def detect_intent_rules(message):
    """Détection à base de mots-clés — comportement historique du projet."""
    text = normalize_text(message)
    if looks_like_emergency(message): return 'emergency'
    if any(x in text for x in ['bonjour','bonsoir','salut','hello','coucou']): return 'greeting'
    if any(x in text for x in ['aide','que peux tu','comment utiliser','commande']): return 'help'
    if any(x in text for x in ['combien','nombre','total']): return 'stats'
    if any(x in text for x in ['itineraire','route','chemin','guider','guide moi','navigation','amene moi']): return 'route'
    if any(x in text for x in ['plus proche','proche de moi','autour de moi','pres de moi','nearest']): return 'nearest_guard'
    if any(x in text for x in ['garde','pharmacie','urgence','ouverte la nuit']): return 'guard'
    return 'guard'


def detect_intent_detailed(message):
    """Renvoie (intention, confiance, origine).

    Le classifieur entraîne est interrogé en premier. On ne retient sa réponse
    que si elle est suffisamment sûre et exploitable par l'application ; sinon
    les règles à mots-clés reprennent la main, ce qui garantit que le
    comportement du chatbot reste au moins celui d'avant l'ajout du modèle.
    """
    label, confidence = intent_classifier.predict(message)

    # Urgence vitale : on retient l'alerte dès que l'un des deux systèmes la
    # signale, et avec un seuil de confiance abaissé pour le modèle.
    if looks_like_emergency(message):
        return 'emergency', max(confidence, 1.0), 'regles'
    if label == 'emergency' and confidence >= intent_classifier.emergency_threshold():
        return 'emergency', confidence, 'modele'

    if label and label != 'unknown' and confidence >= intent_classifier.threshold():
        return label, confidence, 'modele'
    return detect_intent_rules(message), confidence, 'regles'


def detect_intent(message):
    """Interface inchangée pour le reste de l'application."""
    intent, _, _ = detect_intent_detailed(message)
    return intent


def similarity(a,b):
    return SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()


# Mots à ignorer lors de la recherche d'un quartier dans une phrase.
PLACE_STOPWORDS = {
    'pharmacie', 'pharmacies', 'pharmaci', 'phamarcie', 'farmacie', 'pharma',
    'garde', 'gardes', 'de', 'du', 'des', 'la', 'le', 'les', 'un', 'une',
    'a', 'au', 'aux', 'en', 'et', 'ou', 'ce', 'cet', 'cette', 'ici', 'la',
    'soir', 'nuit', 'matin', 'jour', 'aujourd', 'hui', 'demain', 'urgence',
    'urgences', 'ouverte', 'ouvert', 'ouvertes', 'plus', 'proche', 'pres',
    'moi', 'je', 'tu', 'il', 'elle', 'on', 'y', 'est', 'sont', 'quelle',
    'quel', 'quelles', 'qui', 'que', 'quoi', 'ou', 'trouve', 'cherche',
    'donne', 'veux', 'besoin', 'svp', 'stp', 'merci', 'bonjour', 'salut',
    'cote', 'secteur', 'zone', 'vers', 'dans', 'pour', 'avec', 'chez',
}


def place_variants(value):
    """Formes normalisées d'un nom de lieu : avec et sans tirets."""
    base = normalize_text(value)
    return {base, base.replace('-', ' ')}


def extract_best_place(message, known_places):
    """Repère le quartier mentionné dans le message.

    Trois niveaux, du plus fiable au plus tolérant :
    1. le nom complet du quartier apparaît dans la phrase (« Agoè-Nyivé 1 ») ;
    2. un mot significatif du quartier apparaît (« Bè » pour « Bè Château ») —
       on renvoie alors ce mot seul, pour que le filtre retrouve tous les
       quartiers qui le contiennent ;
    3. un mot de la phrase ressemble fortement à un mot du quartier, ce qui
       rattrape les fautes de frappe (« Adidogome » pour « Adidogomé »).
    """
    text = normalize_text(message)
    text_plat = text.replace('-', ' ')
    tokens = [t for t in text_plat.split() if t not in PLACE_STOPWORDS]

    meilleur, meilleur_score, meilleure_taille = None, 0.0, 0

    for place in known_places or []:
        if not place:
            continue
        mots_place = [m for m in str(place).replace('-', ' ').split() if m]

        # 1. nom complet présent dans la phrase
        for variante in place_variants(place):
            if variante and (variante in text or variante in text_plat):
                if 1.0 > meilleur_score or len(variante) > meilleure_taille:
                    meilleur, meilleur_score, meilleure_taille = place, 1.0, len(variante)

        if meilleur_score >= 1.0 and meilleur == place:
            continue

        # 2. mot significatif du quartier présent tel quel
        for mot in mots_place:
            mot_norm = normalize_text(mot)
            if len(mot_norm) < 2 or mot_norm in PLACE_STOPWORDS:
                continue
            if mot_norm in tokens:
                score = 0.95
                if score > meilleur_score or (score == meilleur_score and len(mot_norm) > meilleure_taille):
                    meilleur, meilleur_score, meilleure_taille = mot, score, len(mot_norm)

        # 3. tolérance aux fautes de frappe
        for token in tokens:
            if len(token) < 4:
                continue
            for mot in mots_place:
                mot_norm = normalize_text(mot)
                if len(mot_norm) < 4:
                    continue
                ratio = SequenceMatcher(None, token, mot_norm).ratio()
                if ratio >= 0.82 and ratio > meilleur_score and meilleur_score < 0.95:
                    meilleur, meilleur_score, meilleure_taille = place, ratio, len(mot_norm)

    return meilleur if meilleur_score >= 0.8 else None


# Quartiers de Lomé et environs, y compris ceux qui ne comptent encore aucune
# pharmacie dans l'annuaire : cela permet de répondre « aucune pharmacie
# référencée à Hédzranawoé » plutôt que de renvoyer silencieusement toute la
# liste des gardes du jour.
LOME_QUARTIERS = [
    'Adakpamé', 'Adéwui', 'Adidogomé', 'Adjololo', 'Adétikopé', 'Aflao Gakli',
    'Agbalépédo', 'Agbalépédogan', 'Agoè', 'Agoè-Nyivé', 'Agoè-Nyivé 1',
    'Agoè-Nyivé 2', 'Agoè-Assiyéyé', 'Agoè-Logopé', 'Agoè-Zongo', 'Ahligo',
    'Akodessewa', 'Amoutivé', 'Amadahomé', 'Anfamé', 'Apédokoé', 'Assiganto',
    'Attiégou', 'Avénou', 'Avédji', 'Ablogamé', 'Baguida', 'Bè', 'Bè-Kpota',
    'Bè Château', 'Bè Klikamé', 'Cacavéli', 'Casablanca', 'Davié', 'Dékon',
    'Djagblé', 'Djidjolé', 'Doulassamé', 'Gbossimé', 'Hanoukopé', 'Hédzranawoé',
    'Hountigomé', 'Kanyikopé', 'Kégué', 'Kélégougan', 'Klikamé', 'Kodjoviakopé',
    'Legbassito', 'Lomé 2', 'Nukafu', 'Nyékonakpoè', 'Sagbado', 'Sanguéra',
    'Ségbé', 'Soviépé', 'Togblékopé', 'Tokoin', 'Tokoin Hôpital', 'Totsi',
    'Vakpossito', 'Wuiti', 'Zanguéra', 'Zopomahé',
]


def extract_mentioned_quartier(message):
    """Quartier de Lomé cité dans le message, qu'il soit ou non dans l'annuaire."""
    return extract_best_place(message, LOME_QUARTIERS)


def format_date_fr(iso_date):
    try: return datetime.strptime(iso_date, '%Y-%m-%d').strftime('%d/%m/%Y')
    except Exception: return iso_date
