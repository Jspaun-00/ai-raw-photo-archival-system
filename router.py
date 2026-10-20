#!/usr/bin/env python3
"""
Phase 2 Engine: Automated File Router (router.py)
AI-Powered RAW Photo Archival System v2.0

Executes:
1. Reads all tagged/inherited photo records from SQLite database
2. Evaluates the 12-Bucket Taxonomy Matrix & confidence thresholds
3. Performs atomic file relocation for both RAW files and .xmp sidecars
4. Incorporates a 3-loop I/O retry decorator for NAS connection resilience
5. Updates the database records with new target file paths
"""

import os
import sys
import shutil
import sqlite3
import time
import logging
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR
os.environ["PATH"] = f"{PROJECT_ROOT}/bin:{PROJECT_ROOT}/.venv/bin:{os.environ.get('PATH', '')}"
CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"

# Default paths & taxonomy
DESTINATION_DIR = Path("/Volumes/NAS_Photos/Organized Archive")
DB_PATH = PROJECT_ROOT / "data" / "photo_archive.db"
LOG_DIR = PROJECT_ROOT / "logs"

TAXONOMY = {
    "Landscape": "01_Landscapes_&_Travel",
    "Travel": "01_Landscapes_&_Travel",
    "Ranch": "02_Ranch_&_Western",
    "Family": "03_Family_&_Personal",
    "Pets": "04_Pets_&_Animals",
    "Wildlife": "05_Wildlife_&_Nature",
    "Macro": "06_Macro_&_Details",
    "Food": "07_Food_&_Culinary",
    "Documents": "08_Documents_&_Tech",
    "Technology": "08_Documents_&_Tech",
    "Architecture": "09_Architecture_&_Interiors",
    "Interior": "09_Architecture_&_Interiors",
    "Aerial": "10_Aerial_&_Aviation",
    "Panoramas": "11_Panoramas",
    "Review": "99_Review_Queue",
    "Unclassified": "00_Unclassified"
}

CONFIDENCE_THRESHOLD = 0.70

if CONFIG_PATH.exists():
    if yaml:
        with open(CONFIG_PATH, "r") as f:
            loaded = yaml.safe_load(f)
            if loaded:
                if "paths" in loaded:
                    if "destination_dir" in loaded["paths"]:
                        DESTINATION_DIR = Path(loaded["paths"]["destination_dir"])
                    if "db_path" in loaded["paths"]:
                        DB_PATH = Path(loaded["paths"]["db_path"])
                if "taxonomy" in loaded:
                    TAXONOMY.update(loaded["taxonomy"])
                if "processing" in loaded and "confidence_review_threshold" in loaded["processing"]:
                    CONFIDENCE_THRESHOLD = loaded["processing"]["confidence_review_threshold"]
    else:
        with open(CONFIG_PATH, "r") as f:
            for line in f:
                line_str = line.strip()
                if line_str.startswith("destination_dir:"):
                    val = line_str.split(":", 1)[1].strip().strip('"').strip("'")
                    DESTINATION_DIR = Path(val)
                elif line_str.startswith("db_path:"):
                    val = line_str.split(":", 1)[1].strip().strip('"').strip("'")
                    DB_PATH = Path(val)

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=LOG_DIR / "router.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def atomic_transfer(src: Path, dest: Path, copy_only: bool = False) -> bool:
    """
    Transfers file atomically to target destination with retry loop for NAS resilience.
    Uses shutil.copy2 if copy_only is True, else shutil.move.
    """
    if not src.exists():
        logging.warning(f"Source file does not exist, skipping: {src}")
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    
    for attempt in range(1, 4):
        try:
            if copy_only:
                shutil.copy2(str(src), str(dest))
                logging.info(f"Successfully copied {src.name} -> {dest}")
            else:
                shutil.move(str(src), str(dest))
                logging.info(f"Successfully moved {src.name} -> {dest}")
            return True
        except Exception as e:
            logging.warning(f"Transfer attempt {attempt}/3 failed for {src} -> {dest}: {e}")
            time.sleep(2)

    logging.error(f"Failed to transfer {src} after 3 attempts.")
    return False

