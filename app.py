import hashlib
import json
import os
import secrets
import smtplib
from email.message import EmailMessage
import string
import threading
import time
from datetime import date, datetime, timedelta
from flask import (Flask, render_template, request, redirect, url_for, flash,
                   session, jsonify, has_request_context, send_from_directory)
from werkzeug.security import check_password_hash, generate_password_hash
from config import (SECRET_KEY, DEFAULT_CENTER, DEFAULT_ZOOM,
                    AUTO_SCRAPE_ENABLED, AUTO_SCRAPE_INTERVAL_HOURS, AUTO_SCRAPE_CHECK_MINUTES,
                    AUTO_SCRAPE_URGENT_HOURS,
                    AUTO_VALIDATE_EXACT_MATCH, EMERGENCY_CONTACTS, SHOW_INTENT_BADGE,
                    RESET_TOKEN_TTL_MINUTES, RESET_MAX_PER_HOUR,
                    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_USE_TLS, SMTP_SENDER)
from database import get_db_connection
from utils import haversine_distance, to_float
import intent_classifier
from nlu import (detect_intent, detect_intent_detailed, parse_date_from_message,
                 extract_best_place, extract_mentioned_quartier, format_date_fr)

app = Flask(__name__)
app.secret_key = SECRET_KEY


def ensure_ai_tables():
    conn = get_db_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT NOT NULL,
            intent TEXT,
            response TEXT,
            user_lat REAL,
            user_lon REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scraped_guards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_name TEXT NOT NULL,
            normalized_name TEXT,
            phone TEXT,
            address TEXT,
            period_label TEXT,
            period_start TEXT,
            period_end TEXT,
            period_dates TEXT,
            matched_pharmacy_id INTEGER,
            match_score INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            source_url TEXT,
            fetched_at TEXT,
            decided_at TEXT,
            decided_by TEXT,
            UNIQUE(normalized_name, period_start)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            pharmacy_id INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, pharmacy_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            pharmacy_id INTEGER NOT NULL,
            guard_date TEXT NOT NULL,
            message TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            read_at TEXT,
            UNIQUE(user_id, pharmacy_id, guard_date)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS password_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS search_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            query TEXT NOT NULL,
            results_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scrape_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT,
            status TEXT,
            added INTEGER DEFAULT 0,
            message TEXT,
            started_at TEXT,
            finished_at TEXT
        )
    """)

    # Colonnes de suivi du classifieur, ajoutées après coup
    colonnes_logs = {row[1] for row in conn.execute('PRAGMA table_info(chat_logs)')}
    for colonne, ddl in [
        ('intent_source', "ALTER TABLE chat_logs ADD COLUMN intent_source TEXT"),
        ('confidence', "ALTER TABLE chat_logs ADD COLUMN confidence REAL"),
        ('feedback', "ALTER TABLE chat_logs ADD COLUMN feedback INTEGER"),
    ]:
        if colonne not in colonnes_logs:
            conn.execute(ddl)

    # Colonnes ajoutées après coup sur la table users
    existing = {row[1] for row in conn.execute('PRAGMA table_info(users)')}
    for column, ddl in [
        ('email', "ALTER TABLE users ADD COLUMN email TEXT"),
        ('role', "ALTER TABLE users ADD COLUMN role TEXT"),
        ('created_at', "ALTER TABLE users ADD COLUMN created_at TEXT"),
        ('phone', "ALTER TABLE users ADD COLUMN phone TEXT"),
        ('status', "ALTER TABLE users ADD COLUMN status TEXT"),
    ]:
        if column not in existing:
            conn.execute(ddl)
    # Les comptes déjà présents avant cette migration sont des administrateurs
    conn.execute("UPDATE users SET role = 'admin' WHERE role IS NULL OR role = ''")
    conn.execute("UPDATE users SET status = 'active' WHERE status IS NULL OR status = ''")
    # Filet de sécurité : il doit toujours rester au moins un administrateur
    admins = conn.execute("SELECT COUNT(*) AS n FROM users WHERE role = 'admin'").fetchone()['n']
    if not admins:
        conn.execute(
            "UPDATE users SET role = 'admin' WHERE id = (SELECT MIN(id) FROM users)"
        )
    conn.commit()
    conn.close()


def count_pending_scraped():
    conn = get_db_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM scraped_guards WHERE status = 'pending'"
    ).fetchone()
    conn.close()
    return row['n']


def serialize_pharmacy(row, distance_km=None):
    item = dict(row)
    if distance_km is not None:
        item['distance_km'] = round(distance_km, 2)
    return item


def get_guard_source(conn):
    row = conn.execute("SELECT * FROM guard_sources ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def chat_search(message, user_lat=None, user_lon=None):
    intent, intent_confidence, intent_source = detect_intent_detailed(message)
    target_date = parse_date_from_message(message)
    conn = get_db_connection()
    source = get_guard_source(conn)
    places = [r['quartier'] for r in conn.execute(
        "SELECT DISTINCT quartier FROM pharmacies WHERE quartier IS NOT NULL AND quartier <> ''"
    ).fetchall()]
    place = extract_best_place(message, places)

    guard_sql = """
        SELECT p.*, 1 AS is_on_guard
        FROM guard_schedules gs
        JOIN pharmacies p ON p.id = gs.pharmacy_id
        WHERE gs.guard_date = ? AND p.status = 'active'
    """
    params = [target_date]
    pharmacies = []

    if intent == 'emergency':
        # Priorité absolue : les numéros de secours d'abord, sans détour.
        numeros = ' · '.join('%s : %s' % (libelle, numero)
                             for libelle, numero in EMERGENCY_CONTACTS)
        reply = (
            "⚠️ URGENCE VITALE — appelez immédiatement les secours, "
            "ne perdez pas de temps avec cette application.\n\n"
            + numeros
            + "\n\nJe ne suis pas un service médical et je ne peux pas porter secours. "
            "Une fois les secours prévenus, je peux vous indiquer la pharmacie de garde "
            "la plus proche si vous en avez besoin."
        )
        curseur = conn.execute(
            """INSERT INTO chat_logs(message, intent, response, user_lat, user_lon,
                                    intent_source, confidence)
               VALUES (?,?,?,?,?,?,?)""",
            (message, intent, reply, user_lat, user_lon, intent_source,
             round(intent_confidence, 3)),
        )
        conn.commit()
        log_id = curseur.lastrowid
        conn.close()
        return {'reply': reply, 'pharmacies': [], 'intent': intent, 'log_id': log_id,
                'date': target_date, 'place': place, 'emergency': True,
                'intent_confidence': round(intent_confidence, 2),
                'intent_source': intent_source,
                'show_intent': SHOW_INTENT_BADGE}

    if intent == 'greeting':
        reply = "Bonjour ! Je recherche uniquement les pharmacies de garde. Dites : « Trouve la pharmacie de garde la plus proche de moi » et autorisez votre position."
    elif intent == 'help':
        reply = "Je peux identifier les pharmacies de garde du jour, les classer selon votre position et vous guider sur une carte animée jusqu’à la pharmacie choisie."
    elif intent == 'stats':
        guarded = conn.execute("SELECT COUNT(DISTINCT pharmacy_id) AS n FROM guard_schedules WHERE guard_date=?", (target_date,)).fetchone()['n']
        reply = f"Pour le {format_date_fr(target_date)}, {guarded} pharmacie(s) de garde géolocalisée(s) sont disponibles."
    elif intent in ('nearest', 'nearest_guard', 'route'):
        if user_lat is None or user_lon is None:
            reply = "J’ai besoin de votre position pour trouver la pharmacie de garde réellement la plus proche. Cliquez sur le bouton de localisation, puis acceptez l’autorisation GPS."
        else:
            rows = conn.execute(guard_sql + " ORDER BY p.name", params).fetchall()
            ranked = []
            for row in rows:
                if row['latitude'] is None or row['longitude'] is None:
                    continue
                dist = haversine_distance(user_lat, user_lon, row['latitude'], row['longitude'])
                ranked.append((dist, row))
            ranked.sort(key=lambda x: x[0])
            pharmacies = [serialize_pharmacy(row, dist) for dist, row in ranked[:5]]
            if pharmacies:
                first = pharmacies[0]
                reply = (f"La pharmacie de garde la plus proche est {first['name']}, à environ "
                         f"{first['distance_km']} km. Appuyez sur « Démarrer le guidage » pour suivre le trajet en direct.")
            else:
                reply = f"Aucune pharmacie de garde géolocalisée n’est disponible pour le {format_date_fr(target_date)}. Je ne vous proposerai pas une pharmacie ordinaire à la place."
    else:
        query = guard_sql
        if place:
            query += " AND (p.quartier LIKE ? OR p.address LIKE ?)"
            params.extend([f"%{place}%", f"%{place}%"])
        query += " ORDER BY p.name LIMIT 20"
        rows = conn.execute(query, params).fetchall()
        pharmacies = [serialize_pharmacy(r) for r in rows]

        # Quartier cité par l'utilisateur mais absent de l'annuaire : on le dit
        # au lieu de renvoyer silencieusement toutes les gardes de la ville.
        quartier_cite = extract_mentioned_quartier(message) if not place else None

        if pharmacies and place:
            reply = (f"Voici les pharmacies de garde du secteur de {place} pour le "
                     f"{format_date_fr(target_date)}. Activez votre position pour que "
                     "je classe les plus proches.")
        elif pharmacies and quartier_cite:
            reply = (f"Aucune pharmacie de l'annuaire n'est située à {quartier_cite} : "
                     f"l'annuaire ne couvre pas encore ce quartier. Voici les "
                     f"{len(pharmacies)} pharmacie(s) de garde du {format_date_fr(target_date)} "
                     "pour toute la zone couverte.")
        elif pharmacies:
            reply = (f"Voici les pharmacies de garde pour le {format_date_fr(target_date)}. "
                     "Activez votre position pour que je classe les plus proches.")
        elif place:
            reply = (f"Aucune pharmacie de garde n'est enregistrée à {place} pour le "
                     f"{format_date_fr(target_date)}.")
        elif quartier_cite:
            reply = (f"Aucune pharmacie n'est référencée à {quartier_cite} dans l'annuaire, "
                     f"et aucune garde n'est enregistrée pour le {format_date_fr(target_date)}.")
        else:
            reply = f"Aucune pharmacie de garde n'est enregistrée pour le {format_date_fr(target_date)}."

    curseur = conn.execute(
        """INSERT INTO chat_logs(message, intent, response, user_lat, user_lon,
                                intent_source, confidence)
           VALUES (?,?,?,?,?,?,?)""",
        (message, intent, reply, user_lat, user_lon, intent_source,
         round(intent_confidence, 3)),
    )
    conn.commit()
    log_id = curseur.lastrowid
    conn.close()
    return {
        'reply': reply,
        'intent': intent,
        'log_id': log_id,
        'intent_confidence': round(intent_confidence, 2),
        'intent_source': intent_source,
        'show_intent': SHOW_INTENT_BADGE,
        'pharmacies': pharmacies,
        'target_date': target_date,
        'source': source,
    }

ensure_ai_tables()


def verify_password(stored, given):
    """Accepte les mots de passe hachés et ceux encore en clair (ancienne base)."""
    if not stored:
        return False
    if stored.startswith(('pbkdf2:', 'scrypt:', 'argon2')):
        return check_password_hash(stored, given)
    return stored == given


def set_session_identity(full_name, username, email):
    """Mémorise le nom affiché, l'e-mail et les initiales pour le bandeau."""
    name = (full_name or username or '').strip() or username
    parts = [part for part in name.replace('-', ' ').split() if part]
    initials = ''.join(part[0] for part in parts[:2]).upper() or name[:2].upper()
    session['display_name'] = name
    session['email'] = email or ''
    session['initials'] = initials


