DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS pharmacies;
DROP TABLE IF EXISTS guard_schedules;

CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL,
    full_name TEXT
);

CREATE TABLE pharmacies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    address TEXT,
    quartier TEXT,
    phone TEXT,
    email TEXT,
    opening_hours TEXT,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    source_time TEXT,
    accuracy REAL,
    provider TEXT,
    tags TEXT,
    status TEXT DEFAULT 'active'
);

CREATE TABLE guard_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pharmacy_id INTEGER NOT NULL,
    guard_date TEXT NOT NULL,
    notes TEXT,
    UNIQUE(pharmacy_id, guard_date),
    FOREIGN KEY (pharmacy_id) REFERENCES pharmacies(id)
);
