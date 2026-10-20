#!/usr/bin/env python3
"""
Phase 1 Engine: Ingestion & Tagging (pipeline.py)
AI-Powered RAW Photo Archival System v2.0

Executes:
1. Preview extraction from RAW headers via ExifTool
2. Perceptual hashing (imagehash) for burst bypass
3. VLM inference (Qwen2.5-VL 7B via Ollama) with photographer lore directive
4. Biometric facial matching (InsightFace / OpenCV)
5. Panorama sequence timeline grouping
6. Dublin Core IPTC XMP sidecar generation with Capture One .IIQ.xmp compatibility
7. SQLite checkpointing in WAL mode for crash-proof recovery
"""

import os
import sys
import json
import re
import sqlite3
import hashlib
import logging
import threading
import queue
import time
from pathlib import Path
from datetime import datetime

try:
    import yaml
except ImportError:
    yaml = None

import exiftool
import ollama
from PIL import Image, ImageStat
import imagehash
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn

# --- BIOMETRIC INITIALIZATION (OPTIONAL) ---
try:
    import cv2
    import insightface
    import numpy as np
    BIOMETRICS_ENABLED = True
except ImportError:
    BIOMETRICS_ENABLED = False

# --- BASE PATHS & DIRECTORY STRUCTURE ---
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR
os.environ["PATH"] = f"{PROJECT_ROOT}/bin:{PROJECT_ROOT}/.venv/bin:{os.environ.get('PATH', '')}"

CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"
DIRECTIVE_PATH = PROJECT_ROOT / "config" / "directive.txt"

# Default fallback settings
settings = {
    "paths": {
        "source_dir": "/Volumes/NAS_Photos/Unsorted Archive",
        "destination_dir": "/Volumes/NAS_Photos/Organized Archive",
        "cache_dir": str(PROJECT_ROOT / "cache"),
        "data_dir": str(PROJECT_ROOT / "data"),
        "logs_dir": str(PROJECT_ROOT / "logs"),
        "ref_faces_dir": str(PROJECT_ROOT / "reference_faces"),
        "db_path": str(PROJECT_ROOT / "data" / "photo_archive.db"),
    },
    "processing": {
        "vlm_model": "qwen2.5vl:7b",
        "supported_extensions": [".nef", ".iiq", ".arw", ".cr2", ".cr3", ".dng", ".jpg", ".jpeg", ".png"],
        "queue_maxsize": 200,
        "vram_flush_interval": 1000,
        "phash_similarity_threshold": 4,
        "burst_timeline_window_seconds": 6.0,
        "confidence_review_threshold": 0.70,
        "aspect_ratio_pano_threshold": 2.1,
    }
}

if CONFIG_PATH.exists() and yaml:
    with open(CONFIG_PATH, "r") as f:
        loaded = yaml.safe_load(f)
        if loaded:
            if "paths" in loaded: settings["paths"].update(loaded["paths"])
            if "processing" in loaded: settings["processing"].update(loaded["processing"])

def resolve_ollama_model(preferred_model: str) -> str:
    """Dynamically resolves model name matching Ollama local catalog (handling hyphen variances)."""
    try:
        models_resp = ollama.list()
        models = models_resp.get("models", []) if isinstance(models_resp, dict) else getattr(models_resp, "models", [])
        avail = []
        for m in models:
            name = m.get("model", "") or m.get("name", "") if isinstance(m, dict) else getattr(m, "model", "")
            if name: avail.append(name)
        if preferred_model in avail:
            return preferred_model
        pref_norm = preferred_model.replace("-", "").replace("_", "")
        for m in avail:
            if m.replace("-", "").replace("_", "") == pref_norm or "qwen2.5" in m.lower():
                return m
    except Exception:
        pass
    return preferred_model