def require_account():
    """Pages réservées aux comptes. Renvoie une redirection, ou None si l'accès est permis.

    Les visiteurs sans compte gardent l'assistant conversationnel et le guidage ;
    l'annuaire, la carte, les gardes et l'espace personnel demandent un compte.
    """
    if is_logged_in():
        return None
    flash('Créez un compte gratuit pour accéder à cette page. '
          "L'assistant et le guidage restent accessibles sans compte.", 'info')
    return redirect(url_for('register'))


def is_admin():
    return session.get('role') == 'admin'


def current_user_id():
    return session.get('user_id')


def unread_notifications_count():
    user_id = current_user_id()
    if not user_id:
        return 0
    conn = get_db_connection()
    row = conn.execute(
        'SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND read_at IS NULL',
        (user_id,),
    ).fetchone()
    conn.close()
    return row['n']


def favorite_ids(user_id=None):
    user_id = user_id or current_user_id()
    if not user_id:
        return set()
    conn = get_db_connection()
    rows = conn.execute('SELECT pharmacy_id FROM favorites WHERE user_id = ?', (user_id,)).fetchall()
    conn.close()
    return {row['pharmacy_id'] for row in rows}


def duty_periods_for(conn, pharmacy_ids=None):
    """Période de garde courante ou prochaine, par pharmacie.

    Renvoie {pharmacy_id: {'start': iso, 'end': iso, 'is_today': bool}}.
    """
    today = date.today().isoformat()
    query = 'SELECT pharmacy_id, guard_date FROM guard_schedules WHERE guard_date >= ?'
    params = [today]
    if pharmacy_ids:
        placeholders = ','.join('?' * len(pharmacy_ids))
        query += ' AND pharmacy_id IN (%s)' % placeholders
        params.extend(pharmacy_ids)
    query += ' ORDER BY pharmacy_id, guard_date'

    dates_by_pharmacy = {}
    for row in conn.execute(query, params):
        dates_by_pharmacy.setdefault(row['pharmacy_id'], []).append(row['guard_date'])

    result = {}
    for pharmacy_id, dates in dates_by_pharmacy.items():
        periods = group_consecutive_dates(dates)
        if not periods:
            continue
        first = periods[0]
        result[pharmacy_id] = {
            'start': first['start'],
            'end': first['end'],
            'is_today': first['start'] <= today <= first['end'],
        }
    return result


def log_search(query, results_count):
    """Journalise une recherche de l'assistant pour l'onglet « Recherches »."""
    user_id = current_user_id()
    if not user_id or not query:
        return
    conn = get_db_connection()
    conn.execute(
        'INSERT INTO search_history (user_id, query, results_count, created_at) VALUES (?, ?, ?, ?)',
        (user_id, query[:200], results_count, datetime.now().isoformat(timespec='seconds')),
    )
    # On conserve les 50 dernières recherches par compte
    conn.execute(
        """DELETE FROM search_history WHERE user_id = ? AND id NOT IN
           (SELECT id FROM search_history WHERE user_id = ? ORDER BY id DESC LIMIT 50)""",
        (user_id, user_id),
    )
    conn.commit()
    conn.close()


def notify_favorites(conn, pharmacy_id, dates):
    """Crée une alerte pour chaque utilisateur ayant mis cette pharmacie en favori."""
    if not dates:
        return 0
    pharmacy = conn.execute('SELECT name FROM pharmacies WHERE id = ?', (pharmacy_id,)).fetchone()
    if not pharmacy:
        return 0
    users = conn.execute('SELECT user_id FROM favorites WHERE pharmacy_id = ?', (pharmacy_id,)).fetchall()
    today = date.today().isoformat()
    created = 0
    for user in users:
        for guard_date in dates:
            if guard_date < today:
                continue
            message = '%s est de garde le %s.' % (pharmacy['name'], guard_date)
            cursor = conn.execute(
                """INSERT OR IGNORE INTO notifications (user_id, pharmacy_id, guard_date, message)
                   VALUES (?, ?, ?, ?)""",
                (user['user_id'], pharmacy_id, guard_date, message),
            )
            created += cursor.rowcount
    return created


def is_logged_in():
    return 'user_id' in session


