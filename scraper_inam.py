import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

INAM_GUARD_URL = 'https://www.inam.tg/pharmacies-de-garde/'

REQUEST_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.8',
}

MONTHS = {
    'janvier': 1, 'fevrier': 2, 'mars': 3, 'avril': 4, 'mai': 5, 'juin': 6,
    'juillet': 7, 'aout': 8, 'septembre': 9, 'octobre': 10, 'novembre': 11,
    'decembre': 12,
}


def strip_accents(value: str) -> str:
    value = unicodedata.normalize('NFKD', value or '')
    return ''.join(ch for ch in value if not unicodedata.combining(ch))


def normalize_text(value: str) -> str:
    """Forme normalisée servant au rapprochement avec le référentiel local."""
    value = strip_accents(value).upper()
    value = value.replace('PHARMACIE', '').replace('PHARM', '')
    value = re.sub(r'[^A-Z0-9]+', ' ', value)
    return re.sub(r'\s+', ' ', value).strip()


def flatten(value: str) -> str:
    """Aplatit sauts de ligne, espaces insécables et caractères Unicode parasites."""
    if not value:
        return ''
    value = value.replace('\xa0', ' ').replace('\u202f', ' ')
    value = re.sub(r'[\u200b-\u200d\ufeff]', '', value)
    return re.sub(r'\s+', ' ', value).strip()


# --- Période -----------------------------------------------------------------

PERIOD_PATTERNS = [
    # 1. Format textuel : "du 31 aout au 7 septembre 2026", "du 31/08 au 07/09/2026"
    re.compile(
        r'(?:du\s*)?(\d{1,2})\s*(?:er)?\s*([a-z0-9\/\.]+)?\s*(?:au|-|a)\s*(\d{1,2})\s*(?:er)?\s*([a-z]+)\s*(\d{4})',
        re.I
    ),
    # 2. Format numérique simple : "03/09/2026 - 10/09/2026"
    re.compile(
        r'(\d{1,2})[\/\.-](\d{1,2})\s*(?:au|-|a)?\s*(\d{1,2})[\/\.-](\d{1,2})[\/\.-](\d{4})',
        re.I
    )
]


def _build_range_response(start_dt: date, end_dt: date) -> Tuple[Optional[str], List[str]]:
    if end_dt < start_dt or (end_dt - start_dt).days > 60:
        return None, []

    dates = []
    current = start_dt
    while current <= end_dt:
        dates.append(current.isoformat())
        current += timedelta(days=1)

    label = f"{start_dt.day:02d}/{start_dt.month:02d}/{start_dt.year} au {end_dt.day:02d}/{end_dt.month:02d}/{end_dt.year}"
    return label, dates


def extract_date_range(text: str) -> Tuple[Optional[str], List[str]]:
    """Renvoie (libellé lisible, liste des dates ISO couvertes)."""
    haystack = strip_accents(flatten(text)).lower()

    for pattern in PERIOD_PATTERNS:
        match = pattern.search(haystack)
        if match:
            groups = match.groups()
            try:
                if len(groups) == 5 and groups[1] and not groups[1].isdigit():
                    start_day = int(groups[0])
                    start_month_name = groups[1].strip()
                    end_day = int(groups[2])
                    end_month_name = groups[3].strip()
                    year = int(groups[4])

                    end_month = MONTHS.get(end_month_name)
                    if end_month:
                        start_month = MONTHS.get(start_month_name, end_month)
                        start_year = year - 1 if start_month > end_month else year
                        start_dt = date(start_year, start_month, start_day)
                        end_dt = date(year, end_month, end_day)
                        return _build_range_response(start_dt, end_dt)
                elif len(groups) == 5:
                    start_dt = date(int(groups[4]), int(groups[1]), int(groups[0]))
                    end_dt = date(int(groups[4]), int(groups[3]), int(groups[2]))
                    return _build_range_response(start_dt, end_dt)
            except ValueError:
                continue

    return None, []


# --- Entrées -----------------------------------------------------------------

def clean_phone(value: str) -> str:
    """Nettoie « ☎22 21 65 49 » ou « ☎7() 44 84 59 »."""
    value = flatten(value).replace('☎', ' ')
    digits = re.sub(r'[^0-9]', '', value)
    if not digits:
        return ''
    return ' '.join(digits[i:i + 2] for i in range(0, len(digits), 2))


def is_pharmacy_name(value: str) -> bool:
    upper = strip_accents(value).upper()
    if 'PHARMACIE' not in upper and not upper.startswith('PHARM'):
        return False
    return 'TELEPHONE' not in upper and 'EMPLACEMENT' not in upper


