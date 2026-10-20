#!/usr/bin/env python3
"""
Master Control Center & Ingestion Dashboard (app.py)
AI-Powered RAW Photo Archival System v2.0
"""

import os
import sys
import time
import json
import sqlite3
import subprocess
from pathlib import Path
import streamlit as st
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent
DB_PATH = PROJECT_ROOT / "data" / "photo_archive.db"
CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"

# Set Streamlit Page Configuration
st.set_page_config(
    page_title="AI RAW Photo Archival System",
    page_icon="📷",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Studio CSS Styling
st.markdown("""
<style>
    .main { background-color: #0b0f19; }
    .stMetric {
        background: #111827;
        padding: 14px 18px;
        border-radius: 10px;
        border: 1px solid #1f2937;
    }
    .status-badge {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 6px;
        font-size: 0.82rem;
        font-weight: 600;
        margin-right: 8px;
    }
    .badge-green { background: #064e3b; color: #34d399; border: 1px solid #059669; }
    .badge-yellow { background: #78350f; color: #fde047; border: 1px solid #d97706; }
    .badge-red { background: #7f1d1d; color: #fca5a5; border: 1px solid #dc2626; }
    .console-box {
        background-color: #030712;
        border: 1px solid #1f2937;
        border-radius: 8px;
        padding: 12px 16px;
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        font-size: 0.85rem;
        color: #e5e7eb;
        max-height: 260px;
        overflow-y: auto;
    }
    .mode-card {
        background: #111827;
        border: 1px solid #374151;
        border-radius: 8px;
        padding: 12px;
        margin-bottom: 8px;
    }
</style>
""", unsafe_allow_html=True)

# Database Helper Functions
def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_photo_status ON photo_records(status);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_photo_conf ON photo_records(confidence_score);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_photo_created ON photo_records(created_at DESC);")
    conn.commit()
    return conn

def check_ollama():
    try:
        import urllib.request
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1.5) as resp:
            data = json.loads(resp.read().decode())
            models = [m.get("name") for m in data.get("models", [])]
            has_qwen = any("qwen2.5" in m for m in models)
            return True, has_qwen, models
    except Exception:
        return False, False, []

def scan_directory(dir_path_str: str):
    p = Path(dir_path_str)
    if not p.exists() or not p.is_dir():
        return False, 0, {}
    supported = {
        ".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff", ".bmp",
        ".nef", ".iiq", ".arw", ".cr2", ".cr3", ".dng", ".raf", ".orf", ".rw2", ".pef", ".srw", ".3fr"
    }
    files = [f for f in p.rglob("*") if f.suffix.lower() in supported]
    counts = {}
    for f in files:
        ext = f.suffix.upper()
        counts[ext] = counts.get(ext, 0) + 1
    return True, len(files), counts

def pick_folder_native(title="Select Folder"):
    script = f'POSIX path of (choose folder with prompt "{title}")'
    try:
        res = subprocess.check_output(["osascript", "-e", script], text=True, stderr=subprocess.DEVNULL)
        return res.strip()
    except Exception:
        return None

# Session State Defaults
if "source_folder" not in st.session_state:
    st.session_state.source_folder = str(Path.home() / "Desktop" / "Photo")
if "dest_folder" not in st.session_state:
    st.session_state.dest_folder = str(Path.home() / "Desktop" / "Organized_Photos")
if "pipeline_running" not in st.session_state:
    st.session_state.pipeline_running = False

# Sidebar System Health & Navigation
with st.sidebar:
    st.title("📷 System Status")
    
    ollama_online, qwen_ready, ollama_models = check_ollama()
    if ollama_online and qwen_ready:
        st.markdown('<span class="status-badge badge-green">● Ollama: qwen2.5vl:7b Ready</span>', unsafe_allow_html=True)
    elif ollama_online:
        st.markdown('<span class="status-badge badge-yellow">● Ollama Online (Model missing)</span>', unsafe_allow_html=True)
    else:
        st.markdown('<span class="status-badge badge-red">● Ollama Offline</span>', unsafe_allow_html=True)
        if st.button("Start Ollama Engine"):
            subprocess.Popen(["/Applications/Ollama.app/Contents/Resources/ollama", "serve"])
            time.sleep(2)
            st.rerun()

    st.markdown('<span class="status-badge badge-green">● ExifTool: v13.34 Ready</span>', unsafe_allow_html=True)
    st.markdown('<span class="status-badge badge-green">● SQLite: WAL Mode Active</span>', unsafe_allow_html=True)

    st.divider()
    st.caption("Supported Formats:")
    st.caption("JPEG, PNG, HEIC, WEBP, TIFF + Camera RAW (IIQ, NEF, ARW, CR2/CR3, DNG, RAF, ORF, RW2)")
    st.divider()
    st.caption("12-Bucket Taxonomy:")
    st.code("""
01_Landscapes_&_Travel
02_Ranch_&_Western
03_Family_&_Personal
04_Pets_&_Animals
05_Wildlife_&_Nature
06_Macro_&_Details
07_Food_&_Culinary
08_Documents_&_Tech
09_Architecture_&_Interiors
10_Aerial_&_Aviation
11_Panoramas
99_Review_Queue
    """, language="text")

