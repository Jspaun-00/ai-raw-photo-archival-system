# AI-Powered RAW Photo Archival System (v2.0)
## System Architecture & Autonomous Execution Plan

### Executive Summary
The **AI-Powered RAW Photo Archival System** is a decoupled, local-first multi-threaded pipeline designed to autonomously process, classify, and organize 50,000+ uncataloged medium-format and full-frame RAW photographs (Phase One `.IIQ`, Nikon `.NEF`, Sony `.ARW`, Canon `.CR2`/`.CR3`, `.DNG`). 

The architecture eliminates API rate limits and token costs by running local Vision-Language Models (`qwen2.5-vl:7b` via Ollama) and camera header preview extraction via `ExifTool`.

---

### End-to-End Pipeline Architecture

```mermaid
graph TD
    NAS["Unsorted RAW Archive<br/>(/Volumes/NAS_Photos)"] --> P1["Phase 1: Ingestion & Tagging<br/>(pipeline.py)"]
    
    subgraph Phase 1 Processing
        W1["Worker 1: Extractor<br/>ExifTool Header JPEGs"] --> Q1["Extraction Queue"]
        Q1 --> W2["Worker 2: Inference<br/>pHash Bypass + Qwen2.5-VL 7B"]
        W2 --> W2B["Biometrics & Timeline<br/>InsightFace + Pano Seq"]
        W2B --> Q2["Write Queue"]
        Q2 --> W3["Worker 3: Writer<br/>XMP Sidecars + SQLite WAL"]
    end
    
    P1 --> DB[("SQLite Database<br/>photo_archive.db (WAL)")]
    DB --> P2["Phase 2: File Router<br/>(router.py)"]
    
    subgraph Taxonomy Relocation
        P2 --> T1["01_Landscapes_&_Travel"]
        P2 --> T2["02_Ranch_&_Western"]
        P2 --> T3["03_Family_&_Personal"]
        P2 --> T4["10_Aerial_&_Aviation"]
        P2 --> T5["11_Panoramas"]
        P2 --> T99["99_Review_Queue (<70% conf)"]
    end
    
    DB --> DASH["Streamlit Control Hub<br/>(app.py)"]
```

---

### Implemented Project Components

The codebase includes:

1. **[`pipeline.py`](pipeline.py)**:
   - **Multi-threaded producer-consumer queues** (`extraction_queue`, `write_queue`) with bounded memory ceilings.
   - **ExifTool preview extraction** avoiding heavy RAW demosaicing.
   - **Perceptual Hashing (`imagehash.phash`)**: Burst frames within 4 Hamming distance inherit metadata without redundant GPU inference.
   - **VRAM Flush Protocol**: Forces Ollama model memory cleanup every 1,000 frames to prevent macOS memory leaks.
   - **Phase One IIQ Patch**: Uses `.IIQ.xmp` naming convention required by Capture One and Lightroom.

2. **[`router.py`](router.py)**:
   - Evaluates the 12-Bucket Taxonomy priority matrix.
   - Routes low confidence items ($<0.70$) into `99_Review_Queue`.
   - Atomic moves with 3-loop retry decorator to survive transient network drive disconnects.

3. **[`app.py`](app.py)**:
   - Streamlit dashboard providing real-time KPI metrics, category charts, country breakdowns, and an interactive review queue inspector.

4. **[`dry_run.py`](dry_run.py)**:
   - Standalone test harness with synthetic data covering Wyoming ranch shots, Iceland expeditions, aerial captures, family portraits, and panoramas.

5. **[`config/directive.txt`](config/directive.txt)**:
   - Ontological rules and historical context guiding the VLM inference.

6. **[`config/settings.yaml`](config/settings.yaml)**:
   - Centralized configuration for storage paths, models, thresholds, and taxonomy mappings.

7. **[`setup.sh`](setup.sh)**:
   - Automated environment builder and dependency verification script.

---

### Step-by-Step Execution Guide

```bash
# 1. Navigate to the project directory
cd ~/.gemini/antigravity/scratch/raw-photo-archival-system

# 2. Run automated environment setup
./setup.sh

# 3. Launch the dashboard to monitor progress
streamlit run app.py

# 4. In a separate terminal, launch the ingestion pipeline
python3 pipeline.py

# 5. When ingestion completes, execute physical taxonomy routing
python3 router.py
```
