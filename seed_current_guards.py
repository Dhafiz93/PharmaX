from datetime import date, timedelta
import sqlite3
from pathlib import Path

DB = Path(__file__).parent / 'data' / 'pharmacies.db'
START = date(2026, 7, 13)
END = date(2026, 7, 20)
SOURCE = 'https://www.inam.tg/pharmacies-de-garde/'
GOOGLE_SEARCH = "https://www.google.com/search?q=pharmacie+de+garde+aujourd%27hui+lom%C3%A9"

# Liste géolocalisée publiée pour la semaine du 13 au 20 juillet 2026.
GUARDS = [
('PHARMACIE ADIDOGOME','Adidogomé','Face au camp 2ème RI d’Adidogomé','91 05 78 21',6.1850232,1.1662806),
('PHARMACIE OSSAN','Avédji','Carrefour Avédji, face Ets LA LIMOUSINE','70 40 44 55',6.2011493,1.1807703),
('PHARMACIE APOLLON','Avédji','Face complexe scolaire Makafui, près du carrefour des Hirondelles','93 50 42 55',6.2028463,1.1666778),
('PHARMACIE DIEUDONNE','Agoè-Téléssou','Route de Léo 2000, non loin de FUCEC Agoè-Téléssou','70 44 84 59',6.2157252,1.1724430),
('PHARMACIE VIGUEUR','Agbalépédogan','Rue 267, Agbalépédogan, Kilimandjaro','70 44 81 96',6.1925431,1.2008180),
('PHARMACIE DU MILLENAIRE','Agoè-Nyivé','Face réserve de la gendarmerie d’Agoè-Nyivé','70 21 31 97',6.2068416,1.1895233),
('PHARMACIE DJIDJOLE','Djidjolé','Avenue Pya, face à l’hôtel Mboloani','93 93 99 27',6.1804700,1.1952551),
('PHARMACIE EL NOUR','Agoè-Alinka','Non loin du CMS Togblékopé','97 79 93 74',6.2763269,1.2105083),
('PHARMACIE SILOE','Apédokoè Atigangomé','Carrefour Aflao Apédokoè Atigangomé','90 80 26 39',6.2068637,1.1409208),
('PHARMACIE ACTUELLE','Sagbado-Adidogomé','Route de Ségbé','90 61 46 44',6.1925272,1.1440001),
('PHARMACIE SEGBE','Ségbé','Près de l’EPP et du CEG Ségbé','92 59 49 35',6.1939393,1.1141034),
('PHARMACIE CLEMENCE','Agoè-Kakaveli','Route de la Cour d’Appel, entre CEET Agoè et l’école La Source','70 19 35 35',6.2198601,1.1982851),
('PHARMACIE ST MICHEL','Agoè-Nyivé','Entre la Brasserie BB et l’Espace Télécom','70 43 30 43',6.2152163,1.2083838),
('PHARMACIE ASSURANCE','Adétikopé','Nationale N°1, non loin du marché','93 08 76 76',6.3209304,1.2175500),
("PHARMACIE CHATEAU-D'EAU",'Bè Château','Près du château d’eau de Bè','96 80 08 88',6.1370894,1.2489577),
('PHARMACIE ST JOSEPH','Bè Klikamé','Bretelle Bè Klikamé','96 80 09 65',6.1772920,1.2051030),
('PHARMACIE ST PAUL',"N'Kafu",'Boulevard Jean-Paul II, à côté du Lycée St Joseph','22 61 85 08',6.1521253,1.2324983),
('PHARMACIE LE JOURDAIN','Tokoin Wuiti','Boulevard Léopold Sédar Senghor, face au CEG Tokoin Wuiti','92 38 30 50',6.1420394,1.2108748),
('PHARMACIE KOUESSAN','Kégué','En face du stade de Kégué','96 80 10 01',6.2039914,1.2423006),
('PHARMACIE J-MIMSHAK','Hountigomé','Rue Tchamba 964, à 50 m de la base SATOM','92 24 42 70',6.1688979,1.2401948),
('PHARMACIE KOBOYO','Davié','Nationale N°1 avant le péage de Davié, face station Sanol','93 70 48 12',6.3514611,1.2117051),
('PHARMACIE STE RITA','Doulassamé','Rue pavée, face Hôtel SANA','96 80 09 70',6.1382886,1.2281209),
('PHARMACIE ST KISITO','Tokoin Doumasséssé','Boulevard de la Kara, près du Bar TAMTAM','96 80 09 64',6.1642690,1.2203399),
('PHARMACIE OLIVIERS','Agoè-Fiovi','Rue du Rotary Club, angle boulevard Félix Houphouët-Boigny','96 80 09 50',6.1645558,1.2674263),
('PHARMACIE LE PROGRES','Ahadzi-Kpota','Grand contournement, face EPPL Universelle','70 45 86 55',6.1666854,1.2832432),
('PHARMACIE LA MAIN DE DIEU','Agoè-Assiyéyé','Près du Temple Galilée','93 40 21 21',6.2349736,1.2003722),
('PHARMACIE ABRAHAM','Agoè-Logopé Kossigan','Agoè Logopé Kossigan','92 25 99 83',6.2347668,1.1753467),
('PHARMACIE GANFAT','Agoè-Daliko','Près du carrefour EDEM, Camp GP','70 22 15 15',6.2372998,1.1627322),
('PHARMACIE ZOPOMAHE','Zopomahé','Route Zossimé-Sanguéra, près de la salle des Témoins de Jéhovah','96 28 34 10',6.2331326,1.1360166),
('PHARMACIE EL-SALI','Aflao Apédokoè Gbomamé','Route Lomé-Kpalimé, à 50 m de la quincaillerie MACO','97 53 44 44',6.2201679,1.1253384),
('PHARMACIE BEL AIR','Assiganto','Près de Ramco et de l’hôtel Palm Beach','22 21 03 21',6.1237929,1.2222418),
('PHARMACIE MAWUNYO','Agoè-Sogbossito','Route de Mission Tové, face station OANDO','70 42 34 64',6.2423512,1.1751599),
('PHARMACIE VERSEAU','Baguida','Près de la Maison Bateau, Baguida','90 01 90 29',6.1668073,1.3344968),
('PHARMACIE LE ROCHER','Agoè-Zongo','Nationale N°1, près du terrain de golf','92 30 06 56',6.2461663,1.2093809),
]