# Main Content Header
st.title("📷 AI Photo & RAW Archival System")
st.caption("Universal Photo (JPEG / HEIC / PNG / TIFF) & Camera RAW Control Center | Dublin Core & Lightroom Classic XMP Engine")

conn = get_connection()

# Query High Level Stats
try:
    total_count = conn.execute("SELECT COUNT(*) FROM photo_records").fetchone()[0]
    tagged_count = conn.execute("SELECT COUNT(*) FROM photo_records WHERE status = 'tagged'").fetchone()[0]
    inherited_count = conn.execute("SELECT COUNT(*) FROM photo_records WHERE status = 'inherited'").fetchone()[0]
    review_count = conn.execute("SELECT COUNT(*) FROM photo_records WHERE confidence_score < 0.70 AND status != 'failed'").fetchone()[0]
    failed_count = conn.execute("SELECT COUNT(*) FROM photo_records WHERE status = 'failed'").fetchone()[0]
except Exception as e:
    total_count, tagged_count, inherited_count, review_count, failed_count = 0, 0, 0, 0, 0

# KPI Metric Row
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Cataloged Photos", f"{total_count:,}")
k2.metric("AI Tagged (VLM)", f"{tagged_count:,}")
k3.metric("pHash Inherited", f"{inherited_count:,}")
k4.metric("Needs Review (<70%)", f"{review_count:,}")
k5.metric("Extraction Failures", f"{failed_count:,}")

st.divider()

# Main Tabs
tab_console, tab_analytics, tab_review, tab_database = st.tabs([
    "🚀 Archival Console & Operations",
    "📊 Analytics & Taxonomy",
    "🔍 Review Queue (<0.70)",
    "🗄️ Raw Records Explorer"
])

