#!/usr/bin/env python3
"""
Metadata Merger: merge_sidecars.py
AI-Powered RAW Photo Archival System v2.0

Embeds .xmp sidecar metadata directly into image files (JPEG, TIFF, DNG, RAW)
using ExifTool, and safely removes the redundant .xmp sidecar files.
"""

import sys
import subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR
BIN_DIR = PROJECT_ROOT / "bin"
EXIFTOOL = str(BIN_DIR / "exiftool") if (BIN_DIR / "exiftool").exists() else "exiftool"

def merge_metadata(target_dir: Path, delete_sidecars: bool = True):
    if not target_dir.exists():
        print(f"Error: Target directory '{target_dir}' does not exist.")
        return

    xmp_files = list(target_dir.rglob("*.xmp"))
    if not xmp_files:
        print(f"No .xmp sidecars found in {target_dir}")
        return

    print(f"Found {len(xmp_files)} .xmp sidecars to merge into image metadata...")
    success_count = 0

    for xmp_path in xmp_files:
        # Find corresponding image file
        # Check standard name (e.g. photo.jpg for photo.xmp) or IIQ patch (photo.IIQ for photo.IIQ.xmp)
        base_name = xmp_path.name[:-4] # strip .xmp
        parent = xmp_path.parent

        candidate_images = [
            parent / base_name,
            parent / f"{xmp_path.stem}.jpg",
            parent / f"{xmp_path.stem}.jpeg",
            parent / f"{xmp_path.stem}.JPG",
            parent / f"{xmp_path.stem}.NEF",
            parent / f"{xmp_path.stem}.ARW",
            parent / f"{xmp_path.stem}.CR2",
            parent / f"{xmp_path.stem}.CR3",
            parent / f"{xmp_path.stem}.DNG",
        ]

        target_image = None
        for cand in candidate_images:
            if cand.exists() and cand != xmp_path:
                target_image = cand
                break

        if not target_image:
            print(f"⚠️ Could not find companion image for: {xmp_path.name}")
            continue

        # Use ExifTool to copy all tags from XMP into the image file directly
        # -tagsfromfile <xmp> -all:all -overwrite_original <image>
        cmd = [
            EXIFTOOL,
            "-overwrite_original",
            "-tagsfromfile", str(xmp_path),
            "-all:all",
            "-XMP:all",
            "-IPTC:Keywords<XMP-dc:Subject",
            str(target_image)
        ]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            success_count += 1
            print(f"✓ Merged XMP -> {target_image.name}")
            if delete_sidecars:
                xmp_path.unlink()
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to merge {xmp_path.name} into {target_image.name}: {e.stderr}")

    print(f"\nCompleted! Successfully merged {success_count}/{len(xmp_files)} sidecars directly into image files.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Merge .xmp sidecars directly into image metadata")
    parser.add_argument("dir", nargs="?", default=str(Path.home() / "Desktop" / "Organized_Photos"), help="Target directory")
    parser.add_argument("--keep-sidecars", action="store_true", help="Do not delete .xmp files after merging")
    args = parser.parse_args()

    merge_metadata(Path(args.dir), delete_sidecars=not args.keep_sidecars)