def sanitize_segment(s, fallback=""):
    if not s:
        return fallback
    import re
    clean = re.split(r'\s*[|/,]\s*', str(s).strip())[0].strip()
    clean = clean.replace("\\", "-").replace(" ", "_")
    if clean.lower() in ("unknown", "general", "none", "null", ""):
        return fallback
    return clean

def extract_year(exif_time: str) -> str:
    if not exif_time:
        return "Undated"
    clean = str(exif_time).strip()
    if len(clean) >= 4 and clean[:4].isdigit():
        return clean[:4]
    return "Undated"

def run_routing(dry_run: bool = False, copy_mode: bool = False, target_destination: Path = None):
    if not DB_PATH.exists():
        print(f"[Error] Database file {DB_PATH} not found. Run pipeline.py first.")
        return

    dest_root = Path(target_destination) if target_destination else DESTINATION_DIR

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    for col, col_type in [("hierarchical_keywords", "TEXT"), ("sub_category", "TEXT")]:
        try:
            conn.execute(f"ALTER TABLE photo_records ADD COLUMN {col} {col_type};")
        except Exception:
            pass
    conn.commit()

    query = """
        SELECT file_path, primary_category, country, state_region, specific_location, 
               confidence_score, key_subjects, exif_time, sub_category, is_aerial
        FROM photo_records
        WHERE status IN ('tagged', 'inherited')
    """
    records = cursor.execute(query).fetchall()

    print(f"Routing {len(records)} records into 12-Bucket Taxonomy (Dry Run: {dry_run})...")

    success_count = 0
    skip_count = 0

    for raw_str, category, country, state_region, location, conf, subjects_json, exif_time, sub_cat, is_aerial in records:
        raw_path = Path(raw_str)
        if not raw_path.exists() and not dry_run:
            skip_count += 1
            continue

        # Strict IIQ Naming check for sidecar
        if raw_path.suffix.lower() == '.iiq':
            xmp_path = raw_path.parent / (raw_path.name + '.xmp')
        else:
            xmp_path = raw_path.with_suffix('.xmp')

        subjects = []
        if subjects_json:
            try:
                import json
                subjects = json.loads(subjects_json)
            except Exception:
                pass

        year = extract_year(exif_time)
        clean_cat = sanitize_segment(category, "Unclassified")
        clean_country = sanitize_segment(country)
        clean_state = sanitize_segment(state_region)
        clean_loc = sanitize_segment(location)
        clean_subcat = sanitize_segment(sub_cat)

        # 12-Bucket Taxonomy Decision Logic
        if conf is not None and conf < CONFIDENCE_THRESHOLD:
            bucket_name = TAXONOMY.get("Review", "99_Review_Queue")
            sub_path_parts = [year if year != "Undated" else "Needs_Review"]
        else:
            has_people = any(s.startswith("Person:") or s in ["Adult_Male", "Adult_Female", "Young_Child_Boy", "Young_Child_Girl"] for s in subjects) or clean_cat == "Family"
            is_pano = any("Stitched_Panorama" in s or "Pano_Sequence_Group" in s for s in subjects) or clean_cat == "Panoramas"
            is_air = (is_aerial == 1) or (clean_cat == "Aerial") or any("Aerial" in s for s in subjects)

            if has_people:
                bucket_name = TAXONOMY.get("Family", "03_Family_&_Personal")
                person_sub = clean_subcat or clean_loc or "Portraits"
                sub_path_parts = [year, person_sub]

            elif is_pano:
                bucket_name = TAXONOMY.get("Panoramas", "11_Panoramas")
                sub_path_parts = [year]
                if clean_loc:
                    sub_path_parts.append(clean_loc)

            elif is_air:
                bucket_name = TAXONOMY.get("Aerial", "10_Aerial_&_Aviation")
                sub_path_parts = [year]
                if clean_loc:
                    sub_path_parts.append(clean_loc)

            elif clean_cat == "Ranch":
                bucket_name = TAXONOMY.get("Ranch", "02_Ranch_&_Western")
                ranch_sub = clean_loc or clean_subcat or "Ranch_Life"
                sub_path_parts = [year, ranch_sub]

            elif clean_cat in ["Landscape", "Landscapes", "Travel"]:
                bucket_name = TAXONOMY.get("Landscape", "01_Landscapes_&_Travel")
                if clean_country:
                    if clean_state:
                        sub_path_parts = [clean_country, clean_state, clean_loc or year]
                    else:
                        sub_path_parts = [clean_country, clean_loc or year]
                else:
                    sub_path_parts = [year, clean_loc or clean_subcat or "Scenic"]

            elif clean_cat == "Pets":
                bucket_name = TAXONOMY.get("Pets", "04_Pets_&_Animals")
                sub_path_parts = [year, clean_subcat or clean_loc or "Pets"]

            elif clean_cat == "Wildlife":
                bucket_name = TAXONOMY.get("Wildlife", "05_Wildlife_&_Nature")
                wild_sub = clean_loc or clean_subcat or "Wildlife"
                sub_path_parts = [year, wild_sub]

            elif clean_cat == "Food":
                bucket_name = TAXONOMY.get("Food", "07_Food_&_Culinary")
                sub_path_parts = [year, clean_subcat or clean_loc or "Culinary"]

            elif clean_cat in ["Documents", "Technology"]:
                bucket_name = TAXONOMY.get(clean_cat, "08_Documents_&_Tech")
                sub_path_parts = [year, clean_subcat or clean_cat]

            elif clean_cat in ["Architecture", "Interior"]:
                bucket_name = TAXONOMY.get(clean_cat, "09_Architecture_&_Interiors")
                if clean_country:
                    sub_path_parts = [clean_country, clean_state or year]
                else:
                    sub_path_parts = [year, clean_loc or clean_subcat or clean_cat]

            elif clean_cat == "Macro":
                bucket_name = TAXONOMY.get("Macro", "06_Macro_&_Details")
                sub_path_parts = [year, clean_subcat or "Details"]

            elif clean_cat and clean_cat in TAXONOMY:
                bucket_name = TAXONOMY[clean_cat]
                sub_path_parts = [year, clean_loc or clean_subcat or clean_cat]

            else:
                bucket_name = TAXONOMY.get("Unclassified", "00_Unclassified")
                sub_path_parts = [year]

        # Construct target directory path with no empty segments and no /General
        clean_parts = [p for p in sub_path_parts if p]
        target_bucket = dest_root / bucket_name
        for part in clean_parts:
            target_bucket = target_bucket / part

        target_raw = target_bucket / raw_path.name
        target_xmp = target_bucket / xmp_path.name

        action_label = "copy" if copy_mode else "move"
        if dry_run:
            print(f"[DRY-RUN] Would {action_label}:\n  RAW: {raw_path} -> {target_raw}\n  XMP: {xmp_path} -> {target_xmp}")
            success_count += 1
            continue

        # Perform atomic transfer (move or copy)
        raw_transferred = atomic_transfer(raw_path, target_raw, copy_only=copy_mode)
        if xmp_path.exists():
            atomic_transfer(xmp_path, target_xmp, copy_only=copy_mode)

        if raw_transferred:
            if not copy_mode:
                cursor.execute(
                    "UPDATE photo_records SET file_path = ? WHERE file_path = ?",
                    (str(target_raw), raw_str)
                )
                conn.commit()
            success_count += 1

    conn.close()
    verb = "copied" if copy_mode else "relocated"
    print(f"Routing completed! Successfully {verb}: {success_count}, Skipped (missing source): {skip_count}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AI RAW Photo Taxonomy Router")
    parser.add_argument("--dry-run", action="store_true", help="Simulate moves without changing files")
    parser.add_argument("--copy", action="store_true", help="Copy files instead of moving them")
    parser.add_argument("--dest", type=str, default=None, help="Custom destination root directory")
    args = parser.parse_args()

    run_routing(dry_run=args.dry_run, copy_mode=args.copy, target_destination=Path(args.dest) if args.dest else None)