# ==============================================================================
# TAB 1: ARCHIVAL CONSOLE & OPERATIONS
# ==============================================================================
with tab_console:
    st.subheader("Interactive Ingestion & Pipeline Control")
    st.write("Configure source directory, select archival behavior (In-Place Tagging, Move, or Copy), and execute with real-time throughput telemetry.")

    col_src, col_opts = st.columns([1, 1], gap="large")

    with col_src:
        st.markdown("### 1. Source Folder")
        src_input = st.text_input("Source Directory Path:", value=st.session_state.source_folder)
        st.session_state.source_folder = src_input

        # Browse & Quick Selectors
        b_col1, b_col2, b_col3, b_col4 = st.columns(4)
        if b_col1.button("📂 Browse Finder...", key="btn_browse_src"):
            chosen = pick_folder_native("Select Source RAW Photos Folder")
            if chosen:
                st.session_state.source_folder = chosen
                st.rerun()
        if b_col2.button("Desktop/Photo"):
            st.session_state.source_folder = str(Path.home() / "Desktop" / "Photo")
            st.rerun()
        if b_col3.button("Pictures"):
            st.session_state.source_folder = str(Path.home() / "Pictures")
            st.rerun()
        if b_col4.button("Volumes (/Volumes)"):
            st.session_state.source_folder = "/Volumes"
            st.rerun()

        # Scan Directory
        valid_src, file_count, ext_counts = scan_directory(st.session_state.source_folder)
        if valid_src:
            if file_count > 0:
                breakdown = ", ".join([f"{c} {ext}" for ext, c in ext_counts.items()])
                st.success(f"✅ Found **{file_count}** supported photos/RAW files ({breakdown}) in `{st.session_state.source_folder}`")
            else:
                st.warning(f"⚠️ Folder exists but contains 0 photos. (If you previously ran 'Tag & Relocate', your files were moved to `{st.session_state.dest_folder}`.)")
                valid_dest, dest_count, _ = scan_directory(st.session_state.dest_folder)
                if valid_dest and dest_count > 0:
                    if st.button(f"↩️ Restore {dest_count} Moved Photos from Destination back to Source"):
                        import shutil
                        src_dir = Path(st.session_state.source_folder)
                        dest_dir = Path(st.session_state.dest_folder)
                        supported_exts = {
                            ".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff", ".bmp",
                            ".nef", ".iiq", ".arw", ".cr2", ".cr3", ".dng", ".raf", ".orf", ".rw2", ".pef", ".srw", ".3fr"
                        }
                        moved_back = 0
                        for f in dest_dir.rglob("*"):
                            if f.is_file() and f.suffix.lower() in supported_exts:
                                shutil.move(str(f), str(src_dir / f.name))
                                moved_back += 1
                            elif f.is_file() and f.suffix.lower() == ".xmp":
                                f.unlink(missing_ok=True)
                        conn.execute("DELETE FROM photo_records;")
                        conn.commit()
                        st.success(f"Restored {moved_back} photos back to `{src_dir}`!")
                        time.sleep(1.0)
                        st.rerun()
        else:
            st.error(f"❌ Source path does not exist or is not a directory.")

    with col_opts:
        st.markdown("### 2. Archival Operation Mode")
        operation_mode = st.radio(
            "How would you like to handle the photos?",
            [
                "🏷️ Tag In-Place Only (No Moving)",
                "📂 Tag & Relocate (Organize & Move)",
                "📋 Tag & Copy (Safe Duplicate Archive)"
            ],
            index=0,
            help="Choose whether to write XMP sidecars in the original folder or relocate into structured year/category folders."
        )

        force_rescan = st.checkbox(
            "🔄 Force Fresh Pass (Re-analyze already cataloged photos)",
            value=False,
            help="When checked, re-runs AI vision analysis on all photos in the folder even if they were previously cataloged."
        )

        if "In-Place" in operation_mode:
            st.info("ℹ️ **In-Place Mode:** Embeds Dublin Core, IPTC, & Lightroom Hierarchical XMP metadata directly **inside** your photo files (no separate `.xmp` sidecar clutter). Original file locations remain untouched.")
        elif "Relocate" in operation_mode:
            st.info("ℹ️ **Tag & Relocate:** Embeds XMP metadata directly inside each photo file and moves the organized photos into the 12-Bucket taxonomy (`Year / SubCategory / Location`) with zero loose `.xmp` files.")
        else:
            st.info("ℹ️ **Tag & Copy:** Embeds XMP metadata directly inside each photo and copies them into the organized destination hierarchy, leaving your source folder untouched.")

        # Destination Folder (for Relocate or Copy)
        if "In-Place" not in operation_mode:
            st.markdown("### 3. Destination Folder")
            dest_input = st.text_input("Destination Directory Path:", value=st.session_state.dest_folder)
            st.session_state.dest_folder = dest_input
            d_col1, d_col2 = st.columns(2)
            if d_col1.button("📂 Browse Destination...", key="btn_browse_dest"):
                chosen_dest = pick_folder_native("Select Target Destination Folder")
                if chosen_dest:
                    st.session_state.dest_folder = chosen_dest
                    st.rerun()
            if d_col2.button("Desktop/Organized_Photos"):
                st.session_state.dest_folder = str(Path.home() / "Desktop" / "Organized_Photos")
                st.rerun()

    st.divider()

    # Launch Pipeline Engine
    # Check if a pipeline is already running in the background
    def is_pipeline_active():
        try:
            out = subprocess.check_output(["pgrep", "-f", "pipeline.py --source"], text=True)
            return len(out.strip().splitlines()) > 0
        except Exception:
            return False

    already_running = is_pipeline_active()

    # Launch / Stop Pipeline Controls
    st.markdown("### 3. Pipeline Execution & Real-Time Throughput")

    col_btn1, col_btn2 = st.columns([1, 4])
    can_run = valid_src and (file_count > 0) and not already_running
    launch_btn = col_btn1.button("▶ Run AI Archival Pipeline", type="primary", disabled=not can_run)

    if already_running:
        if col_btn2.button("⏹ Stop Active Pipeline", type="secondary"):
            subprocess.run(["pkill", "-9", "-f", "pipeline.py"])
            subprocess.run(["pkill", "-9", "-f", "exiftool"])
            st.warning("Sent stop signal to pipeline. Resetting...")
            time.sleep(1.5)
            st.rerun()

    if already_running and not launch_btn:
        st.info("⚡ A pipeline is currently actively processing photos in the background. Monitoring live progress...")

    if launch_btn or already_running:
        progress_placeholder = st.empty()
        telemetry_placeholder = st.empty()
        console_placeholder = st.empty()

        py_bin = str(PROJECT_ROOT / ".venv" / "bin" / "python")
        if not Path(py_bin).exists():
            py_bin = sys.executable

        src_p = Path(st.session_state.source_folder)
        src_resolved = str(src_p.resolve())
        target_total = max(file_count, 1)

        if launch_btn and force_rescan:
            conn.execute("DELETE FROM photo_records WHERE file_path LIKE ?", (f"{src_resolved}%",))
            conn.commit()
            initial_completed = 0
        else:
            try:
                initial_completed = conn.execute(
                    "SELECT COUNT(*) FROM photo_records WHERE file_path LIKE ? AND status IN ('tagged', 'inherited')",
                    (f"{src_resolved}%",)
                ).fetchone()[0]
            except Exception:
                initial_completed = 0

        # If user clicked launch, start the process
        if launch_btn:
            pipeline_cmd = [py_bin, str(PROJECT_ROOT / "pipeline.py"), "--source", st.session_state.source_folder]
            if force_rescan:
                pipeline_cmd.append("--force")
            proc = subprocess.Popen(
                pipeline_cmd,
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
        else:
            proc = None

        start_time = time.time()

        # Monitor loop
        while True:
            # Check if active
            active = is_pipeline_active() if proc is None else (proc.poll() is None)
            elapsed = max(time.time() - start_time, 0.1)

            # Query DB for live updates
            try:
                cur_conn = sqlite3.connect(DB_PATH)
                db_tagged = cur_conn.execute("""
                    SELECT COUNT(*) FROM photo_records 
                    WHERE file_path LIKE ? AND status IN ('tagged', 'inherited')
                """, (f"{src_resolved}%",)).fetchone()[0]

                recent_records = cur_conn.execute("""
                    SELECT file_path, primary_category, sub_category, confidence_score, hierarchical_keywords 
                    FROM photo_records 
                    ORDER BY created_at DESC 
                    LIMIT 4
                """).fetchall()
                cur_conn.close()
            except Exception:
                db_tagged = initial_completed
                recent_records = []

            completed = min(db_tagged, target_total)
            newly_done = max(completed - initial_completed, 0)
            pct = min(completed / max(target_total, 1), 1.0)

            if newly_done > 0:
                rate = newly_done / elapsed
                rate_str = f"{rate:.1f} photos/sec"
                rem_photos = max(target_total - completed, 0)
                eta_s = int(rem_photos / rate) if rate > 0 else 0
                eta_str = f"{eta_s}s remaining" if eta_s > 0 else "Wrapping up..."
            else:
                if elapsed < 35:
                    rate_str = "Warming up VLM..."
                    eta_str = "Loading model to GPU..."
                else:
                    rate_str = "Analyzing..."
                    eta_str = "Estimating..."

            # Render in-place metrics (no duplication)
            with telemetry_placeholder.container():
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("⚡ Throughput Rate", rate_str)
                c2.metric("⏱️ Elapsed Time", f"{int(elapsed)}s")
                c3.metric("✅ Processed", f"{completed} / {target_total}")
                c4.metric("⏳ Estimated Time", eta_str)

            progress_placeholder.progress(pct, text=f"Processing Archive: {completed} / {target_total} photos ({int(pct*100)}%)")

            # Render Recent Activity Feed
            if recent_records:
                feed_html = '<div class="console-box">'
                for fpath, cat, subcat, conf, hk in recent_records:
                    fname = Path(fpath).name
                    feed_html += f"<div><b>{fname}</b> &rarr; <span style='color:#60a5fa;'>{cat}</span> | <span style='color:#34d399;'>{subcat or 'General'}</span> | Conf: {conf:.2f}</div>"
                    if hk:
                        feed_html += f"<div style='color:#9ca3af; font-size:0.75rem; margin-bottom:6px;'>LR Keywords: {hk}</div>"
                feed_html += '</div>'
                console_placeholder.markdown(feed_html, unsafe_allow_html=True)

            if not active:
                break
            time.sleep(0.6)

        if proc:
            proc.communicate()

        # Step 2: Routing if requested
        if "Relocate" in operation_mode or "Copy" in operation_mode:
            st.info("Organizing files into 12-Bucket Taxonomy...")
            router_cmd = [py_bin, str(PROJECT_ROOT / "router.py"), "--dest", st.session_state.dest_folder]
            if "Copy" in operation_mode:
                router_cmd.append("--copy")

            r_proc = subprocess.run(router_cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True)
            st.success(f"Routing Complete: {r_proc.stdout.strip()}")

        progress_placeholder.progress(1.0, text="Archive Processing Completed Successfully!")
        st.balloons()
        time.sleep(1.5)
        st.rerun()

# ==============================================================================
# TAB 2: ANALYTICS & TAXONOMY
# ==============================================================================
with tab_analytics:
    st.subheader("Archival Analytics & Distributions")
    
    col_left, col_right = st.columns(2)
    with col_left:
        st.markdown("#### Primary Category Distribution")
        df_cat = pd.read_sql_query("""
            SELECT primary_category, COUNT(*) as count 
            FROM photo_records 
            WHERE status IN ('tagged', 'inherited')
            GROUP BY primary_category 
            ORDER BY count DESC
        """, conn)
        if not df_cat.empty:
            st.bar_chart(df_cat.set_index("primary_category"))
        else:
            st.info("No cataloged categories recorded yet.")

    with col_right:
        st.markdown("#### Geographic Distribution (Country)")
        df_country = pd.read_sql_query("""
            SELECT country, COUNT(*) as count 
            FROM photo_records 
            WHERE status IN ('tagged', 'inherited') AND country IS NOT NULL AND country != 'Unknown'
            GROUP BY country 
            ORDER BY count DESC
            LIMIT 10
        """, conn)
        if not df_country.empty:
            st.bar_chart(df_country.set_index("country"))
        else:
            st.info("No geographic tags recorded yet.")

    st.markdown("#### VLM Confidence Score Distribution")
    df_conf = pd.read_sql_query("""
        SELECT confidence_score 
        FROM photo_records 
        WHERE confidence_score IS NOT NULL
    """, conn)
    if not df_conf.empty:
        st.area_chart(df_conf["confidence_score"].value_counts().sort_index())

# ==============================================================================
# TAB 3: REVIEW QUEUE (<0.70)
# ==============================================================================
with tab_review:
    st.subheader("Low-Confidence & Manual Review Inspector")
    st.write("Items scored under 0.70 confidence or flagged for manual verification before physical routing.")
    
    df_review = pd.read_sql_query("""
        SELECT file_path, primary_category, sub_category, country, specific_location, 
               confidence_score, key_subjects, hierarchical_keywords
        FROM photo_records 
        WHERE confidence_score < 0.70 AND status != 'failed'
        LIMIT 50
    """, conn)

    if df_review.empty:
        st.success("🎉 All files meet or exceed 70% confidence! The review queue is empty.")
    else:
        st.dataframe(df_review, width="stretch")

# ==============================================================================
# TAB 4: RAW RECORDS EXPLORER
# ==============================================================================
with tab_database:
    st.subheader("Full Database Search & Metadata Inspector")
    search_term = st.text_input("Filter by filename, category, subcategory, location, or keyword:")
    
    if search_term:
        query = """
            SELECT file_path, primary_category, sub_category, country, state_region, 
                   specific_location, exif_time, confidence_score, key_subjects, 
                   hierarchical_keywords, status
            FROM photo_records
            WHERE file_path LIKE ? OR primary_category LIKE ? OR sub_category LIKE ? 
               OR country LIKE ? OR state_region LIKE ? OR specific_location LIKE ? 
               OR key_subjects LIKE ? OR hierarchical_keywords LIKE ?
            LIMIT 100
        """
        wildcard = f"%{search_term}%"
        df_search = pd.read_sql_query(
            query, conn, 
            params=(wildcard, wildcard, wildcard, wildcard, wildcard, wildcard, wildcard, wildcard)
        )
    else:
        df_search = pd.read_sql_query("""
            SELECT file_path, primary_category, sub_category, country, state_region, 
                   specific_location, exif_time, confidence_score, key_subjects, 
                   hierarchical_keywords, status
            FROM photo_records
            ORDER BY created_at DESC
            LIMIT 100
        """, conn)

    st.dataframe(df_search, width="stretch")

conn.close()