def get_guard_pharmacies(target_date=None):
    target_date = target_date or date.today().isoformat()
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT gs.id AS schedule_id, gs.guard_date, gs.notes, p.*
        FROM guard_schedules gs
        JOIN pharmacies p ON p.id = gs.pharmacy_id
        WHERE gs.guard_date = ?
        ORDER BY p.name
        """,
        (target_date,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_stats():
    conn = get_db_connection()
    total = conn.execute('SELECT COUNT(*) AS n FROM pharmacies').fetchone()['n']
    guard_today = conn.execute(
        'SELECT COUNT(*) AS n FROM guard_schedules WHERE guard_date = ?',
        (date.today().isoformat(),),
    ).fetchone()['n']
    avg_acc = conn.execute(
        'SELECT ROUND(AVG(COALESCE(accuracy, 0)), 2) AS a FROM pharmacies'
    ).fetchone()['a']
    conn.close()
    return {'total': total, 'guard_today': guard_today, 'avg_acc': avg_acc}


def fetch_map_features(target_date=None):
    target_date = target_date or date.today().isoformat()
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT p.*, CASE WHEN gs.pharmacy_id IS NULL THEN 0 ELSE 1 END AS is_on_guard
        FROM pharmacies p
        LEFT JOIN guard_schedules gs
          ON gs.pharmacy_id = p.id AND gs.guard_date = ?
        WHERE p.status = 'active'
        ORDER BY p.name
        """,
        (target_date,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_selected_pharmacy(pharmacy_id):
    if not pharmacy_id:
        return None
    conn = get_db_connection()
    row = conn.execute(
        """
        SELECT p.*, CASE WHEN gs.pharmacy_id IS NULL THEN 0 ELSE 1 END AS is_on_guard
        FROM pharmacies p
        LEFT JOIN guard_schedules gs
          ON gs.pharmacy_id = p.id AND gs.guard_date = ?
        WHERE p.id = ?
        """,
        (date.today().isoformat(), pharmacy_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Console d'administration : utilitaires
# ---------------------------------------------------------------------------

MONTHS_FR = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet',
             'août', 'septembre', 'octobre', 'novembre', 'décembre']
DAYS_FR = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche']


def format_date_long(value):
    """'mercredi 12 août 2026'"""
    return '%s %d %s %d' % (DAYS_FR[value.weekday()], value.day,
                            MONTHS_FR[value.month - 1], value.year)


def group_consecutive_dates(dates):
    """Regroupe une liste de dates ISO triées en périodes consécutives."""
    periods = []
    for iso in sorted(set(dates)):
        try:
            current = date.fromisoformat(iso)
        except ValueError:
            continue
        if periods and date.fromisoformat(periods[-1]['end']) + timedelta(days=1) == current:
            periods[-1]['end'] = iso
            periods[-1]['days'] += 1
        else:
            periods.append({'start': iso, 'end': iso, 'days': 1})
    return periods


def guard_history_groups():
    """Historique des gardes regroupé par pharmacie puis par période."""
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT p.id, p.name, p.quartier, gs.guard_date
        FROM guard_schedules gs
        JOIN pharmacies p ON p.id = gs.pharmacy_id
        ORDER BY p.name, gs.guard_date
        """
    ).fetchall()
    conn.close()

    grouped = {}
    for row in rows:
        entry = grouped.setdefault(row['id'], {
            'id': row['id'], 'name': row['name'],
            'quartier': row['quartier'], 'dates': [],
        })
        entry['dates'].append(row['guard_date'])

    groups = []
    for entry in grouped.values():
        periods = group_consecutive_dates(entry['dates'])
        groups.append({
            'id': entry['id'],
            'name': entry['name'],
            'quartier': entry['quartier'],
            'periods': periods,
            'total': sum(p['days'] for p in periods),
        })
    groups.sort(key=lambda g: g['name'])
    return groups


def match_score_for(entry_name, pharmacies):
    """Rapproche un nom scrapé du référentiel local et renvoie (pharmacie, score)."""
    from scraper_inam import normalize_text

    target = normalize_text(entry_name)
    if not target:
        return None, 0
    target_tokens = set(target.split())
    best, best_score = None, 0
    for pharmacy in pharmacies:
        candidate = normalize_text(str(pharmacy['name']))
        if not candidate:
            continue
        if target == candidate:
            score = 100
        elif target in candidate or candidate in target:
            score = 80
        else:
            score = len(target_tokens & set(candidate.split())) * 10
        if score > best_score:
            best, best_score = pharmacy, score
    if best_score >= 10:
        return best, best_score
    return None, 0


def decorate_scraped(row):
    """Ajoute les champs d'affichage d'une entrée scrapée."""
    item = dict(row)
    dates = json.loads(item.get('period_dates') or '[]')
    item['day_count'] = len(dates) or 1
    if item.get('period_start') and item.get('period_end'):
        if item['period_start'] == item['period_end']:
            item['period_display'] = item['period_start']
        else:
            item['period_display'] = 'du %s au %s' % (item['period_start'], item['period_end'])
    else:
        item['period_display'] = item.get('period_label') or 'période non précisée'
    return item


def publish_scraped_entry(conn, entry, pharmacy_id, decided_by=None):
    """Insère les jours de garde d'une entrée validée.

    `decided_by` est explicite pour la validation automatique, qui s'exécute
    dans le fil de la collecte, sans session HTTP.
    """
    if decided_by is None:
        decided_by = session.get('username', 'admin') if has_request_context() else 'système'

    dates = json.loads(entry['period_dates'] or '[]')
    if not dates and entry['period_start']:
        dates = [entry['period_start']]
    note = 'Import INAM %s' % (entry['period_label'] or '')
    inserted = 0
    for guard_date in dates:
        cursor = conn.execute(
            'INSERT OR IGNORE INTO guard_schedules (pharmacy_id, guard_date, notes) VALUES (?, ?, ?)',
            (pharmacy_id, guard_date, note.strip()),
        )
        inserted += cursor.rowcount
    conn.execute(
        "UPDATE scraped_guards SET status='validated', matched_pharmacy_id=?, decided_at=?, decided_by=? WHERE id=?",
        (pharmacy_id, datetime.now().isoformat(timespec='seconds'), decided_by, entry['id']),
    )
    # Alerte les utilisateurs ayant cette pharmacie en favori
    notify_favorites(conn, pharmacy_id, dates)
    return inserted


@app.context_processor
def inject_globals():
    return {
        'today_label': date.today().strftime('%d/%m/%Y'),
        'default_center': DEFAULT_CENTER,
        'default_zoom': DEFAULT_ZOOM,
        'user_logged_in': is_logged_in(),
        'stats': get_stats(),
        'today_long': format_date_long(date.today()),
        'pending_scraped': count_pending_scraped() if is_admin() else 0,
        'user_is_admin': is_admin(),
        'unread_alerts': unread_notifications_count(),
        'user_display_name': session.get('display_name') or session.get('username', ''),
        'user_email': session.get('email'),
        'user_initials': session.get('initials', '?'),
    }




@app.route('/assistant')
def assistant_chat():
    return render_template('chat.html', stats=get_stats())


@app.route('/api/chat', methods=['POST'])
def api_chat():
    payload = request.get_json(silent=True) or {}
    message = str(payload.get('message', '')).strip()
    if not message:
        return jsonify({'reply': 'Veuillez écrire une demande.', 'pharmacies': []}), 400
    lat = to_float(payload.get('lat'))
    lon = to_float(payload.get('lon'))
    result = chat_search(message, lat, lon)
    log_search(message, len(result.get('pharmacies') or []))
    return jsonify(result)


@app.route('/manifest.webmanifest')
def manifest():
    """Servi à la racine : le manifeste doit couvrir tout le site."""
    return send_from_directory(app.static_folder, 'manifest.webmanifest',
                               mimetype='application/manifest+json')


@app.route('/sw.js')
def service_worker():
    """Le service worker doit être servi depuis la racine pour que sa portée
    couvre l'ensemble de l'application, et non le seul dossier /static."""
    reponse = send_from_directory(app.static_folder, 'sw.js',
                                  mimetype='application/javascript')
    reponse.headers['Service-Worker-Allowed'] = '/'
    reponse.headers['Cache-Control'] = 'no-cache'
    return reponse


@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.static_folder, 'img', 'icons'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')


@app.route('/api/chat/feedback', methods=['POST'])
def api_chat_feedback():
    """Retour de l'utilisateur sur une réponse : 1 = utile, 0 = à côté."""
    payload = request.get_json(silent=True) or {}
    log_id = payload.get('log_id')
    valeur = payload.get('value')
    if not log_id or valeur not in (0, 1):
        return jsonify({'error': 'parametres invalides'}), 400
    conn = get_db_connection()
    conn.execute('UPDATE chat_logs SET feedback = ? WHERE id = ?', (valeur, log_id))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/')
@app.route('/accueil')
def index():
    return render_template(
        'index.html',
        stats=get_stats(),
        guard_today=get_guard_pharmacies(),
        features=fetch_map_features(),
    )


@app.route('/pharmacies')
def pharmacies():
    blocked = require_account()
    if blocked:
        return blocked
    search = request.args.get('search', '').strip()
    guard_only = request.args.get('guard_only') == '1'
    today = date.today().isoformat()

    conn = get_db_connection()
    query = """
        SELECT p.*, CASE WHEN gs.pharmacy_id IS NULL THEN 0 ELSE 1 END AS is_on_guard
        FROM pharmacies p
        LEFT JOIN guard_schedules gs
          ON gs.pharmacy_id = p.id AND gs.guard_date = ?
        WHERE p.status = 'active'
    """
    params = [today]
    if search:
        like = f'%{search}%'
        query += ' AND (p.name LIKE ? OR p.address LIKE ? OR p.quartier LIKE ? OR p.tags LIKE ?)'
        params.extend([like, like, like, like])
    if guard_only:
        query += ' AND gs.pharmacy_id IS NOT NULL'
    query += ' ORDER BY p.name'

    rows = conn.execute(query, params).fetchall()
    periods = duty_periods_for(conn, [row['id'] for row in rows])
    conn.close()

    items = []
    for row in rows:
        item = dict(row)
        period = periods.get(item['id'])
        item['duty_start'] = period['start'] if period else None
        item['duty_end'] = period['end'] if period else None
        items.append(item)

    return render_template('directory.html', pharmacies=items, search=search,
                           guard_only=guard_only, favorites=favorite_ids())


@app.route('/pharmacies/<int:pharmacy_id>')
def pharmacy_detail(pharmacy_id):
    blocked = require_account()
    if blocked:
        return blocked
    conn = get_db_connection()
    pharmacy = conn.execute(
        """
        SELECT p.*, CASE WHEN gs.pharmacy_id IS NULL THEN 0 ELSE 1 END AS is_on_guard
        FROM pharmacies p
        LEFT JOIN guard_schedules gs
          ON gs.pharmacy_id = p.id AND gs.guard_date = ?
        WHERE p.id = ?
        """,
        (date.today().isoformat(), pharmacy_id),
    ).fetchone()
    guard_dates = conn.execute(
        'SELECT guard_date, notes FROM guard_schedules WHERE pharmacy_id = ? ORDER BY guard_date DESC LIMIT 12',
        (pharmacy_id,),
    ).fetchall()
    conn.close()
    if not pharmacy:
        flash('Pharmacie introuvable.', 'warning')
        return redirect(url_for('pharmacies'))
    return render_template('pharmacy_detail.html', pharmacy=dict(pharmacy), guard_dates=guard_dates)


@app.route('/garde')
def garde():
    blocked = require_account()
    if blocked:
        return blocked
    today = date.today().isoformat()
    target_date = request.args.get('date') or today
    search = request.args.get('q', '').strip()
    dates = [(date.today() + timedelta(days=i)).isoformat() for i in range(10)]

    rows = get_guard_pharmacies(target_date)
    if search:
        needle = search.lower()
        rows = [r for r in rows
                if needle in (r.get('name') or '').lower()
                or needle in (r.get('quartier') or '').lower()
                or needle in (r.get('address') or '').lower()]

    # Période complète encadrant la date affichée, pour le bandeau « Garde : ... »
    conn = get_db_connection()
    periods = {}
    for row in rows:
        guard_dates = [d['guard_date'] for d in conn.execute(
            'SELECT guard_date FROM guard_schedules WHERE pharmacy_id = ? ORDER BY guard_date',
            (row['id'],),
        ).fetchall()]
        for period in group_consecutive_dates(guard_dates):
            if period['start'] <= target_date <= period['end']:
                periods[row['id']] = period
                break
    conn.close()

    for row in rows:
        period = periods.get(row['id'])
        row['duty_start'] = period['start'] if period else None
        row['duty_end'] = period['end'] if period else None

    return render_template('garde.html', guard_rows=rows, target_date=target_date,
                           dates=dates, today=today, q=search, favorites=favorite_ids())


@app.route('/carte')
def carte():
    blocked = require_account()
    if blocked:
        return blocked
    return render_template('map.html', features=fetch_map_features())


@app.route('/navigation')
def navigation_page():
    selected_id = request.args.get('focus', type=int)
    selected_pharmacy = get_selected_pharmacy(selected_id)
    return render_template(
        'navigation.html',
        features=fetch_map_features(),
        selected_pharmacy=selected_pharmacy,
    )


@app.route('/nearest')
def nearest_page():
    return redirect(url_for('navigation_page', mode='nearest'))


@app.route('/api/pharmacies')
def api_pharmacies():
    return jsonify(fetch_map_features())


@app.route('/api/pharmacy/<int:pharmacy_id>')
def api_pharmacy(pharmacy_id):
    pharmacy = get_selected_pharmacy(pharmacy_id)
    if not pharmacy:
        return jsonify({'error': 'Pharmacie introuvable.'}), 404
    return jsonify(pharmacy)


@app.route('/api/nearest')
def api_nearest():
    user_lat = to_float(request.args.get('lat'))
    user_lon = to_float(request.args.get('lon'))
    guard_only = request.args.get('guard_only') == '1'

    if user_lat is None or user_lon is None:
        return jsonify({'error': 'Coordonnées invalides.'}), 400

    conn = get_db_connection()
    query = """
        SELECT p.*, CASE WHEN gs.pharmacy_id IS NULL THEN 0 ELSE 1 END AS is_on_guard
        FROM pharmacies p
        LEFT JOIN guard_schedules gs
          ON gs.pharmacy_id = p.id AND gs.guard_date = ?
        WHERE p.status = 'active'
    """
    params = [date.today().isoformat()]
    if guard_only:
        query += ' AND gs.pharmacy_id IS NOT NULL'
    rows = conn.execute(query, params).fetchall()
    conn.close()

    nearest = None
    nearest_distance = None
    for row in rows:
        if row['latitude'] is None or row['longitude'] is None:
            continue
        distance = haversine_distance(user_lat, user_lon, row['latitude'], row['longitude'])
        if nearest is None or distance < nearest_distance:
            nearest = dict(row)
            nearest_distance = distance

    if nearest is None:
        return jsonify({'error': 'Aucune pharmacie disponible.'}), 404

    nearest['distance_km'] = round(nearest_distance, 2)
    nearest['user_position'] = {'lat': user_lat, 'lon': user_lon}
    return jsonify(nearest)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        conn = get_db_connection()
        user = conn.execute(
            'SELECT * FROM users WHERE username = ? OR email = ?',
            (username, username),
        ).fetchone()
        if user and (user['status'] or 'active') != 'active':
            conn.close()
            flash('Ce compte est désactivé. Contactez un administrateur.', 'danger')
            return render_template('login.html', stats=get_stats())
        if user and verify_password(user['password'], password):
            # Mise à niveau silencieuse des anciens mots de passe stockés en clair
            if not str(user['password']).startswith(('pbkdf2:', 'scrypt:', 'argon2')):
                conn.execute('UPDATE users SET password = ? WHERE id = ?',
                             (generate_password_hash(password), user['id']))
                conn.commit()
            conn.close()
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role'] or 'user'
            set_session_identity(user['full_name'], user['username'], user['email'])
            flash('Connexion réussie.', 'success')
            return redirect(url_for('dashboard') if session['role'] == 'admin'
                            else url_for('assistant_chat'))
        conn.close()
        flash('Identifiants incorrects.', 'danger')
    return render_template('login.html', stats=get_stats())


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        phone = request.form.get('phone', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('password_confirm', '')
        full_name = request.form.get('full_name', '').strip()

        form = {'username': username, 'email': email, 'phone': phone, 'full_name': full_name}

        def back(message, category='warning'):
            flash(message, category)
            return render_template('login.html', stats=get_stats(), mode='register', form=form)

        if not username or not email or not phone or not password:
            return back('Pseudo, e-mail, téléphone et mot de passe sont obligatoires.')
        if len(password) < 6:
            return back('Le mot de passe doit contenir au moins 6 caractères.')
        if password != confirm:
            return back('Les deux mots de passe ne correspondent pas.')

        conn = get_db_connection()
        exists = conn.execute(
            'SELECT id FROM users WHERE username = ? OR (email IS NOT NULL AND email = ?)',
            (username, email),
        ).fetchone()
        if exists:
            conn.close()
            return back("Ce pseudo ou cet e-mail est déjà utilisé.", 'danger')

        cursor = conn.execute(
            """INSERT INTO users (username, password, full_name, email, phone, role, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'user', 'active', ?)""",
            (username, generate_password_hash(password), full_name, email, phone,
             datetime.now().isoformat(timespec='seconds')),
        )
        conn.commit()
        conn.close()

        session['user_id'] = cursor.lastrowid
        session['username'] = username
        session['role'] = 'user'
        set_session_identity(full_name, username, email)
        flash('Compte créé. Choisissez vos pharmacies favorites pour recevoir les alertes de garde.', 'success')
        return redirect(url_for('favorites'))

    return render_template('login.html', stats=get_stats(), mode='register', form={})


# ---------------------------------------------------------------------------
# Mot de passe oublié
# ---------------------------------------------------------------------------

def hash_token(token):
    """Le jeton n'est jamais stocké en clair : seule son empreinte l'est."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def send_reset_email(recipient, username, link):
    """Envoie le lien par e-mail. Sans SMTP configuré, l'affiche dans la console."""
    body = (
        "Bonjour %s,\n\n"
        "Vous avez demandé la réinitialisation de votre mot de passe PharmaX.\n"
        "Cliquez sur le lien ci-dessous pour choisir un nouveau mot de passe :\n\n"
        "%s\n\n"
        "Ce lien est valable %d minutes et ne peut servir qu'une seule fois.\n"
        "Si vous n'êtes pas à l'origine de cette demande, ignorez ce message : "
        "votre mot de passe reste inchangé.\n\n"
        "— L'équipe PharmaX"
    ) % (username, link, RESET_TOKEN_TTL_MINUTES)

    if not SMTP_HOST:
        app.logger.warning(
            '\n=== PharmaX : lien de réinitialisation (SMTP non configuré) ===\n'
            'Destinataire : %s\n%s\n'
            '===============================================================',
            recipient, link,
        )
        return False

    message = EmailMessage()
    message['Subject'] = 'PharmaX — réinitialisation de votre mot de passe'
    message['From'] = SMTP_SENDER
    message['To'] = recipient
    message.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
        if SMTP_USE_TLS:
            server.starttls()
        if SMTP_USER:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(message)
    return True


@app.route('/mot-de-passe-oublie', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()
        # Message identique dans tous les cas : ne révèle pas quels comptes existent
        neutral = ("Si un compte correspond à ces informations, un lien de "
                   "réinitialisation vient d'être envoyé. Pensez à vérifier vos indésirables.")

        if not identifier:
            flash('Indiquez votre e-mail ou votre pseudo.', 'warning')
            return render_template('password_forgot.html', ttl_minutes=RESET_TOKEN_TTL_MINUTES)

        conn = get_db_connection()
        user = conn.execute(
            'SELECT id, username, email, status FROM users WHERE username = ? OR email = ?',
            (identifier, identifier),
        ).fetchone()

        if not user or not user['email'] or (user['status'] or 'active') != 'active':
            conn.close()
            flash(neutral, 'info')
            return redirect(url_for('login'))

        # Limitation du nombre de demandes par heure
        since = (datetime.now() - timedelta(hours=1)).isoformat(timespec='seconds')
        recent = conn.execute(
            'SELECT COUNT(*) AS n FROM password_resets WHERE user_id = ? AND created_at >= ?',
            (user['id'], since),
        ).fetchone()['n']
        if recent >= RESET_MAX_PER_HOUR:
            conn.close()
            flash('Trop de demandes pour ce compte. Réessayez dans une heure.', 'warning')
            return redirect(url_for('login'))

        now = datetime.now()
        token = secrets.token_urlsafe(32)
        conn.execute(
            """INSERT INTO password_resets (user_id, token_hash, created_at, expires_at)
               VALUES (?, ?, ?, ?)""",
            (user['id'], hash_token(token), now.isoformat(timespec='seconds'),
             (now + timedelta(minutes=RESET_TOKEN_TTL_MINUTES)).isoformat(timespec='seconds')),
        )
        conn.commit()
        conn.close()

        link = url_for('reset_password', token=token, _external=True)
        try:
            sent = send_reset_email(user['email'], user['username'], link)
        except Exception as exc:
            app.logger.error("Envoi du lien de réinitialisation impossible : %s", exc)
            flash("L'envoi de l'e-mail a échoué. Contactez un administrateur.", 'danger')
            return redirect(url_for('login'))

        if not sent:
            flash('SMTP non configuré : le lien a été écrit dans la console du serveur.', 'info')
        flash(neutral, 'info')
        return redirect(url_for('login'))

    return render_template('password_forgot.html', ttl_minutes=RESET_TOKEN_TTL_MINUTES)


@app.route('/reinitialiser/<token>', methods=['GET', 'POST'])
def reset_password(token):
    conn = get_db_connection()
    now = datetime.now().isoformat(timespec='seconds')
    entry = conn.execute(
        """SELECT r.id, r.user_id, u.username
           FROM password_resets r JOIN users u ON u.id = r.user_id
           WHERE r.token_hash = ? AND r.used_at IS NULL AND r.expires_at > ?""",
        (hash_token(token), now),
    ).fetchone()

    if not entry:
        conn.close()
        flash('Ce lien est invalide ou a expiré. Demandez-en un nouveau.', 'danger')
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('password_confirm', '')

        if len(password) < 6:
            conn.close()
            flash('Le mot de passe doit contenir au moins 6 caractères.', 'warning')
            return render_template('password_reset.html', token=token, username=entry['username'])
        if password != confirm:
            conn.close()
            flash('Les deux mots de passe ne correspondent pas.', 'warning')
            return render_template('password_reset.html', token=token, username=entry['username'])

        conn.execute('UPDATE users SET password = ? WHERE id = ?',
                     (generate_password_hash(password), entry['user_id']))
        # Le jeton est consommé, et les autres demandes en cours annulées
        conn.execute('UPDATE password_resets SET used_at = ? WHERE user_id = ? AND used_at IS NULL',
                     (now, entry['user_id']))
        conn.commit()
        conn.close()
        session.clear()
        flash('Mot de passe mis à jour. Vous pouvez vous connecter.', 'success')
        return redirect(url_for('login'))

    conn.close()
    return render_template('password_reset.html', token=token, username=entry['username'])


@app.route('/logout')
def logout():
    session.clear()
    flash('Déconnexion effectuée.', 'info')
    return redirect(url_for('index'))


@app.route('/admin')
def dashboard():
    if not is_admin():
        return redirect(url_for('login'))
    today = date.today().isoformat()
    conn = get_db_connection()
    counts = {
        'total': conn.execute('SELECT COUNT(*) AS n FROM pharmacies').fetchone()['n'],
        'guard_today': conn.execute(
            'SELECT COUNT(*) AS n FROM guard_schedules WHERE guard_date = ?', (today,)
        ).fetchone()['n'],
        'pending': conn.execute(
            "SELECT COUNT(*) AS n FROM scraped_guards WHERE status = 'pending'"
        ).fetchone()['n'],
        'no_gps': conn.execute(
            'SELECT COUNT(*) AS n FROM pharmacies '
            'WHERE latitude IS NULL OR longitude IS NULL OR (latitude = 0 AND longitude = 0)'
        ).fetchone()['n'],
    }
    guard_today = conn.execute(
        """
        SELECT p.* FROM guard_schedules gs
        JOIN pharmacies p ON p.id = gs.pharmacy_id
        WHERE gs.guard_date = ? ORDER BY p.name
        """,
        (today,),
    ).fetchall()
    upcoming = conn.execute(
        """
        SELECT gs.guard_date, gs.notes, p.name
        FROM guard_schedules gs JOIN pharmacies p ON p.id = gs.pharmacy_id
        WHERE gs.guard_date >= ? ORDER BY gs.guard_date, p.name LIMIT 15
        """,
        (today,),
    ).fetchall()
    conn.close()
    return render_template('dashboard.html', counts=counts,
                           guard_today=guard_today, upcoming=upcoming)


@app.route('/admin/pharmacies/add', methods=['GET', 'POST'])
def add_pharmacy():
    if not is_admin():
        return redirect(url_for('login'))
    if request.method == 'POST':
        fields = [
            request.form.get('name', '').strip(),
            request.form.get('address', '').strip(),
            request.form.get('quartier', '').strip(),
            request.form.get('phone', '').strip(),
            request.form.get('email', '').strip(),
            request.form.get('opening_hours', '').strip(),
            to_float(request.form.get('latitude')),
            to_float(request.form.get('longitude')),
            request.form.get('source_time', '').strip(),
            to_float(request.form.get('accuracy')),
            request.form.get('provider', '').strip(),
            request.form.get('tags', '').strip(),
            request.form.get('status', 'active').strip(),
        ]
        if not fields[0] or fields[6] is None or fields[7] is None:
            flash('Nom, latitude et longitude sont obligatoires.', 'warning')
            return render_template('pharmacy_form.html', pharmacy=None)
        conn = get_db_connection()
        conn.execute(
            """
            INSERT INTO pharmacies (name, address, quartier, phone, email, opening_hours, latitude, longitude, source_time, accuracy, provider, tags, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            fields,
        )
        conn.commit()
        conn.close()
        flash('Pharmacie ajoutée.', 'success')
        return redirect(url_for('admin_pharmacies'))
    return render_template('pharmacy_form.html', pharmacy=None)


@app.route('/admin/pharmacies/<int:pharmacy_id>/edit', methods=['GET', 'POST'])
def edit_pharmacy(pharmacy_id):
    if not is_admin():
        return redirect(url_for('login'))
    conn = get_db_connection()
    pharmacy = conn.execute('SELECT * FROM pharmacies WHERE id = ?', (pharmacy_id,)).fetchone()
    if not pharmacy:
        conn.close()
        flash('Pharmacie introuvable.', 'warning')
        return redirect(url_for('admin_pharmacies'))
    if request.method == 'POST':
        values = [
            request.form.get('name', '').strip(),
            request.form.get('address', '').strip(),
            request.form.get('quartier', '').strip(),
            request.form.get('phone', '').strip(),
            request.form.get('email', '').strip(),
            request.form.get('opening_hours', '').strip(),
            to_float(request.form.get('latitude')),
            to_float(request.form.get('longitude')),
            request.form.get('source_time', '').strip(),
            to_float(request.form.get('accuracy')),
            request.form.get('provider', '').strip(),
            request.form.get('tags', '').strip(),
            request.form.get('status', 'active').strip(),
            pharmacy_id,
        ]
        conn.execute(
            """
            UPDATE pharmacies
            SET name=?, address=?, quartier=?, phone=?, email=?, opening_hours=?, latitude=?, longitude=?, source_time=?, accuracy=?, provider=?, tags=?, status=?
            WHERE id=?
            """,
            values,
        )
        conn.commit()
        conn.close()
        flash('Pharmacie mise à jour.', 'success')
        return redirect(url_for('admin_pharmacies'))
    conn.close()
    return render_template('pharmacy_form.html', pharmacy=dict(pharmacy))


@app.route('/admin/pharmacies/<int:pharmacy_id>/delete', methods=['POST'])
def delete_pharmacy(pharmacy_id):
    if not is_admin():
        return redirect(url_for('login'))
    conn = get_db_connection()
    conn.execute('DELETE FROM pharmacies WHERE id = ?', (pharmacy_id,))
    conn.execute('DELETE FROM guard_schedules WHERE pharmacy_id = ?', (pharmacy_id,))
    conn.commit()
    conn.close()
    flash('Pharmacie supprimée.', 'info')
    return redirect(url_for('admin_pharmacies'))


@app.route('/admin/guards', methods=['GET', 'POST'])
def manage_guards():
    if not is_admin():
        return redirect(url_for('login'))
    conn = get_db_connection()

    if request.method == 'POST':
        pharmacy_id = request.form.get('pharmacy_id', type=int)
        debut_saisi = request.form.get('date_debut', '').strip()
        fin_saisie = request.form.get('date_fin', '').strip() or debut_saisi
        notes = request.form.get('notes', '').strip()

        if not pharmacy_id or not debut_saisi:
            flash('Veuillez renseigner la pharmacie et la date de début.', 'warning')
        else:
            try:
                jour_debut = date.fromisoformat(debut_saisi)
                jour_fin = date.fromisoformat(fin_saisie)
            except ValueError:
                jour_debut = jour_fin = None
                flash('Dates invalides.', 'warning')

            if jour_debut and jour_fin:
                if jour_fin < jour_debut:
                    flash('La date de fin précède la date de début.', 'warning')
                elif (jour_fin - jour_debut).days > 366:
                    flash('La période ne peut pas dépasser un an.', 'warning')
                else:
                    dates = []
                    courant = jour_debut
                    while courant <= jour_fin:
                        dates.append(courant.isoformat())
                        courant += timedelta(days=1)

                    ajoutes = 0
                    for jour in dates:
                        curseur = conn.execute(
                            'INSERT OR IGNORE INTO guard_schedules (pharmacy_id, guard_date, notes) '
                            'VALUES (?, ?, ?)',
                            (pharmacy_id, jour, notes),
                        )
                        ajoutes += curseur.rowcount

                    if ajoutes:
                        notify_favorites(conn, pharmacy_id, dates)
                    conn.commit()

                    if ajoutes == len(dates):
                        flash('Garde enregistrée : %d jour(s).' % ajoutes, 'success')
                    elif ajoutes:
                        flash('Garde enregistrée : %d jour(s) ajouté(s), %d déjà présent(s).'
                              % (ajoutes, len(dates) - ajoutes), 'success')
                    else:
                        flash('Cette période est déjà entièrement enregistrée.', 'warning')

    pharmacies = conn.execute('SELECT id, name, quartier FROM pharmacies ORDER BY name').fetchall()
    conn.close()

    # Une ligne par période de garde, triée par pharmacie puis par dates
    periodes = []
    for groupe in guard_history_groups():
        for periode in groupe['periods']:
            periodes.append({
                'id': groupe['id'],
                'name': groupe['name'],
                'quartier': groupe['quartier'],
                'start': periode['start'],
                'end': periode['end'],
            })
    periodes.sort(key=lambda ligne: (ligne['name'].lower(), ligne['start'], ligne['end']))

    return render_template('guards_admin.html', pharmacies=pharmacies,
                           periodes=periodes, today=date.today().isoformat())


# ---------------------------------------------------------------------------
# Console d'administration
# ---------------------------------------------------------------------------

@app.route('/admin/pharmacies')
def admin_pharmacies():
    if not is_admin():
        return redirect(url_for('login'))
    search = request.args.get('search', '').strip()
    no_gps = request.args.get('no_gps') == '1'

    query = 'SELECT * FROM pharmacies WHERE 1=1'
    params = []
    if search:
        query += ' AND (name LIKE ? OR quartier LIKE ? OR address LIKE ?)'
        like = '%%%s%%' % search
        params += [like, like, like]
    if no_gps:
        query += ' AND (latitude IS NULL OR longitude IS NULL OR (latitude = 0 AND longitude = 0))'
    query += ' ORDER BY name'

    conn = get_db_connection()
    pharmacies = conn.execute(query, params).fetchall()
    counts = {
        'total': conn.execute('SELECT COUNT(*) AS n FROM pharmacies').fetchone()['n'],
        'active': conn.execute("SELECT COUNT(*) AS n FROM pharmacies WHERE status = 'active'").fetchone()['n'],
        'no_gps': conn.execute(
            'SELECT COUNT(*) AS n FROM pharmacies '
            'WHERE latitude IS NULL OR longitude IS NULL OR (latitude = 0 AND longitude = 0)'
        ).fetchone()['n'],
    }
    conn.close()
    return render_template('admin_pharmacies.html', pharmacies=pharmacies,
                           counts=counts, search=search, no_gps=no_gps)


@app.route('/admin/scraped')
def admin_scraped():
    if not is_admin():
        return redirect(url_for('login'))
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT s.*, p.name AS matched_name, p.quartier AS matched_quartier
        FROM scraped_guards s
        LEFT JOIN pharmacies p ON p.id = s.matched_pharmacy_id
        WHERE s.status = 'pending'
        ORDER BY s.match_score DESC, s.raw_name
        """
    ).fetchall()
    history = conn.execute(
        """
        SELECT * FROM scraped_guards
        WHERE status IN ('validated', 'rejected')
        ORDER BY decided_at DESC LIMIT 20
        """
    ).fetchall()
    pharmacies = conn.execute('SELECT id, name, quartier FROM pharmacies ORDER BY name').fetchall()
    runs = conn.execute('SELECT * FROM scrape_runs ORDER BY id DESC LIMIT 5').fetchall()
    conn.close()

    return render_template(
        'admin_scraped.html',
        entries=[decorate_scraped(r) for r in rows],
        history=[decorate_scraped(r) for r in history],
        pharmacies=pharmacies,
        last_run=last_successful_run(),
        runs=runs,
        auto_enabled=AUTO_SCRAPE_ENABLED,
        auto_interval=AUTO_SCRAPE_INTERVAL_HOURS,
        auto_urgent=AUTO_SCRAPE_URGENT_HOURS,
        coverage_gap=guard_coverage_gap(),
        auto_validate=AUTO_VALIDATE_EXACT_MATCH,
    )


@app.route('/admin/scraped/run', methods=['POST'])
def run_scraping():
    if not is_admin():
        return redirect(url_for('login'))
    try:
        added, message = perform_scraping(mode='manuel')
    except Exception as exc:  # réseau indisponible, site modifié, dépendance absente
        flash('La collecte a échoué : %s' % exc, 'danger')
        return redirect(url_for('admin_scraped'))
    flash('Collecte terminée : %s' % message, 'success' if added else 'info')
    return redirect(url_for('admin_scraped'))


@app.route('/admin/scraped/<int:entry_id>/validate', methods=['POST'])
def validate_scraped(entry_id):
    if not is_admin():
        return redirect(url_for('login'))
    conn = get_db_connection()
    entry = conn.execute('SELECT * FROM scraped_guards WHERE id = ?', (entry_id,)).fetchone()
    if not entry:
        conn.close()
        flash('Entrée introuvable.', 'warning')
        return redirect(url_for('admin_scraped'))

    pharmacy_id = request.form.get('manual_%d' % entry_id, type=int) or entry['matched_pharmacy_id']
    if not pharmacy_id:
        conn.close()
        flash('Choisissez d\'abord la pharmacie correspondante.', 'warning')
        return redirect(url_for('admin_scraped'))

    inserted = publish_scraped_entry(conn, entry, pharmacy_id)
    conn.commit()
    conn.close()
    flash('Entrée validée : %d jour(s) de garde publié(s).' % inserted, 'success')
    return redirect(url_for('admin_scraped'))


@app.route('/admin/scraped/<int:entry_id>/reject', methods=['POST'])
def reject_scraped(entry_id):
    if not is_admin():
        return redirect(url_for('login'))
    conn = get_db_connection()
    conn.execute(
        "UPDATE scraped_guards SET status='rejected', decided_at=?, decided_by=? WHERE id=?",
        (datetime.now().isoformat(timespec='seconds'), session.get('username', 'admin'), entry_id),
    )
    conn.commit()
    conn.close()
    flash('Entrée rejetée.', 'info')
    return redirect(url_for('admin_scraped'))


@app.route('/admin/scraped/bulk', methods=['POST'])
def bulk_scraped():
    if not is_admin():
        return redirect(url_for('login'))
    ids = request.form.getlist('entry_ids', type=int)
    action = request.form.get('action')
    if not ids:
        flash('Aucune entrée sélectionnée.', 'warning')
        return redirect(url_for('admin_scraped'))

    conn = get_db_connection()
    if action == 'reject':
        conn.executemany(
            "UPDATE scraped_guards SET status='rejected', decided_at=?, decided_by=? WHERE id=?",
            [(datetime.now().isoformat(timespec='seconds'), session.get('username', 'admin'), i) for i in ids],
        )
        flash('%d entrée(s) rejetée(s).' % len(ids), 'info')
    else:
        published, skipped = 0, 0
        for entry_id in ids:
            entry = conn.execute('SELECT * FROM scraped_guards WHERE id = ?', (entry_id,)).fetchone()
            if not entry or not entry['matched_pharmacy_id']:
                skipped += 1
                continue
            publish_scraped_entry(conn, entry, entry['matched_pharmacy_id'])
            published += 1
        message = '%d entrée(s) validée(s).' % published
        if skipped:
            message += ' %d ignorée(s) faute de pharmacie associée.' % skipped
        flash(message, 'success' if published else 'warning')
    conn.commit()
    conn.close()
    return redirect(url_for('admin_scraped'))


@app.route('/admin/guards/period/delete', methods=['POST'])
def delete_guard_period():
    if not is_admin():
        return redirect(url_for('login'))
    pharmacy_id = request.form.get('pharmacy_id', type=int)
    start = request.form.get('start', '').strip()
    end = request.form.get('end', '').strip() or start
    if pharmacy_id and start:
        conn = get_db_connection()
        conn.execute(
            'DELETE FROM guard_schedules WHERE pharmacy_id = ? AND guard_date BETWEEN ? AND ?',
            (pharmacy_id, start, end),
        )
        conn.commit()
        conn.close()
        flash('Période de garde supprimée.', 'info')
    return redirect(url_for('manage_guards'))


@app.route('/admin/guards/<int:schedule_id>/delete', methods=['POST'])
def delete_guard(schedule_id):
    if not is_admin():
        return redirect(url_for('login'))
    conn = get_db_connection()
    conn.execute('DELETE FROM guard_schedules WHERE id = ?', (schedule_id,))
    conn.commit()
    conn.close()
    flash('Garde supprimée.', 'info')
    return redirect(url_for('manage_guards'))


def generate_temp_password(length=10):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def count_active_admins(conn, exclude_id=None):
    query = "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND status = 'active'"
    params = []
    if exclude_id:
        query += ' AND id != ?'
        params.append(exclude_id)
    return conn.execute(query, params).fetchone()['n']


@app.route('/admin/users', methods=['GET', 'POST'])
def admin_users():
    if not is_admin():
        return redirect(url_for('login'))
    conn = get_db_connection()

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        phone = request.form.get('phone', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('password_confirm', '')
        full_name = request.form.get('full_name', '').strip()
        role = request.form.get('role', 'user').strip()
        if role not in ('user', 'admin'):
            role = 'user'

        if not username or not email or not phone or not password:
            flash('Pseudo, e-mail, téléphone et mot de passe sont obligatoires.', 'warning')
        elif len(password) < 6:
            flash('Le mot de passe doit contenir au moins 6 caractères.', 'warning')
        elif password != confirm:
            flash('Les deux mots de passe ne correspondent pas.', 'warning')
        elif conn.execute('SELECT id FROM users WHERE username = ? OR email = ?',
                          (username, email)).fetchone():
            flash('Ce pseudo ou cet e-mail est déjà utilisé.', 'danger')
        else:
            conn.execute(
                """INSERT INTO users (username, password, full_name, email, phone, role, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'active', ?)""",
                (username, generate_password_hash(password), full_name, email, phone, role,
                 datetime.now().isoformat(timespec='seconds')),
            )
            conn.commit()
            flash('Compte @%s créé (%s).' % (username, role), 'success')

    users = conn.execute(
        """
        SELECT u.id, u.username, u.full_name, u.email, u.phone, u.role, u.status, u.created_at,
               (SELECT COUNT(*) FROM favorites f WHERE f.user_id = u.id) AS favorite_count
        FROM users u ORDER BY u.role, u.username
        """
    ).fetchall()
    conn.close()
    return render_template('admin_users.html', users=users)


@app.route('/admin/users/<int:user_id>/role', methods=['POST'])
def update_user_role(user_id):
    if not is_admin():
        return redirect(url_for('login'))
    role = request.form.get('role', 'user').strip()
    if role not in ('user', 'admin'):
        role = 'user'

    conn = get_db_connection()
    target = conn.execute('SELECT username, role FROM users WHERE id = ?', (user_id,)).fetchone()
    if not target:
        flash('Compte introuvable.', 'warning')
    elif target['role'] == 'admin' and role != 'admin' and not count_active_admins(conn, exclude_id=user_id):
        flash('Impossible : il doit rester au moins un administrateur actif.', 'danger')
    else:
        conn.execute('UPDATE users SET role = ? WHERE id = ?', (role, user_id))
        conn.commit()
        if target['username'] == session.get('username'):
            session['role'] = role
        flash('Rôle de @%s mis à jour : %s.' % (target['username'], role), 'success')
    conn.close()
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:user_id>/status', methods=['POST'])
def toggle_user_status(user_id):
    if not is_admin():
        return redirect(url_for('login'))
    conn = get_db_connection()
    target = conn.execute('SELECT username, role, status FROM users WHERE id = ?', (user_id,)).fetchone()
    if not target:
        flash('Compte introuvable.', 'warning')
    elif target['username'] == session.get('username'):
        flash('Vous ne pouvez pas désactiver votre propre compte.', 'warning')
    else:
        active = (target['status'] or 'active') == 'active'
        if active and target['role'] == 'admin' and not count_active_admins(conn, exclude_id=user_id):
            flash('Impossible : il doit rester au moins un administrateur actif.', 'danger')
        else:
            conn.execute('UPDATE users SET status = ? WHERE id = ?',
                         ('disabled' if active else 'active', user_id))
            conn.commit()
            flash('Compte @%s %s.' % (target['username'], 'désactivé' if active else 'réactivé'), 'info')
    conn.close()
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:user_id>/reset', methods=['POST'])
def reset_user_password(user_id):
    if not is_admin():
        return redirect(url_for('login'))
    conn = get_db_connection()
    target = conn.execute('SELECT username FROM users WHERE id = ?', (user_id,)).fetchone()
    if not target:
        flash('Compte introuvable.', 'warning')
    else:
        temp = generate_temp_password()
        conn.execute('UPDATE users SET password = ? WHERE id = ?',
                     (generate_password_hash(temp), user_id))
        conn.commit()
        flash('Mot de passe de @%s réinitialisé. Mot de passe provisoire à transmettre : %s'
              % (target['username'], temp), 'success')
    conn.close()
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
def delete_user(user_id):
    if not is_admin():
        return redirect(url_for('login'))
    conn = get_db_connection()
    target = conn.execute('SELECT username, role FROM users WHERE id = ?', (user_id,)).fetchone()
    total = conn.execute('SELECT COUNT(*) AS n FROM users').fetchone()['n']
    if not target:
        flash('Compte introuvable.', 'warning')
    elif target['username'] == session.get('username'):
        flash('Vous ne pouvez pas supprimer votre propre compte.', 'warning')
    elif total <= 1 or (target['role'] == 'admin' and not count_active_admins(conn, exclude_id=user_id)):
        flash('Impossible : il doit rester au moins un administrateur actif.', 'danger')
    else:
        conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
        conn.execute('DELETE FROM favorites WHERE user_id = ?', (user_id,))
        conn.execute('DELETE FROM notifications WHERE user_id = ?', (user_id,))
        conn.execute('DELETE FROM search_history WHERE user_id = ?', (user_id,))
        conn.commit()
        flash('Compte @%s supprimé.' % target['username'], 'info')
    conn.close()
    return redirect(url_for('admin_users'))


@app.route('/admin/analytics')
def admin_analytics():
    if not is_admin():
        return redirect(url_for('login'))
    conn = get_db_connection()
    metrics = {
        'total_guards': conn.execute('SELECT COUNT(*) AS n FROM guard_schedules').fetchone()['n'],
        'covered': conn.execute('SELECT COUNT(DISTINCT pharmacy_id) AS n FROM guard_schedules').fetchone()['n'],
        'chat_logs': conn.execute('SELECT COUNT(*) AS n FROM chat_logs').fetchone()['n'],
        'scraped_total': conn.execute('SELECT COUNT(*) AS n FROM scraped_guards').fetchone()['n'],
    }
    month_rows = conn.execute(
        """
        SELECT substr(guard_date, 1, 7) AS month, COUNT(*) AS count
        FROM guard_schedules GROUP BY month ORDER BY month DESC LIMIT 12
        """
    ).fetchall()
    top_rows = conn.execute(
        """
        SELECT p.name, p.quartier, COUNT(*) AS days
        FROM guard_schedules gs JOIN pharmacies p ON p.id = gs.pharmacy_id
        GROUP BY p.id ORDER BY days DESC, p.name LIMIT 10
        """
    ).fetchall()

    # ----- Mesures réelles du NLU en usage -----
    seuil = intent_classifier.threshold()
    total_messages = metrics['chat_logs']

    par_origine = {r['intent_source'] or 'inconnu': r['n'] for r in conn.execute(
        'SELECT intent_source, COUNT(*) AS n FROM chat_logs GROUP BY intent_source')}
    traites_modele = par_origine.get('modele', 0)
    traites_regles = par_origine.get('regles', 0)
    mesurables = traites_modele + traites_regles

    avis = conn.execute(
        """SELECT
             SUM(CASE WHEN feedback = 1 THEN 1 ELSE 0 END) AS utiles,
             SUM(CASE WHEN feedback = 0 THEN 1 ELSE 0 END) AS ratees,
             COUNT(feedback) AS total
           FROM chat_logs"""
    ).fetchone()
    avis_total = avis['total'] or 0
    avis_utiles = avis['utiles'] or 0
    avis_ratees = avis['ratees'] or 0

    confiance_moyenne = conn.execute(
        "SELECT AVG(confidence) AS m FROM chat_logs WHERE intent_source = 'modele'"
    ).fetchone()['m']

    intentions = conn.execute(
        """SELECT intent, COUNT(*) AS n,
                  AVG(confidence) AS confiance,
                  SUM(CASE WHEN intent_source = 'regles' THEN 1 ELSE 0 END) AS repli,
                  SUM(CASE WHEN feedback = 1 THEN 1 ELSE 0 END) AS utiles,
                  SUM(CASE WHEN feedback = 0 THEN 1 ELSE 0 END) AS ratees
           FROM chat_logs GROUP BY intent ORDER BY n DESC"""
    ).fetchall()

    ratees_recentes = conn.execute(
        """SELECT message, intent, confidence, intent_source, created_at
           FROM chat_logs WHERE feedback = 0
           ORDER BY id DESC LIMIT 12"""
    ).fetchall()

    conn.close()

    nlu = {
        'total_messages': total_messages,
        'traites_modele': traites_modele,
        'traites_regles': traites_regles,
        'part_modele': round(traites_modele * 100 / mesurables) if mesurables else 0,
        'part_regles': round(traites_regles * 100 / mesurables) if mesurables else 0,
        'non_mesures': max(0, total_messages - mesurables),
        'confiance_moyenne': round(confiance_moyenne, 2) if confiance_moyenne else None,
        'seuil': seuil,
        'avis_total': avis_total,
        'avis_utiles': avis_utiles,
        'avis_ratees': avis_ratees,
        'taux_reussite': round(avis_utiles * 100 / avis_total) if avis_total else None,
        'taux_echec': round(avis_ratees * 100 / avis_total) if avis_total else None,
        'couverture_avis': round(avis_total * 100 / total_messages) if total_messages else 0,
    }

    lignes_intentions = []
    for row in intentions:
        n = row['n'] or 0
        avis_n = (row['utiles'] or 0) + (row['ratees'] or 0)
        lignes_intentions.append({
            'intent': row['intent'],
            'n': n,
            'part': round(n * 100 / total_messages) if total_messages else 0,
            'confiance': round(row['confiance'], 2) if row['confiance'] else None,
            'repli': row['repli'] or 0,
            'avis': avis_n,
            'taux': round((row['utiles'] or 0) * 100 / avis_n) if avis_n else None,
        })

    peak = max([r['count'] for r in month_rows], default=0) or 1
    by_month = [
        {'month': r['month'], 'count': r['count'], 'pct': round(r['count'] * 100 / peak)}
        for r in month_rows
    ]
    return render_template('admin_analytics.html', metrics=metrics,
                           by_month=by_month, top_pharmacies=top_rows,
                           nlu=nlu, intentions=lignes_intentions,
                           ratees=ratees_recentes)


# ---------------------------------------------------------------------------
# Espace utilisateur : favoris et alertes de garde
# ---------------------------------------------------------------------------

@app.route('/favoris')
@app.route('/profil')
def favorites():
    if not is_logged_in():
        flash('Connectez-vous pour accéder à votre profil.', 'info')
        return redirect(url_for('login'))

    user_id = current_user_id()
    today = date.today().isoformat()
    conn = get_db_connection()

    profile = conn.execute(
        'SELECT username, full_name, email FROM users WHERE id = ?', (user_id,)
    ).fetchone()

    # Favoris du compte courant uniquement
    rows = conn.execute(
        """
        SELECT p.*, f.created_at AS favorited_at,
               CASE WHEN gs.pharmacy_id IS NULL THEN 0 ELSE 1 END AS is_on_guard
        FROM favorites f
        JOIN pharmacies p ON p.id = f.pharmacy_id
        LEFT JOIN guard_schedules gs ON gs.pharmacy_id = p.id AND gs.guard_date = ?
        WHERE f.user_id = ?
        ORDER BY p.name
        """,
        (today, user_id),
    ).fetchall()

    periods = duty_periods_for(conn, [row['id'] for row in rows])

    favorites_list, alerts = [], []
    for row in rows:
        item = dict(row)
        period = periods.get(item['id'])
        item['duty_start'] = period['start'] if period else None
        item['duty_end'] = period['end'] if period else None
        item['added_on'] = (item.get('favorited_at') or '')[:10]
        favorites_list.append(item)
        if period:
            alerts.append({
                'name': item['name'],
                'start': period['start'],
                'end': period['end'],
                'is_today': period['is_today'],
            })
    alerts.sort(key=lambda a: (not a['is_today'], a['start']))

    history = [
        {'query': row['query'], 'results_count': row['results_count'],
         'day': (row['created_at'] or '')[:10]}
        for row in conn.execute(
            'SELECT query, results_count, created_at FROM search_history '
            'WHERE user_id = ? ORDER BY id DESC LIMIT 50', (user_id,)
        ).fetchall()
    ]

    # La consultation du profil marque les alertes comme lues
    conn.execute(
        'UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL',
        (datetime.now().isoformat(timespec='seconds'), user_id),
    )
    conn.commit()
    conn.close()

    name = (profile['full_name'] or profile['username']) if profile else 'Utilisateur'
    parts = [p for p in name.replace('-', ' ').split() if p]
    initials = ''.join(p[0] for p in parts[:2]).upper() or name[:2].upper()

    return render_template('profile.html', profile=profile, initials=initials,
                           favorites=favorites_list, alerts=alerts, history=history)


@app.route('/profil/historique/vider', methods=['POST'])
def clear_search_history():
    if not is_logged_in():
        return redirect(url_for('login'))
    conn = get_db_connection()
    conn.execute('DELETE FROM search_history WHERE user_id = ?', (current_user_id(),))
    conn.commit()
    conn.close()
    flash('Historique de recherche effacé.', 'info')
    return redirect(url_for('favorites', onglet='recherches'))


@app.route('/favoris/basculer/<int:pharmacy_id>', methods=['POST'])
def toggle_favorite(pharmacy_id):
    if not is_logged_in():
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify({'error': 'auth'}), 401
        flash('Créez un compte pour enregistrer vos pharmacies favorites.', 'info')
        return redirect(url_for('login'))

    user_id = current_user_id()
    conn = get_db_connection()
    existing = conn.execute(
        'SELECT id FROM favorites WHERE user_id = ? AND pharmacy_id = ?',
        (user_id, pharmacy_id),
    ).fetchone()

    if existing:
        conn.execute('DELETE FROM favorites WHERE id = ?', (existing['id'],))
        active = False
        message = 'Pharmacie retirée de vos favoris.'
    else:
        conn.execute(
            'INSERT OR IGNORE INTO favorites (user_id, pharmacy_id) VALUES (?, ?)',
            (user_id, pharmacy_id),
        )
        active = True
        message = 'Pharmacie ajoutée à vos favoris. Vous serez alerté de ses gardes.'
        # Alerte immédiate si des gardes sont déjà planifiées
        dates = [r['guard_date'] for r in conn.execute(
            'SELECT guard_date FROM guard_schedules WHERE pharmacy_id = ? AND guard_date >= ?',
            (pharmacy_id, date.today().isoformat()),
        ).fetchall()]
        notify_favorites(conn, pharmacy_id, dates)

    conn.commit()
    conn.close()

    if request.headers.get('X-Requested-With') == 'fetch':
        return jsonify({'active': active, 'message': message})
    flash(message, 'success')
    return redirect(request.referrer or url_for('pharmacies'))


@app.route('/alertes')
def notifications():
    if not is_logged_in():
        return redirect(url_for('login'))
    return redirect(url_for('favorites', onglet='alertes'))


# ---------------------------------------------------------------------------
# Collecte automatique planifiée
# ---------------------------------------------------------------------------

def perform_scraping(mode='manuel'):
    """Collecte la liste INAM et remplit la file de validation. Renvoie (ajouts, message)."""
    started = datetime.now().isoformat(timespec='seconds')
    try:
        from scraper_inam import fetch_guard_entries
        payload = fetch_guard_entries()
    except Exception as exc:
        conn = get_db_connection()
        conn.execute(
            """INSERT INTO scrape_runs (mode, status, added, message, started_at, finished_at)
               VALUES (?, 'error', 0, ?, ?, ?)""",
            (mode, str(exc), started, datetime.now().isoformat(timespec='seconds')),
        )
        conn.commit()
        conn.close()
        raise

    dates = payload.get('period_dates') or []
    period_start = dates[0] if dates else None
    period_end = dates[-1] if dates else None

    conn = get_db_connection()
    pharmacies_ref = conn.execute('SELECT id, name FROM pharmacies').fetchall()
    added = 0
    auto_validated = 0
    auto_days = 0
    for entry in payload.get('entries', []):
        match, score = match_score_for(entry['name'], pharmacies_ref)
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO scraped_guards
                (raw_name, normalized_name, phone, address, period_label, period_start,
                 period_end, period_dates, matched_pharmacy_id, match_score, status,
                 source_url, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (entry['name'], entry['normalized_name'], entry.get('phone'), entry.get('address'),
             payload.get('period_label'), period_start, period_end, json.dumps(dates),
             match['id'] if match else None, score,
             payload.get('source_url'), payload.get('fetched_at')),
        )
        if not cursor.rowcount:
            continue
        added += 1

        # Correspondance exacte : publication immédiate, sans intervention
        if AUTO_VALIDATE_EXACT_MATCH and match and score >= 100:
            saved = conn.execute(
                'SELECT * FROM scraped_guards WHERE id = ?', (cursor.lastrowid,)
            ).fetchone()
            if saved:
                auto_days += publish_scraped_entry(
                    conn, saved, match['id'],
                    decided_by='collecte %s' % mode,
                )
                auto_validated += 1

    pending = added - auto_validated
    if auto_validated:
        message = ('%d entrée(s) collectée(s) : %d publiée(s) automatiquement '
                   '(%d jour(s) de garde), %d en attente de validation.'
                   % (added, auto_validated, auto_days, pending))
    else:
        message = '%d nouvelle(s) entrée(s) en attente de validation.' % added
    conn.execute(
        """INSERT INTO scrape_runs (mode, status, added, message, started_at, finished_at)
           VALUES (?, 'success', ?, ?, ?, ?)""",
        (mode, added, message, started, datetime.now().isoformat(timespec='seconds')),
    )
    conn.commit()
    conn.close()
    return added, message


def last_successful_run():
    conn = get_db_connection()
    row = conn.execute(
        "SELECT finished_at FROM scrape_runs WHERE status = 'success' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row['finished_at'] if row else None


def guard_coverage_gap():
    """Vrai si aujourd'hui ou demain n'a aucune pharmacie de garde publiée.

    C'est le signal d'un changement de semaine INAM non encore importé : on
    resserre alors la fréquence de collecte au lieu d'attendre le cycle normal.
    """
    today = date.today()
    conn = get_db_connection()
    try:
        for jour in (today, today + timedelta(days=1)):
            row = conn.execute(
                'SELECT COUNT(*) AS n FROM guard_schedules WHERE guard_date = ?',
                (jour.isoformat(),),
            ).fetchone()
            if not row['n']:
                return True
        return False
    finally:
        conn.close()


def _scheduler_loop():
    """Relance la collecte selon l'intervalle courant.

    L'intervalle se resserre automatiquement lorsque les jours à venir ne sont
    pas couverts : c'est le cas au changement de semaine, moment où une
    application de pharmacies de garde ne peut pas se permettre d'attendre.
    """
    while True:
        try:
            manque = guard_coverage_gap()
            intervalle = AUTO_SCRAPE_URGENT_HOURS if manque else AUTO_SCRAPE_INTERVAL_HOURS

            last = last_successful_run()
            due = True
            if last:
                try:
                    elapsed = datetime.now() - datetime.fromisoformat(last)
                    due = elapsed >= timedelta(hours=intervalle)
                except ValueError:
                    due = True
            if due:
                perform_scraping(mode='automatique')
        except Exception:
            pass  # une collecte ratée ne doit jamais arrêter le planificateur
        time.sleep(max(60, AUTO_SCRAPE_CHECK_MINUTES * 60))


def start_scheduler():
    if not AUTO_SCRAPE_ENABLED:
        return
    # En mode debug, Flask lance deux processus : on n'arme le fil que dans le principal
    if app.debug and os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        return
    thread = threading.Thread(target=_scheduler_loop, name='pharmax-scraper', daemon=True)
    thread.start()


start_scheduler()


if __name__ == '__main__':
    app.run(debug=True)