def prewarm_vlm(model_name: str):
    """Pre-loads model into unified memory asynchronously to eliminate first-photo cold start."""
    try:
        resolved = resolve_ollama_model(model_name)
        logging.info(f"Pre-warming VLM ({resolved}) into GPU memory...")
        ollama.generate(model=resolved, prompt="warmup", keep_alive="1h")
        logging.info(f"VLM ({resolved}) pre-warmed successfully.")
    except Exception as e:
        err_logger.warning(f"VLM pre-warm warning: {e}")

SOURCE_DIR = Path(settings["paths"]["source_dir"])
CACHE_DIR = Path(settings["paths"]["cache_dir"])
DATA_DIR = Path(settings["paths"]["data_dir"])
LOG_DIR = Path(settings["paths"]["logs_dir"])
REF_DIR = Path(settings["paths"]["ref_faces_dir"])
DB_PATH = Path(settings["paths"]["db_path"])

# Ensure runtime directories exist
CACHE_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
REF_DIR.mkdir(parents=True, exist_ok=True)

DIRECTIVE = DIRECTIVE_PATH.read_text(encoding="utf-8") if DIRECTIVE_PATH.exists() else "Analyze photograph and return valid JSON metadata."

# --- QUEUES ---
QUEUE_MAX = settings["processing"]["queue_maxsize"]
extraction_queue = queue.Queue(maxsize=QUEUE_MAX)
write_queue = queue.Queue(maxsize=QUEUE_MAX)

