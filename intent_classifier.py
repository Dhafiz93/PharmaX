"""Chargement et interrogation du classifieur d'intention entraîné.

Le modèle est optionnel : si scikit-learn n'est pas installé ou si le fichier
`models/intent_classifier.joblib` est absent, l'application continue de
fonctionner avec les seules règles à mots-clés de `nlu.py`.
"""

import json
import logging
import os
import threading

LOGGER = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE_DIR, 'models', 'intent_classifier.joblib')
METADATA_PATH = os.path.join(BASE_DIR, 'models', 'metadata.json')

DEFAULT_THRESHOLD = 0.60
DEFAULT_EMERGENCY_THRESHOLD = 0.30

_lock = threading.Lock()
_state = {'loaded': False, 'pipeline': None, 'metadata': {}, 'error': None}


def _load():
    """Charge le modèle une seule fois, sans jamais interrompre l'application."""
    if _state['loaded']:
        return
    with _lock:
        if _state['loaded']:
            return
        _state['loaded'] = True
        try:
            import joblib  # importé ici pour rester optionnel
        except ImportError as exc:
            _state['error'] = 'joblib/scikit-learn absent : %s' % exc
            LOGGER.warning('Classifieur d\'intention désactivé (%s). '
                           'Les règles à mots-clés prennent le relais.', _state['error'])
            return

        if not os.path.exists(MODEL_PATH):
            _state['error'] = 'fichier %s introuvable' % MODEL_PATH
            LOGGER.warning('Classifieur d\'intention désactivé (%s).', _state['error'])
            return

        try:
            _state['pipeline'] = joblib.load(MODEL_PATH)
        except Exception as exc:
            _state['error'] = 'chargement impossible : %s' % exc
            LOGGER.warning('Classifieur d\'intention désactivé (%s).', _state['error'])
            return

        if os.path.exists(METADATA_PATH):
            try:
                with open(METADATA_PATH, encoding='utf-8') as handle:
                    _state['metadata'] = json.load(handle)
            except Exception:
                _state['metadata'] = {}

        LOGGER.info('Classifieur d\'intention chargé (%s, seuil %.2f).',
                    _state['metadata'].get('model', 'modèle'), threshold())


def threshold():
    return float(_state['metadata'].get('confidence_threshold', DEFAULT_THRESHOLD))


def emergency_threshold():
    """Seuil abaissé : une urgence manquée coûte plus cher qu'une fausse alerte."""
    try:
        from config import EMERGENCY_CONFIDENCE_THRESHOLD
        return float(EMERGENCY_CONFIDENCE_THRESHOLD)
    except ImportError:
        return DEFAULT_EMERGENCY_THRESHOLD


def is_available():
    _load()
    return _state['pipeline'] is not None


def model_info():
    """Métadonnées du modèle, pour la page Analytique de l'administration."""
    _load()
    info = dict(_state['metadata'])
    info['available'] = _state['pipeline'] is not None
    info['error'] = _state['error']
    return info


def predict(message):
    """Renvoie (intention, confiance) ou (None, 0.0) si le modèle est indisponible.

    L'intention `unknown` est renvoyée telle quelle : c'est à l'appelant de
    décider quoi en faire (dans ce projet, `nlu.py` repasse aux mots-clés).
    """
    _load()
    pipeline = _state['pipeline']
    if pipeline is None or not (message or '').strip():
        return None, 0.0
    try:
        probabilities = pipeline.predict_proba([message])[0]
        classes = pipeline.classes_
        best = int(probabilities.argmax())
        return str(classes[best]), float(probabilities[best])
    except Exception as exc:
        LOGGER.warning('Prédiction impossible (%s), repli sur les mots-clés.', exc)
        return None, 0.0
