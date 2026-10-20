#!/usr/bin/env python3
"""
Archive Simulator & Test Harness (dry_run.py)
AI-Powered RAW Photo Archival System v2.0

Creates sample mock data in SQLite and local test directories to demonstrate:
1. Multi-category ingestion (Wyoming Ranch, Iceland, Family, Panoramas, Low Confidence)
2. pHash burst inheritance simulation
3. Mock XMP sidecars
4. Testing router.py physical movement safely
5. Populating app.py Streamlit dashboard
"""

import sqlite3
import json
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent
DB_PATH = PROJECT_ROOT / "data" / "photo_archive.db"
PROJECT_ROOT.joinpath("data").mkdir(parents=True, exist_ok=True)
PROJECT_ROOT.joinpath("logs").mkdir(parents=True, exist_ok=True)

# Sample test dataset representing the photographer's historical lore
SAMPLE_RECORDS = [
    {
        "file_path": "/Volumes/NAS_Photos/Unsorted Archive/2012_WY_Ranch/IMG_0101.NEF",
        "phash": "a1b2c3d4e5f67890",
        "exif_time": "2012:06:14 09:30:15",
        "status": "tagged",
        "primary_category": "Ranch",
        "country": "United States",
        "state_region": "Wyoming",
        "specific_location": "Bighorn Mountains Ranch",
        "key_subjects": ["Livestock", "Horseback", "Working_Shot", "Cattle"],
        "is_aerial": 0,
        "confidence_score": 0.96
    },
    {
        "file_path": "/Volumes/NAS_Photos/Unsorted Archive/2012_WY_Ranch/IMG_0102.NEF",
        "phash": "a1b2c3d4e5f67891",
        "exif_time": "2012:06:14 09:30:18",
        "status": "inherited",
        "primary_category": "Ranch",
        "country": "United States",
        "state_region": "Wyoming",
        "specific_location": "Bighorn Mountains Ranch",
        "key_subjects": ["Livestock", "Horseback", "Working_Shot", "Cattle"],
        "is_aerial": 0,
        "confidence_score": 0.96
    },
    {
        "file_path": "/Volumes/NAS_Photos/Unsorted Archive/2018_Iceland/ICELAND_0042.IIQ",
        "phash": "f5e4d3c2b1a09876",
        "exif_time": "2018:08:22 17:10:02",
        "status": "tagged",
        "primary_category": "Landscape",
        "country": "Iceland",
        "state_region": "Southern Region",
        "specific_location": "Reynisfjara Black Sand Beach",
        "key_subjects": ["Basalt_Columns", "Icelandic_Horse", "Volcanic_Coast"],
        "is_aerial": 0,
        "confidence_score": 0.94
    },
    {
        "file_path": "/Volumes/NAS_Photos/Unsorted Archive/2021_Aerial/FLIGHT_0891.ARW",
        "phash": "1122334455667788",
        "exif_time": "2021:10:05 14:02:11",
        "status": "tagged",
        "primary_category": "Aerial",
        "country": "United States",
        "state_region": "Washington",
        "specific_location": "Palouse Rolling Hills",
        "key_subjects": ["Aerial: Agricultural_Fields", "Contour_Farming"],
        "is_aerial": 1,
        "confidence_score": 0.91
    },
    {
        "file_path": "/Volumes/NAS_Photos/Unsorted Archive/2016_Family/THANKSGIVING_0012.NEF",
        "phash": "9988776655443322",
        "exif_time": "2016:11:24 18:45:00",
        "status": "tagged",
        "primary_category": "Family",
        "country": "United States",
        "state_region": "Wyoming",
        "specific_location": "Ranch Homestead",
        "key_subjects": ["Family_Gathering", "Person: John Smith", "Fireside"],
        "is_aerial": 0,
        "confidence_score": 0.88
    },
    {
        "file_path": "/Volumes/NAS_Photos/Unsorted Archive/2019_Panorama/PANO_001.NEF",
        "phash": "ababcdcdefef0101",
        "exif_time": "2019:07:04 12:00:01",
        "status": "tagged",
        "primary_category": "Landscape",
        "country": "United States",
        "state_region": "Utah",
        "specific_location": "Canyonlands National Park",
        "key_subjects": ["Stitched_Panorama", "Red_Rock", "Mesa"],
        "is_aerial": 0,
        "confidence_score": 0.92
    },
    {
        "file_path": "/Volumes/NAS_Photos/Unsorted Archive/Unknown_Scans/SCAN_9999.CR2",
        "phash": "3344556677889900",
        "exif_time": "2008:04:12 11:22:33",
        "status": "tagged",
        "primary_category": "Landscape",
        "country": "Unknown",
        "state_region": "Unknown",
        "specific_location": "Unknown",
        "key_subjects": ["Macro", "Moss_Texture"],
        "is_aerial": 0,
        "confidence_score": 0.52 # Will trigger 99_Review_Queue
    }
]

def seed_database():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS photo_records (
            file_path TEXT PRIMARY KEY,
            phash TEXT,
            exif_time TEXT,
            status TEXT,
            primary_category TEXT,
            country TEXT,
            state_region TEXT,
            specific_location TEXT,
            key_subjects TEXT,
            is_aerial INTEGER DEFAULT 0,
            confidence_score REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    for r in SAMPLE_RECORDS:
        conn.execute("""
            INSERT OR REPLACE INTO photo_records 
            (file_path, phash, exif_time, status, primary_category, country, state_region, specific_location, key_subjects, is_aerial, confidence_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            r["file_path"],
            r["phash"],
            r["exif_time"],
            r["status"],
            r["primary_category"],
            r["country"],
            r["state_region"],
            r["specific_location"],
            json.dumps(r["key_subjects"]),
            r["is_aerial"],
            r["confidence_score"]
        ))

    conn.commit()
    conn.close()
    print(f"✓ Seeded mock database at: {DB_PATH} with {len(SAMPLE_RECORDS)} realistic test records.")

if __name__ == "__main__":
    seed_database()