# --- LOGGING SETUP ---
logging.basicConfig(
    filename=LOG_DIR / "pipeline.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
err_logger = logging.getLogger("errors")
err_handler = logging.FileHandler(LOG_DIR / "errors.log")
err_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
err_logger.addHandler(err_handler)

# --- DATABASE SETUP (WAL MODE & INDEXED) ---
def init_db():
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
            hierarchical_keywords TEXT,
            sub_category TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    for col, col_type in [("hierarchical_keywords", "TEXT"), ("sub_category", "TEXT")]:
        try:
            conn.execute(f"ALTER TABLE photo_records ADD COLUMN {col} {col_type};")
        except Exception:
            pass
    # Secondary performance indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_photo_status ON photo_records(status);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_photo_conf ON photo_records(confidence_score);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_photo_created ON photo_records(created_at DESC);")
    conn.commit()
    conn.close()

# --- BIOMETRICS INITIALIZATION ---
reference_embeddings = {}
face_app = None

def init_biometrics():
    global face_app, reference_embeddings
    if not BIOMETRICS_ENABLED:
        logging.info("Biometrics disabled (insightface/opencv not installed).")
        return

    ref_images = list(REF_DIR.glob("*.jpg")) + list(REF_DIR.glob("*.png"))
    if not ref_images:
        logging.info("No reference face images found in reference_faces/ directory.")
        return

    try:
        face_app = insightface.app.FaceAnalysis(name='buffalo_l')
        face_app.prepare(ctx_id=0, det_size=(640, 640))
        for ref_img in ref_images:
            img = cv2.imread(str(ref_img))
            if img is not None:
                faces = face_app.get(img)
                if faces:
                    person_name = ref_img.stem.replace("_", " ").title()
                    reference_embeddings[person_name] = faces[0].embedding
                    logging.info(f"Loaded reference face embedding for: {person_name}")
    except Exception as e:
        err_logger.error(f"Failed to initialize FaceAnalysis: {e}")

def detect_faces(preview_path: Path) -> list:
    if not BIOMETRICS_ENABLED or not reference_embeddings or face_app is None:
        return []
    try:
        img = cv2.imread(str(preview_path))
        if img is None:
            return []
        faces = face_app.get(img)
        matches = []
        for face in faces:
            for name, emb in reference_embeddings.items():
                sim = np.dot(face.embedding, emb) / (np.linalg.norm(face.embedding) * np.linalg.norm(emb))
                if sim > 0.50:
                    matches.append(f"Person: {name}")
        return matches
    except Exception as e:
        err_logger.error(f"Error during face detection on {preview_path}: {e}")
        return []

# --- WORKER 1: DISK EXTRACTION (PRODUCER) ---
def worker_extractor(pending_files):
    logging.info(f"Worker Extractor started with {len(pending_files)} files.")
    try:
        with exiftool.ExifToolHelper() as et:
            for raw_path in pending_files:
                hashed_name = hashlib.sha256(str(raw_path.resolve()).encode()).hexdigest() + ".jpg"
                preview_out = CACHE_DIR / hashed_name

                if not preview_out.exists():
                    ext_low = raw_path.suffix.lower()
                    if ext_low in [".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"]:
                        try:
                            from PIL import Image
                            with Image.open(raw_path) as img:
                                if max(img.size) > 896:
                                    img.thumbnail((896, 896), Image.Resampling.LANCZOS)
                                if img.mode != 'RGB':
                                    img = img.convert('RGB')
                                img.save(preview_out, 'JPEG', quality=85)
                        except Exception as e:
                            err_logger.error(f"Direct PIL preview error for {raw_path}: {e}")
                    elif ext_low in [".heic", ".heif"]:
                        import subprocess
                        try:
                            subprocess.run(
                                ["/usr/bin/sips", "-s", "format", "jpeg", "-Z", "896", str(raw_path), "--out", str(preview_out)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
                            )
                        except Exception as e:
                            err_logger.error(f"HEIC sips conversion error for {raw_path}: {e}")
                    else:
                        # Fallback preview extraction tags for RAW in order of quality
                        for tag in ["-PreviewImage", "-JpgFromRaw", "-ThumbnailImage"]:
                            try:
                                et.execute(tag, "-b", str(raw_path), "-w!", str(preview_out))
                                if preview_out.exists() and preview_out.stat().st_size > 10000:
                                    break
                            except Exception:
                                continue
                        # Native macOS sips fallback if RAW embedded preview wasn't extracted
                        if not preview_out.exists() or preview_out.stat().st_size == 0:
                            import subprocess
                            try:
                                subprocess.run(
                                    ["/usr/bin/sips", "-s", "format", "jpeg", "-Z", "896", str(raw_path), "--out", str(preview_out)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
                                )
                            except Exception:
                                pass

                if preview_out.exists() and preview_out.stat().st_size > 0:
                    try:
                        from PIL import Image
                        with Image.open(preview_out) as img:
                            if max(img.size) > 896:
                                img.thumbnail((896, 896), Image.Resampling.LANCZOS)
                                if img.mode != 'RGB':
                                    img = img.convert('RGB')
                                img.save(preview_out, 'JPEG', quality=85)
                    except Exception as resize_err:
                        err_logger.warning(
                            f'Preview resize failed for {preview_out}:'
                            f' {resize_err}'
                        )

                    exif_time = None
                    exif_data = {}
                    try:
                        tags_to_query = [
                            "EXIF:DateTimeOriginal",
                            "EXIF:ExposureTime",
                            "EXIF:FNumber",
                            "EXIF:ISO",
                            "EXIF:ExposureBiasValue",
                            "EXIF:FocalLength",
                            "EXIF:Make",
                            "EXIF:Model",
                            "EXIF:LensModel",
                            "EXIF:GPSLatitude",
                            "EXIF:GPSLongitude",
                            "IPTC:Keywords",
                            "IPTC:City",
                            "IPTC:Province-State",
                            "IPTC:Country-PrimaryLocationName",
                            "XMP-dc:Subject",
                            "XMP-lr:HierarchicalSubject"
                        ]
                        time_meta = et.get_tags(str(raw_path), tags_to_query)
                        if time_meta and len(time_meta) > 0:
                            exif_data = time_meta[0]
                            exif_time = exif_data.get("EXIF:DateTimeOriginal", None)
                    except Exception:
                        pass

                    extraction_queue.put({
                        "raw": raw_path,
                        "preview": preview_out,
                        "exif_time": exif_time,
                        "exif_data": exif_data
                    })
                else:
                    write_queue.put({"raw": raw_path, "status": "failed"})
    except Exception as e:
        err_logger.critical(f"ExifTool extraction crashed: {e}")
    finally:
        extraction_queue.put(None)
        logging.info("Worker Extractor finished.")

# --- WORKER 2: GPU INFERENCE (CONSUMER / PRODUCER) ---
def worker_inference():
    logging.info("Worker Inference started.")
    iteration = 0
    pano_seq = 1
    last_time = None
    last_hash = ""
    last_metadata = {}
    last_landmark_location = None
    last_exif = {}
    
    vram_flush_interval = settings["processing"]["vram_flush_interval"]
    phash_thresh = settings["processing"]["phash_similarity_threshold"]
    window_secs = settings["processing"]["burst_timeline_window_seconds"]
    vlm_model = settings["processing"]["vlm_model"]

    while True:
        task = extraction_queue.get()
        if task is None:
            write_queue.put(None)
            break

        preview_path = task["preview"]
        iteration += 1

        # Fast local programmatic image metrics
        try:
            with Image.open(preview_path) as img:
                aspect = img.size[0] / float(img.size[1]) if img.size[1] > 0 else 1.0
                phash = str(imagehash.phash(img))
                stat = ImageStat.Stat(img.convert('RGB').resize((100, 100)))
                r, g, b = stat.mean
                is_mono = (abs(r - g) + abs(r - b) + abs(g - b)) < 3.0
        except Exception as e:
            err_logger.error(f"Image analysis error on {preview_path}: {e}")
            aspect = 1.0
            phash = ""
            is_mono = False

        # Sequence Timeline Logic (Panoramas)
        dt_current = None
        is_pano_element = False
        if task["exif_time"]:
            try:
                dt_current = datetime.strptime(task["exif_time"], "%Y:%m:%d %H:%M:%S")
                if last_time and (dt_current - last_time).total_seconds() < window_secs:
                    is_pano_element = True
            except Exception:
                pass

        # VLM Inference vs Perceptual Hash Bypass
        is_burst_match = False
        if last_hash and phash:
            try:
                dist = imagehash.hex_to_hash(phash) - imagehash.hex_to_hash(last_hash)
                if dist <= phash_thresh:
                    is_burst_match = True
            except Exception:
                pass

        if is_burst_match:
            ai_data = last_metadata.copy()
            status = 'inherited'
        else:
            # Downsize preview for optimal VLM token budget if too large (896x896)
            try:
                with Image.open(preview_path) as im:
                    if max(im.size) > 896:
                        im.thumbnail((896, 896), Image.Resampling.LANCZOS)
                        im.convert("RGB").save(preview_path, format="JPEG", quality=85)
            except Exception:
                pass

            # Extract technical camera & embedded metadata for context injection
            curr_exif = task.get("exif_data", {})
            context_hints = []
            if curr_exif.get("EXIF:Make") or curr_exif.get("EXIF:Model"):
                context_hints.append(f"Camera: {curr_exif.get('EXIF:Make', '')} {curr_exif.get('EXIF:Model', '')}".strip())
            if curr_exif.get("EXIF:LensModel"):
                context_hints.append(f"Lens: {curr_exif.get('EXIF:LensModel')}")
            if curr_exif.get("EXIF:FocalLength"):
                context_hints.append(f"Focal Length: {curr_exif.get('EXIF:FocalLength')}mm")
            if curr_exif.get("EXIF:ExposureTime"):
                context_hints.append(f"Shutter: {curr_exif.get('EXIF:ExposureTime')}s")
            if curr_exif.get("EXIF:FNumber"):
                context_hints.append(f"Aperture: f/{curr_exif.get('EXIF:FNumber')}")
            if curr_exif.get("EXIF:ISO"):
                context_hints.append(f"ISO: {curr_exif.get('EXIF:ISO')}")

            existing_kws = []
            for tag in ["IPTC:Keywords", "XMP-dc:Subject", "XMP:Subject"]:
                val = curr_exif.get(tag)
                if val:
                    if isinstance(val, list):
                        existing_kws.extend(val)
                    elif isinstance(val, str):
                        existing_kws.append(val)
            if existing_kws:
                context_hints.append(f"Pre-existing Embedded Keywords: {', '.join(set(existing_kws))}")

            loc_hints = []
            for tag in ["IPTC:City", "IPTC:Province-State", "IPTC:Country-PrimaryLocationName"]:
                if curr_exif.get(tag):
                    loc_hints.append(str(curr_exif.get(tag)))
            if loc_hints:
                context_hints.append(f"Embedded Location Data: {', '.join(loc_hints)}")

            prompt_text = "Analyze photograph based on directive."
            if context_hints:
                prompt_text += "\n\nEmbedded File Metadata Context:\n" + "\n".join(f"- {h}" for h in context_hints)

            target_model = resolve_ollama_model(vlm_model)
            try:
                resp = ollama.generate(
                    model=target_model,
                    system=DIRECTIVE,
                    prompt=prompt_text,
                    images=[str(preview_path)],
                    keep_alive="1h",
                    format="json",
                    options={
                        "num_ctx": 4096,
                        "num_predict": 768,
                        "temperature": 0.1
                    }
                )
                raw_response = resp.get('response', '')
                try:
                    ai_data = json.loads(raw_response)
                except Exception:
                    match = re.search(r"\{.*\}", raw_response, re.DOTALL)
                    ai_data = json.loads(match.group(0)) if match else {}
            except Exception as e:
                err_logger.error(f"Ollama inference error on {task['raw']}: {e}")
                ai_data = {"primary_category": "Error", "confidence_score": 0.0}

            # Sanitize single-value category fields (prevent pipe-separated lists in folder names)
            extra_pipe_tags = []
            for field in ["primary_category", "sub_category", "lighting_atmosphere"]:
                val = ai_data.get(field)
                if isinstance(val, str) and ("|" in val or "/" in val or "," in val):
                    parts = [p.strip() for p in re.split(r"[|/,]", val) if p.strip()]
                    if parts:
                        ai_data[field] = parts[0].replace(" ", "_") if field != "primary_category" else parts[0]
                        extra_pipe_tags.extend(p.replace(" ", "_") for p in parts[1:])
                elif isinstance(val, str) and field == "sub_category":
                    ai_data[field] = val.strip().replace(" ", "_")

            if extra_pipe_tags:
                existing_fk = ai_data.get("flat_keywords", [])
                if not isinstance(existing_fk, list):
                    existing_fk = []
                existing_fk.extend(extra_pipe_tags)
                ai_data["flat_keywords"] = existing_fk

            status = 'tagged'
            last_hash = phash
            last_metadata = ai_data

        # Initialize keyword collections
        flat_keywords = []
        if isinstance(ai_data.get('flat_keywords'), list):
            flat_keywords.extend(ai_data['flat_keywords'])
        if isinstance(ai_data.get('key_subjects'), list):
            flat_keywords.extend(ai_data['key_subjects'])

        hierarchical_keywords = []
        if isinstance(ai_data.get('hierarchical_keywords'), list):
            hierarchical_keywords.extend(ai_data['hierarchical_keywords'])

        # Preserve any pre-existing embedded keywords
        for ek in existing_kws:
            if ek not in flat_keywords:
                flat_keywords.append(ek)

        # 1. Subcategory and lighting
        if ai_data.get('sub_category'):
            flat_keywords.append(ai_data['sub_category'])
        if ai_data.get('lighting_atmosphere'):
            flat_keywords.append(ai_data['lighting_atmosphere'])
            hierarchical_keywords.append(f"Lighting|{ai_data['lighting_atmosphere']}")

        # 2. People & demographic details
        people_list = ai_data.get('people_details', [])
        if isinstance(people_list, list):
            for p in people_list:
                if isinstance(p, dict):
                    demo = p.get('demographic')
                    hair = p.get('hair')
                    if demo:
                        flat_keywords.append(demo)
                        hierarchical_keywords.append(f"People|{demo}")
                    if hair:
                        flat_keywords.append(hair)
                        hierarchical_keywords.append(f"People|Hair|{hair}")
                    if p.get('features') and isinstance(p['features'], list):
                        flat_keywords.extend(p['features'])

        # 3. EXIF Time-Burst Location Inheritance
        conf_score = float(ai_data.get('confidence_score', 0.0))
        has_known_location = ai_data.get('country') and ai_data['country'] != 'Unknown' and ai_data.get('specific_location') and ai_data['specific_location'] != 'Unknown'

        if conf_score >= 0.85 and has_known_location and dt_current:
            last_landmark_location = {
                "country": ai_data['country'],
                "state_region": ai_data.get('state_region'),
                "specific_location": ai_data['specific_location'],
                "timestamp": dt_current
            }
        elif last_landmark_location and dt_current and not has_known_location:
            time_diff = abs((dt_current - last_landmark_location["timestamp"]).total_seconds())
            if time_diff <= 900.0:  # 15-minute burst window
                ai_data['country'] = last_landmark_location['country']
                if last_landmark_location.get('state_region'):
                    ai_data['state_region'] = last_landmark_location['state_region']
                ai_data['specific_location'] = last_landmark_location['specific_location']
                flat_keywords.append("Location_Burst_Inherited")
                hierarchical_keywords.append("Metadata|Location Inherited")
                if conf_score < 0.70:
                    ai_data['confidence_score'] = 0.80

        # Build hierarchical place tree if location is identified
        if ai_data.get('country') and ai_data['country'] != 'Unknown':
            country_val = ai_data['country']
            region_val = ai_data.get('state_region') or 'General'
            loc_val = ai_data.get('specific_location')
            if loc_val and loc_val != 'Unknown':
                hierarchical_keywords.append(f"Places|{country_val}|{region_val}|{loc_val}")
            else:
                hierarchical_keywords.append(f"Places|{country_val}|{region_val}")

        # 4. Technical Exposure & Focus Bracketing Analysis
        curr_exif = task.get("exif_data", {})
        ev_bias = curr_exif.get("EXIF:ExposureBiasValue")
        if ev_bias is not None and ev_bias != 0:
            flat_keywords.append(f"EV_{ev_bias}EV")

        is_bracket_element = False
        if dt_current and last_time and (dt_current - last_time).total_seconds() < 4.0:
            same_focal = curr_exif.get("EXIF:FocalLength") and curr_exif.get("EXIF:FocalLength") == last_exif.get("EXIF:FocalLength")
            diff_ev = ev_bias is not None and last_exif.get("EXIF:ExposureBiasValue") is not None and ev_bias != last_exif.get("EXIF:ExposureBiasValue")
            diff_shutter = curr_exif.get("EXIF:ExposureTime") and last_exif.get("EXIF:ExposureTime") and curr_exif.get("EXIF:ExposureTime") != last_exif.get("EXIF:ExposureTime")
            if same_focal and (diff_ev or diff_shutter):
                is_bracket_element = True
                flat_keywords.append("Exposure_Bracket_HDR_Sequence")
                hierarchical_keywords.append("Technical|Exposure|Exposure Bracket HDR")

        # 5. Strict Panorama Logic (No false positives on indoor/family/pet shots)
        primary_cat = ai_data.get('primary_category', '')
        is_scenic_candidate = primary_cat in ["Landscape", "Architecture", "Aerial", "Travel"] and primary_cat != "Family"
        has_people_or_pets = bool(people_list) or any("Person:" in s or "Domestic" in s or "cat" in s.lower() or "dog" in s.lower() for s in flat_keywords)

        if aspect > settings["processing"]["aspect_ratio_pano_threshold"] and is_scenic_candidate:
            flat_keywords.append("Stitched_Panorama")
            hierarchical_keywords.append("Technical|Composition|Stitched Panorama")
        elif is_pano_element and is_scenic_candidate and not has_people_or_pets and not is_bracket_element:
            flat_keywords.append(f"Pano_Sequence_Group_{pano_seq}")
            hierarchical_keywords.append(f"Technical|Sequences|Pano Sequence {pano_seq}")
        else:
            pano_seq += 1

        if is_mono:
            flat_keywords.append("Monochrome")
            hierarchical_keywords.append("Technical|Monochrome")

        # 6. Biometric face recognition
        faces_detected = detect_faces(preview_path)
        if faces_detected:
            flat_keywords.extend(faces_detected)
            for f_name in faces_detected:
                clean_name = f_name.replace("Person: ", "").strip()
                hierarchical_keywords.append(f"People|Named|{clean_name}")

        last_time = dt_current
        last_exif = curr_exif

        ai_data['flat_keywords'] = list(dict.fromkeys(flat_keywords))
        ai_data['hierarchical_keywords'] = list(dict.fromkeys(hierarchical_keywords))
        ai_data['key_subjects'] = ai_data['flat_keywords']

        write_queue.put({
            "raw": task["raw"],
            "ai_data": ai_data,
            "status": status,
            "phash": phash,
            "time": task["exif_time"]
        })

        # Cleanup preview cache to preserve local disk space
        preview_path.unlink(missing_ok=True)
        extraction_queue.task_done()

    logging.info("Worker Inference finished.")

# --- WORKER 3: METADATA WRITER (CONSUMER) ---
def worker_writer(progress, task_id):
    logging.info("Worker Writer started.")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    uncommitted_count = 0

    try:
        with exiftool.ExifToolHelper() as et:
            while True:
                task = write_queue.get()
                if task is None:
                    break

                raw_path = task["raw"]

                if task["status"] == "failed":
                    cursor.execute(
                        "INSERT OR REPLACE INTO photo_records (file_path, status) VALUES (?, ?)",
                        (str(raw_path), "failed")
                    )
                else:
                    ai_data = task["ai_data"]

                    kwds = list(ai_data.get('flat_keywords', ai_data.get('key_subjects', [])))
                    if ai_data.get('primary_category'):
                        kwds.append(ai_data['primary_category'])
                    if ai_data.get('sub_category'):
                        kwds.append(ai_data['sub_category'])
                    if ai_data.get('specific_location') and ai_data['specific_location'] != 'Unknown':
                        kwds.append(ai_data['specific_location'])
                    if ai_data.get('confidence_score', 1.0) < settings["processing"]["confidence_review_threshold"]:
                        kwds.append("Location_Review_Needed")

                    # Deduplicate tags
                    unique_flat = list(dict.fromkeys(kwds))
                    unique_hierarchical = list(dict.fromkeys(ai_data.get('hierarchical_keywords', [])))

                    if not unique_flat and not unique_hierarchical:
                        unique_flat = ["Needs_Review"]
                        unique_hierarchical = ["Review|Needs Review"]

                    # Embed Dublin Core / IPTC Subject tags and Lightroom Hierarchical Subject directly into the image file
                    et_args = [
                        "-overwrite_original",
                        "-XMP-xmp:CreatorTool=AI Photo & RAW Archival System",
                        "-XMP-dc:Subject=",
                        "-Keywords=",
                        "-XMP-lr:HierarchicalSubject="
                    ]
                    for k in unique_flat:
                        et_args.append(f"-XMP-dc:Subject+={k}")
                        et_args.append(f"-Keywords+={k}")

                    for hk in unique_hierarchical:
                        et_args.append(f"-XMP-lr:HierarchicalSubject+={hk}")

                    et_args.append(str(raw_path))

                    try:
                        et.execute(*et_args)
                        # Remove any legacy .xmp sidecars if present so no loose .xmp files remain
                        raw_path.with_suffix('.xmp').unlink(missing_ok=True)
                        (raw_path.parent / (raw_path.name + '.xmp')).unlink(missing_ok=True)
                    except Exception as e:
                        err_logger.error(f"Direct Metadata Embed Error on {raw_path}: {e}")

                    cursor.execute("""
                        INSERT OR REPLACE INTO photo_records 
                        (file_path, phash, exif_time, status, primary_category, country, state_region, specific_location, key_subjects, is_aerial, confidence_score, hierarchical_keywords, sub_category)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        str(raw_path),
                        task["phash"],
                        task["time"],
                        task["status"],
                        ai_data.get('primary_category'),
                        ai_data.get('country'),
                        ai_data.get('state_region'),
                        ai_data.get('specific_location'),
                        json.dumps(unique_flat),
                        1 if ai_data.get('is_aerial') else 0,
                        float(ai_data.get('confidence_score', 0.0)),
                        json.dumps(unique_hierarchical),
                        ai_data.get('sub_category')
                    ))

                uncommitted_count += 1
                if uncommitted_count >= 25 or write_queue.empty():
                    conn.commit()
                    uncommitted_count = 0

                write_queue.task_done()
                if progress and task_id is not None:
                    progress.advance(task_id)
    except Exception as e:
        err_logger.critical(f"Worker Writer crashed: {e}")
    finally:
        try:
            conn.commit()
            conn.close()
        except Exception:
            pass
        logging.info("Worker Writer finished.")

def run_pipeline(custom_source: Path = None, force_rescan: bool = False):
    init_db()
    init_biometrics()

    # Asynchronously pre-warm VLM to eliminate cold-start delay
    threading.Thread(target=prewarm_vlm, args=(settings["processing"]["vlm_model"],), daemon=True).start()

    target_source = Path(custom_source) if custom_source else SOURCE_DIR
    supported = set(ext.lower() for ext in settings["processing"]["supported_extensions"])
    if not target_source.exists():
        print(f"[Warning] SOURCE_DIR '{target_source}' does not exist or is not mounted.")
        return

    conn = sqlite3.connect(DB_PATH)
    if force_rescan:
        src_resolved = str(target_source.resolve())
        conn.execute("DELETE FROM photo_records WHERE file_path LIKE ?", (f"{src_resolved}%",))
        conn.commit()
        existing = set()
    else:
        existing = set(
            row[0] for row in conn.execute(
                "SELECT file_path FROM photo_records WHERE status IN ('tagged', 'inherited')"
            )
        )
    conn.close()

    all_files = [p for p in target_source.rglob("*") if p.suffix.lower() in supported]
    pending_files = [f for f in all_files if str(f) not in existing]

    # Chronologically / naturally sort pending files to guarantee burst sequence proximity
    pending_files.sort(key=lambda p: (str(p.parent), p.name.lower()))

    print(f"Target Source: {target_source}")
    print(f"Total RAW/image files found: {len(all_files)}")
    print(f"Already cataloged in DB: {len(existing)}")
    print(f"Pending files to process: {len(pending_files)}")

    if not pending_files:
        print("Archive is already 100% processed!")
        return

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("({task.completed}/{task.total})"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
    ) as progress:
        task_id = progress.add_task("[cyan]Processing Archive...", total=len(pending_files))
        t1 = threading.Thread(target=worker_extractor, args=(pending_files,))
        t2 = threading.Thread(target=worker_inference)
        t3 = threading.Thread(target=worker_writer, args=(progress, task_id))

        t1.start()
        t2.start()
        t3.start()

        t1.join()
        t2.join()
        t3.join()

    print("Pipeline processing completed successfully!")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AI Photo & RAW Archival Ingestion Pipeline")
    parser.add_argument("--source", type=str, default=None, help="Custom source directory containing photos")
    parser.add_argument("--force", action="store_true", help="Force a fresh re-scan even if photos are already in DB")
    args = parser.parse_args()

    run_pipeline(custom_source=Path(args.source) if args.source else None, force_rescan=args.force)