def parse_tables(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """Lit les tableaux à trois colonnes de la page."""
    entries = []
    for row in soup.find_all('tr'):
        cells = [flatten(cell.get_text(' ')) for cell in row.find_all(['td', 'th'])]
        cells = [c for c in cells if c]
        if not cells:
            continue
        header = strip_accents(' '.join(cells)).upper()
        if 'TELEPHONE' in header and 'EMPLACEMENT' in header:
            continue
        if not is_pharmacy_name(cells[0]) or not normalize_text(cells[0]):
            continue
        entries.append({
            'name': cells[0],
            'phone': clean_phone(cells[1]) if len(cells) > 1 else '',
            'address': cells[2] if len(cells) > 2 else '',
        })
    return entries


LINE_PATTERN = re.compile(r'^(PHARMACIE\s+.+?)☎\s*([0-9()\s]+)\s+(.+)$', re.I)


def parse_guard_line(line: str) -> Optional[Dict[str, str]]:
    """Analyse d'une ligne « PHARMACIE X ☎ 12 34 56 78 Adresse » (repli)."""
    line = flatten(line)
    if not is_pharmacy_name(line):
        return None
    match = LINE_PATTERN.match(line)
    if not match:
        return None
    entry = {
        'name': match.group(1).strip(),
        'phone': clean_phone(match.group(2)),
        'address': match.group(3).strip(),
    }
    entry['normalized_name'] = normalize_text(entry['name'])
    return entry


def parse_lines(soup: BeautifulSoup) -> List[Dict[str, str]]:
    text = soup.get_text('\n', strip=True)
    entries = []
    for line in text.splitlines():
        entry = parse_guard_line(line)
        if entry:
            entries.append(entry)
    return entries


def dedupe(entries: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen = set()
    result = []
    for entry in entries:
        key = entry.get('normalized_name') or normalize_text(entry['name'])
        if not key or key in seen:
            continue
        seen.add(key)
        entry['normalized_name'] = key
        result.append(entry)
    return result


def parse_guard_page(html: str) -> Dict[str, object]:
    """Analyse le HTML de la page. Isolé pour pouvoir être testé hors ligne."""
    soup = BeautifulSoup(html, 'html.parser')

    # Utilisation d'un séparateur d'espace explicite entre les éléments HTML
    text_content = soup.get_text(separator=' ', strip=True)

    period_label, period_dates = extract_date_range(text_content)

    entries = parse_tables(soup)
    if not entries:
        entries = parse_lines(soup)

    return {
        'source_url': INAM_GUARD_URL,
        'period_label': period_label,
        'period_dates': period_dates,
        'entries': dedupe(entries),
        'fetched_at': datetime.utcnow().isoformat(timespec='seconds') + 'Z',
    }


def fetch_guard_entries(timeout: int = 30) -> Dict[str, object]:
    response = requests.get(INAM_GUARD_URL, timeout=timeout, headers=REQUEST_HEADERS)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    payload = parse_guard_page(response.text)

    if not payload['entries']:
        raise ValueError(
            "Aucune pharmacie n'a pu être lue sur la page de l'INAM. "
            'La mise en page du site a probablement changé.'
        )

    # REPLI DE SECOURS (FALLBACK) :
    # Si la période de garde n'a pas pu être lue par la regex, 
    # on génère automatiquement la semaine en cours (Lundi -> Dimanche)
    if not payload['period_dates']:
        today = date.today()
        start_dt = today - timedelta(days=today.weekday())
        end_dt = start_dt + timedelta(days=6)

        payload['period_dates'] = [(start_dt + timedelta(days=i)).isoformat() for i in range(7)]
        payload['period_label'] = f"{start_dt.strftime('%d/%m/%Y')} au {end_dt.strftime('%d/%m/%Y')} (Période estimée)"

    return payload


def match_guard_entry(entry: Dict[str, str], pharmacies: List[Dict[str, object]]) -> Optional[Dict[str, object]]:
    """Conservé pour compatibilité : renvoie la meilleure pharmacie correspondante."""
    target = entry.get('normalized_name') or normalize_text(entry.get('name', ''))
    if not target:
        return None
    target_tokens = set(target.split())
    best, best_score = None, 0
    for pharmacy in pharmacies:
        candidate = normalize_text(str(pharmacy.get('name', '')))
        if not candidate:
            continue
        if target == candidate or target in candidate or candidate in target:
            score = 100
        else:
            score = len(target_tokens & set(candidate.split())) * 10
        if score > best_score:
            best, best_score = pharmacy, score
    return best if best_score >= 10 else None