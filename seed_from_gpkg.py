import os
import sqlite3
import struct
from datetime import date, timedelta
from pyproj import Transformer

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
GPKG_PATH = 'data/data.gpkg'
DB_PATH = os.path.join(BASE_DIR, 'data', 'pharmacies.db')
SCHEMA_PATH = os.path.join(BASE_DIR, 'schema.sql')
TRANSFORMER = Transformer.from_crs('EPSG:32631', 'EPSG:4326', always_xy=True)


def parse_point_z(blob):
    byte_order = blob[8]
    endian = '<' if byte_order == 1 else '>'
    x, y, z = struct.unpack(endian + 'ddd', blob[13:37])
    return x, y, z


def infer_quartier(name):
    upper = (name or '').upper()
    if 'AGOE' in upper:
        return 'Agoè'
    if 'LEGBA' in upper:
        return 'Légbassito'
    if 'KEGUE' in upper:
        return 'Kégué'
    if 'ASSIYEYE' in upper:
        return 'Assiyéyé'
    return 'Agoè-Nyivé 1'


def seed_database():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    with open(SCHEMA_PATH, 'r', encoding='utf-8') as f:
        conn.executescript(f.read())

    gpkg = sqlite3.connect(GPKG_PATH)
    rows = gpkg.execute(
        'SELECT geom, time, name, accuracy, provider, tags, address FROM pharmacie ORDER BY name'
    ).fetchall()

    for geom, source_time, name, accuracy, provider, tags, address in rows:
        x, y, _ = parse_point_z(geom)
        lon, lat = TRANSFORMER.transform(x, y)
        conn.execute(
            """
            INSERT INTO pharmacies (name, address, quartier, phone, email, opening_hours, latitude, longitude, source_time, accuracy, provider, tags, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                None if address == 'N / A' else address,
                infer_quartier(name),
                '+228 90 00 00 00',
                '',
                '24h/24',
                lat,
                lon,
                source_time,
                accuracy,
                provider,
                tags or '',
                'active',
            ),
        )

    conn.execute(
        "INSERT INTO users (username, password, full_name) VALUES ('admin', 'admin123', 'Administrateur SIG')"
    )

    pharmacy_ids = [row[0] for row in conn.execute('SELECT id FROM pharmacies ORDER BY name').fetchall()]
    today = date.today()
    for offset in range(0, 14):
        current = (today + timedelta(days=offset)).isoformat()
        start = (offset * 3) % len(pharmacy_ids)
        selection = [
            pharmacy_ids[start],
            pharmacy_ids[(start + 5) % len(pharmacy_ids)],
            pharmacy_ids[(start + 11) % len(pharmacy_ids)],
        ]
        for pharmacy_id in selection:
            conn.execute(
                'INSERT INTO guard_schedules (pharmacy_id, guard_date, notes) VALUES (?, ?, ?)',
                (pharmacy_id, current, 'Rotation initiale - à ajuster selon le tableau officiel.'),
            )

    conn.commit()
    conn.close()
    gpkg.close()
    print('Base initialisée avec succès :', DB_PATH)


if __name__ == '__main__':
    seed_database()