con = sqlite3.connect(DB)
con.execute('''CREATE TABLE IF NOT EXISTS guard_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    google_search_url TEXT,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
)''')
con.execute('DELETE FROM guard_schedules WHERE guard_date BETWEEN ? AND ?', (START.isoformat(), END.isoformat()))
for name, quartier, address, phone, lat, lon in GUARDS:
    row = con.execute('SELECT id FROM pharmacies WHERE lower(replace(name,"PHARM ","PHARMACIE ")) = lower(?)', (name,)).fetchone()
    if row:
        pid = row[0]
        con.execute('UPDATE pharmacies SET name=?, quartier=?, address=?, phone=?, latitude=?, longitude=?, provider=?, tags=?, status="active" WHERE id=?',
                    (name, quartier, address, phone, lat, lon, 'INAM / Google Maps', 'pharmacie de garde', pid))
    else:
        cur = con.execute('''INSERT INTO pharmacies(name,address,quartier,phone,opening_hours,latitude,longitude,provider,tags,status)
                             VALUES(?,?,?,?,?,?,?,?,?,?)''',
                          (name,address,quartier,phone,'Service de garde selon calendrier officiel',lat,lon,'INAM / Google Maps','pharmacie de garde','active'))
        pid = cur.lastrowid
    d = START
    while d <= END:
        con.execute('INSERT OR IGNORE INTO guard_schedules(pharmacy_id,guard_date,notes) VALUES(?,?,?)',
                    (pid,d.isoformat(),'Liste officielle INAM du 13 au 20 juillet 2026'))
        d += timedelta(days=1)
con.execute('DELETE FROM guard_sources')
con.execute('INSERT INTO guard_sources(source_name,source_url,google_search_url,period_start,period_end) VALUES(?,?,?,?,?)',
            ('INAM Togo',SOURCE,GOOGLE_SEARCH,START.isoformat(),END.isoformat()))
con.commit()
print('Guards:', len(GUARDS), 'rows:', con.execute('select count(*) from guard_schedules where guard_date=?',(START.isoformat(),)).fetchone()[0])
con.close()
