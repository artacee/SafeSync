#!/usr/bin/env python3
"""
=============================================================
  PHOTO / VIDEO ORGANISER
  Copies  G:\\old photos  -->  F:\\Organized Photos
  Structure:  Year > Month > Photos|Videos
  Files renamed by capture date
=============================================================
"""

import os
import re
import csv
import sys
import shutil
import struct
import hashlib
import logging
import argparse
import subprocess
import tkinter as tk
from tkinter import filedialog
from pathlib import Path
from datetime import datetime, timedelta
from time import time as wall_time

try:
    import send2trash
    SEND2TRASH_OK = True
except ImportError:
    SEND2TRASH_OK = False

# ── Optional library imports ──────────────────────────────────────────────────
try:
    from PIL import Image
    import pillow_heif
    pillow_heif.register_heif_opener()
    PILLOW_OK = True
except ImportError:
    PILLOW_OK = False

try:
    import exifread
    EXIFREAD_OK = True
except ImportError:
    EXIFREAD_OK = False

# ==============================================================================
#  CONFIGURATION — change these if needed
# ==============================================================================

UNDATED_DIR  = "_Undated"                # Subfolder for files with no detectable date
MIN_YEAR     = 1995                      # Earliest plausible year (early digital cameras)
MAX_YEAR     = 2035                      # Latest plausible year

# ==============================================================================

PHOTO_EXT = {
    '.jpg', '.jpeg', '.png', '.heic', '.heif',
    '.tiff', '.tif', '.bmp', '.webp', '.gif',
    '.cr2', '.cr3', '.nef', '.arw', '.dng',
    '.rw2', '.orf', '.raf', '.raw', '.3fr', '.sr2'
}
VIDEO_EXT = {
    '.mp4', '.mov', '.avi', '.mkv', '.mts', '.m2ts',
    '.3gp', '.m4v', '.wmv', '.flv', '.mxf', '.mpg',
    '.mpeg', '.vob', '.ts', '.divx'
}
ALL_MEDIA = PHOTO_EXT | VIDEO_EXT

MONTHS = {
    1: '01 - January',   2: '02 - February',  3: '03 - March',
    4: '04 - April',     5: '05 - May',        6: '06 - June',
    7: '07 - July',      8: '08 - August',     9: '09 - September',
    10: '10 - October',  11: '11 - November',  12: '12 - December'
}

# Filename date patterns — most specific first
FN_PATTERNS = [
    # 2019-06-15_143022  or  2019-06-15T14:30:22
    (r'(\d{4})[_\-](\d{2})[_\-](\d{2})[_\-T](\d{2})[_\-\.:](\d{2})[_\-\.:](\d{2})', 6),
    # 20190615_143022
    (r'(\d{4})(\d{2})(\d{2})[_\-](\d{2})(\d{2})(\d{2})', 6),
    # 2019-06-15
    (r'(\d{4})[_\-](\d{2})[_\-](\d{2})', 3),
    # 20190615
    (r'(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)', 3),
]


# ==============================================================================
#  HELPERS
# ==============================================================================

def plausible_year(y):
    """Check if a year is within the plausible range."""
    return MIN_YEAR <= y <= MAX_YEAR


def is_photo(path: Path) -> bool:
    return path.suffix.lower() in PHOTO_EXT


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXT


def is_corrupt(path: Path) -> bool:
    """Check if the file is physically corrupted or completely unreadable."""
    try:
        if is_photo(path) and PILLOW_OK:
            with Image.open(path) as img:
                img.verify()
        else:
            # Basic check for videos or if Pillow isn't available
            with open(path, 'rb') as f:
                chunk = f.read(8192)
                if not chunk and path.stat().st_size > 0:
                    return True
        return False
    except Exception:
        return True


def partial_hash(path: Path, chunk_size=8192) -> str:
    """
    Fast content fingerprint: hash the first + last 8 KB of the file.
    This is much faster than hashing the entire file while still being
    reliable enough to distinguish different files of the same size.
    """
    h = hashlib.sha256()
    size = path.stat().st_size
    with open(path, 'rb') as f:
        # Read first chunk
        h.update(f.read(chunk_size))
        # Read last chunk (if file is large enough that it's different)
        if size > chunk_size * 2:
            f.seek(-chunk_size, 2)
            h.update(f.read(chunk_size))
    # Include file size in the hash for extra safety
    h.update(size.to_bytes(8, 'big'))
    return h.hexdigest()


def full_hash(path: Path) -> str:
    """Full SHA-256 of the entire file. Used for copy verification."""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def format_eta(seconds):
    """Format seconds into a human-readable string."""
    if seconds < 0:
        return "calculating..."
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m}m"
    elif m > 0:
        return f"{m}m {s}s"
    else:
        return f"{s}s"


def format_size(bytes_val):
    """Format bytes into a human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} TB"


# ==============================================================================
#  DATE EXTRACTION METHODS
# ==============================================================================

def exif_pillow(path: Path):
    """Read DateTimeOriginal via Pillow (JPEG, PNG, TIFF, WEBP)."""
    if not PILLOW_OK:
        return None
    try:
        img = Image.open(path)
        # Use the modern, non-deprecated API
        exif_data = img.getexif()
        if not exif_data:
            return None
        # Tag IDs: 36867=DateTimeOriginal, 36868=DateTimeDigitized, 306=DateTime
        for tag_id in (36867, 36868, 306):
            val = exif_data.get(tag_id)
            if val and isinstance(val, str):
                val = val.strip()
                if val and val != '0000:00:00 00:00:00':
                    dt = datetime.strptime(val, '%Y:%m:%d %H:%M:%S')
                    if plausible_year(dt.year):
                        return dt
    except Exception:
        pass
    return None


def exif_read(path: Path):
    """Read DateTimeOriginal via exifread (RAW: CR2, NEF, ARW, DNG, etc.)."""
    if not EXIFREAD_OK:
        return None
    try:
        with open(path, 'rb') as f:
            tags = exifread.process_file(f, stop_tag='EXIF DateTimeOriginal', details=False)
        for key in ('EXIF DateTimeOriginal', 'EXIF DateTimeDigitized', 'Image DateTime'):
            val = tags.get(key)
            if val:
                s = str(val).strip()
                if s and s != '0000:00:00 00:00:00':
                    dt = datetime.strptime(s, '%Y:%m:%d %H:%M:%S')
                    if plausible_year(dt.year):
                        return dt
    except Exception:
        pass
    return None


def mp4_atom_date(path: Path):
    """
    Read creation_time from the MP4/MOV mvhd atom.
    No extra library needed — just binary parsing.

    mvhd atom layout (after the 'mvhd' tag):
      offset +0:   version   (1 byte)
      offset +1:   flags     (3 bytes)
      offset +4:   creation_time  (4 bytes if v0, 8 bytes if v1)
    """
    if path.suffix.lower() not in {'.mp4', '.mov', '.m4v', '.3gp', '.m2ts', '.mts'}:
        return None
    try:
        MAC_OFFSET = 2082844800  # seconds between 1904-01-01 and 1970-01-01
        with open(path, 'rb') as f:
            chunk = f.read(2 * 1024 * 1024)  # first 2 MB

        idx = chunk.find(b'mvhd')
        if idx == -1:
            return None

        # Version at idx+4, flags at idx+5..7, creation_time at idx+8
        version = chunk[idx + 4]
        if version == 0:
            raw = struct.unpack('>I', chunk[idx + 8 : idx + 12])[0]
        elif version == 1:
            raw = struct.unpack('>Q', chunk[idx + 8 : idx + 16])[0]
        else:
            return None

        if raw <= 0:
            return None

        unix_ts = raw - MAC_OFFSET
        if unix_ts < 0:
            return None

        dt = datetime.fromtimestamp(unix_ts)
        if not plausible_year(dt.year):
            return None
        return dt
    except Exception:
        return None


def filename_date(path: Path):
    """Try to find a date embedded in the filename."""
    stem = path.stem
    for pattern, groups in FN_PATTERNS:
        m = re.search(pattern, stem)
        if m:
            parts = [int(x) for x in m.groups()]
            try:
                y, mo, d = parts[0], parts[1], parts[2]
                if not (plausible_year(y) and 1 <= mo <= 12 and 1 <= d <= 31):
                    continue
                if groups == 6:
                    h, mi, s = parts[3], parts[4], min(parts[5], 59)
                    return datetime(y, mo, d, h, mi, s)
                else:
                    return datetime(y, mo, d)
            except (ValueError, OverflowError):
                continue
    return None


def file_stat_date(path: Path):
    """
    Last resort: use the earliest of file creation / modification time.
    WARNING: File timestamps are unreliable — they change when files are
    copied between drives. Files dated via this method are flagged as
    'FileStat (unreliable)' in the log.
    """
    try:
        stat = path.stat()
        ts = min(stat.st_ctime, stat.st_mtime)
        dt = datetime.fromtimestamp(ts)
        if plausible_year(dt.year):
            return dt
    except Exception:
        pass
    return None


def get_best_date(path: Path):
    """
    Try all methods in priority order.
    Returns (datetime | None, method_name: str)
    """
    ext = path.suffix.lower()

    if ext in PHOTO_EXT:
        d = exif_pillow(path)
        if d:
            return d, 'EXIF-Pillow'
        d = exif_read(path)
        if d:
            return d, 'EXIF-exifread'

    if ext in VIDEO_EXT:
        d = mp4_atom_date(path)
        if d:
            return d, 'MP4-atom'

    d = filename_date(path)
    if d:
        return d, 'Filename'

    d = file_stat_date(path)
    if d:
        return d, 'FileStat (unreliable)'

    return None, 'Unknown'


# ==============================================================================
#  DESTINATION PATH BUILDER
# ==============================================================================

def build_dest(dt, src: Path, dest_root: Path) -> Path:
    """
    Construct the full destination path with Photo/Video subfolders.
    Handles duplicates by appending a counter.
    """
    stem = src.stem
    ext = src.suffix.lower()
    
    is_screenshot = 'screenshot' in src.name.lower() or 'screenshot' in str(src.parent).lower()
    
    if is_screenshot:
        media_type = "Screenshots"
    else:
        media_type = "Videos" if is_video(src) else "Photos"

    if dt:
        prefix = dt.strftime('%Y-%m-%d_%H%M%S')
        dest_dir = dest_root / str(dt.year) / MONTHS[dt.month] / media_type
    else:
        prefix = 'UNDATED'
        dest_dir = dest_root / UNDATED_DIR / media_type

    dest_dir.mkdir(parents=True, exist_ok=True)

    name = f"{prefix}_{stem}{ext}"
    dest = dest_dir / name

    counter = 2
    while dest.exists():
        # Check if it's the same file using partial hash (fast & reliable)
        try:
            if partial_hash(dest) == partial_hash(src):
                return dest  # caller will detect and skip
        except Exception:
            pass
        dest = dest_dir / f"{prefix}_{stem}_{counter}{ext}"
        counter += 1

    return dest


# ==============================================================================
#  MAIN
# ==============================================================================

def main():
    # ── Parse CLI arguments ───────────────────────────────────────────────
    parser = argparse.ArgumentParser(description="Photo/Video Organiser")
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview only — no files are copied')
    parser.add_argument('--check-corrupt', action='store_true',
                        help='Scan an existing folder for corrupt files')
    parser.add_argument('--space-free', action='store_true',
                        help='Space Freer: Delete files already backed up')
    parser.add_argument('--compress-video', action='store_true',
                        help='Video Compressor: Shrink large videos')
    parser.add_argument('--clean-empty', action='store_true',
                        help='Clean empty folders from source')
    args = parser.parse_args()
    
    dry_run = args.dry_run
    
    if args.space_free:
        run_space_freer()
        return
    if args.compress_video:
        run_video_compressor()
        return
    if args.clean_empty:
        run_clean_empty_folders()
        return

    check_corrupt = args.check_corrupt

    # ── Check Corrupt Standalone Mode ─────────────────────────────────────
    if check_corrupt:
        root = tk.Tk()
        root.withdraw()
        print("\n============================================================")
        print(" Please select the folder you want to scan for corrupt files")
        print("============================================================\n")
        scan_folder = filedialog.askdirectory(title="Select Folder to Scan for Corrupt Files")
        if not scan_folder:
            print("No folder selected. Exiting.")
            sys.exit(0)
            
        scan_path = Path(scan_folder).resolve()
        run_corrupt_scan(scan_path)
        return

    # ── Ask for Folders using Tkinter ─────────────────────────────────────
    root = tk.Tk()
    root.withdraw() # Hide the main window
    
    print("\n============================================================")
    print(" Please select the SOURCE folder (where your old photos are)")
    print("============================================================\n")
    source_folder = filedialog.askdirectory(title="Select SOURCE Folder (Messy Photos)")
    if not source_folder:
        print("No source folder selected. Exiting.")
        sys.exit(0)
        
    print("\n============================================================")
    print(" Please select the DESTINATION folder (where to organize them)")
    print("============================================================\n")
    dest_folder = filedialog.askdirectory(title="Select DESTINATION Folder (Organized Photos)")
    if not dest_folder:
        print("No destination folder selected. Exiting.")
        sys.exit(0)
        
    SOURCE = str(Path(source_folder).resolve())
    DESTINATION = str(Path(dest_folder).resolve())

    # ── Setup destination and logging ─────────────────────────────────────
    dest_root = Path(DESTINATION)
    try:
        dest_root.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"\n  [ERROR] Cannot create destination folder: {DESTINATION}")
        print(f"  Reason: {e}")
        print(f"  Make sure the F: drive is connected and writable.\n")
        sys.exit(1)

    log_path = dest_root / 'copy_log.txt'
    manifest_path = dest_root / 'manifest.csv'

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s  %(levelname)-7s  %(message)s',
        datefmt='%H:%M:%S',
        handlers=[
            logging.FileHandler(log_path, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )
    log = logging.getLogger('organiser')

    # ── Banner ────────────────────────────────────────────────────────────
    mode = '*** DRY RUN - nothing will be copied ***' if dry_run else 'LIVE'
    log.info("=" * 65)
    log.info(f"  PHOTO ORGANISER  {mode}")
    log.info(f"  Source       : {SOURCE}")
    log.info(f"  Destination  : {DESTINATION}")
    log.info("=" * 65)

    if not os.path.isdir(SOURCE):
        log.error(f"Source folder not found: {SOURCE}")
        log.error("Make sure the source drive is connected and visible.")
        return

    # ── Gather all media files ────────────────────────────────────────────
    log.info("Scanning source folder... (this may take a minute)")
    all_files = []
    total_size = 0
    for root, _, files in os.walk(SOURCE):
        for f in files:
            p = Path(root) / f
            if p.suffix.lower() in ALL_MEDIA:
                all_files.append(p)
                try:
                    total_size += p.stat().st_size
                except OSError:
                    pass

    total = len(all_files)
    log.info(f"Found {total:,} media files ({format_size(total_size)}).")
    log.info(f"  Photos: {sum(1 for f in all_files if is_photo(f)):,}")
    log.info(f"  Videos: {sum(1 for f in all_files if is_video(f)):,}")
    log.info("")

    if total == 0:
        log.warning("No media files found. Check the SOURCE path.")
        return

    # ── HEIC Conversion Prompt ────────────────────────────────────────────
    heic_files = [f for f in all_files if f.suffix.lower() in {'.heic', '.heif'}]
    convert_heic = False
    if heic_files and not dry_run:
        print("\n" + "=" * 60)
        print(f" Found {len(heic_files)} HEIC files (iPhone format).")
        print(" Would you like to automatically create a standard JPEG copy")
        print(" alongside each HEIC file so they can be viewed anywhere?")
        print("=" * 60)
        while True:
            ans = input(" Convert HEIC to JPEG? (y/n): ").strip().lower()
            if ans in ['y', 'yes']:
                convert_heic = True
                break
            elif ans in ['n', 'no']:
                break

    # ── Check free space on destination (live run only) ───────────────────
    if not dry_run:
        try:
            free = shutil.disk_usage(dest_root).free
            if total_size > free:
                log.error(f"Not enough space on destination drive!")
                log.error(f"  Need : {format_size(total_size)}")
                log.error(f"  Free : {format_size(free)}")
                log.error("Free up space or use a larger destination drive.")
                return
            else:
                log.info(f"Disk space OK: need {format_size(total_size)}, "
                         f"have {format_size(free)} free.")
                log.info("")
        except Exception:
            pass  # non-critical, continue anyway

    # ── Open manifest CSV ─────────────────────────────────────────────────
    file_exists = manifest_path.exists()
    try:
        csv_file = open(manifest_path, 'a', newline='', encoding='utf-8')
    except PermissionError:
        log.error("")
        log.error("============================================================")
        log.error(f" [FATAL] Cannot write to manifest.csv!")
        log.error(f"         Please close the file in Excel (or any other")
        log.error(f"         program that has it open), then run again.")
        log.error("============================================================")
        sys.exit(1)
    writer = csv.writer(csv_file)
    if not file_exists:
        writer.writerow(['source', 'destination', 'date_method', 'date',
                         'file_size', 'status'])

    # ── Scan Destination for Cross-Run Duplicates ─────────────────────────
    dest_files_by_size = {}
    if dest_root.exists() and not dry_run:
        log.info("Scanning destination to prevent cross-run duplicates...")
        for root_dir, _, files in os.walk(dest_root):
            for f in files:
                p = Path(root_dir) / f
                if p.suffix.lower() in ALL_MEDIA:
                    try:
                        sz = p.stat().st_size
                        if sz not in dest_files_by_size:
                            dest_files_by_size[sz] = []
                        dest_files_by_size[sz].append(p)
                    except Exception:
                        pass

    # ── Process ───────────────────────────────────────────────────────────
    counts = {
        'copied': 0, 'skipped': 0, 'error': 0,
        'undated': 0, 'unreliable_date': 0, 'verified': 0
    }
    method_tally = {}
    start_time = wall_time()
    bytes_copied = 0

    for i, src in enumerate(all_files, 1):
        try:
            dt, method = get_best_date(src)
            method_tally[method] = method_tally.get(method, 0) + 1

            if dt is None:
                counts['undated'] += 1
            elif 'unreliable' in method.lower():
                counts['unreliable_date'] += 1

            src_size = src.stat().st_size

            # ── Check for Corrupt Files ───────────────────────────────────
            if is_corrupt(src):
                counts['error'] += 1
                counts['corrupt'] = counts.get('corrupt', 0) + 1
                corrupt_dest = dest_root / '_Corrupt' / src.name
                corrupt_dest.parent.mkdir(parents=True, exist_ok=True)
                if not dry_run:
                    shutil.copy2(src, corrupt_dest)
                log.error(f"[CORRUPT] {src}  ->  Sent to _Corrupt folder")
                writer.writerow([str(src), str(corrupt_dest), method,
                                 dt.isoformat() if dt else '',
                                 src_size, 'corrupt'])
                continue

            # ── Check for Cross-Run Duplicates ────────────────────────────
            is_duplicate = False
            dest = None
            if src_size in dest_files_by_size and not dry_run:
                try:
                    src_hash = partial_hash(src)
                    for existing_dest in dest_files_by_size[src_size]:
                        if partial_hash(existing_dest) == src_hash:
                            is_duplicate = True
                            dest = existing_dest
                            break
                except Exception:
                    pass
            
            if is_duplicate:
                counts['skipped'] += 1
                writer.writerow([str(src), str(dest), method,
                                 dt.isoformat() if dt else '',
                                 src_size, 'skipped (cross-run)'])
                continue

            dest = build_dest(dt, src, dest_root)

            # ── Skip if same file already at destination ──────────────────
            if dest.exists():
                try:
                    if partial_hash(dest) == partial_hash(src):
                        counts['skipped'] += 1
                        writer.writerow([str(src), str(dest), method,
                                         dt.isoformat() if dt else '',
                                         src_size, 'skipped'])
                        continue
                except Exception:
                    pass

            if dry_run:
                rel = dest.relative_to(dest_root)
                log.info(f"[DRY {i:>6}/{total}] {src.name}  ->  {rel}  ({method})")
                writer.writerow([str(src), str(dest), method,
                                 dt.isoformat() if dt else '',
                                 src.stat().st_size, 'dry-run'])
            else:
                shutil.copy2(src, dest)

                # ── Verify copy: compare file sizes ───────────────────────
                src_size = src.stat().st_size
                dest_size = dest.stat().st_size
                if src_size != dest_size:
                    log.error(f"[VERIFY FAIL] Size mismatch: {src} "
                              f"({src_size} vs {dest_size})")
                    counts['error'] += 1
                    writer.writerow([str(src), str(dest), method,
                                     dt.isoformat() if dt else '',
                                     src_size, 'verify-fail'])
                    continue

                counts['copied'] += 1
                counts['verified'] += 1
                bytes_copied += src_size

                # ── HEIC Conversion ───────────────────────────────────────────
                if convert_heic and src.suffix.lower() in {'.heic', '.heif'} and PILLOW_OK:
                    try:
                        jpg_dest = dest.with_suffix('.jpg')
                        with Image.open(dest) as img:
                            exif = img.getexif()
                            img.convert('RGB').save(jpg_dest, 'JPEG', exif=exif)
                            log.info(f"Converted HEIC to JPEG -> {jpg_dest.name}")
                    except Exception as e:
                        log.warning(f"Failed to convert HEIC {src.name}: {e}")

                writer.writerow([str(src), str(dest), method,
                                 dt.isoformat() if dt else '',
                                 src_size, 'copied'])

                # ── Progress every 100 files or at the end ────────────────
                if i % 100 == 0 or i == total:
                    elapsed = wall_time() - start_time
                    pct = i / total * 100
                    rate = bytes_copied / elapsed if elapsed > 0 else 0
                    remaining = (total_size - bytes_copied) / rate if rate > 0 else -1
                    log.info(
                        f"[{i:>6}/{total}]  {pct:5.1f}%  "
                        f"copied={counts['copied']:,}  "
                        f"skip={counts['skipped']:,}  "
                        f"err={counts['error']:,}  "
                        f"ETA: {format_eta(remaining)}"
                    )

        except Exception as e:
            counts['error'] += 1
            log.error(f"[ERROR] {src}  ->  {e}")
            writer.writerow([str(src), '', '', '', '', f'error: {e}'])

    csv_file.close()

    # ── Summary ───────────────────────────────────────────────────────────
    elapsed = wall_time() - start_time
    corrupt_count = counts.get('corrupt', 0)

    log.info("")
    log.info("=" * 65)
    log.info("  SUMMARY")
    log.info(f"  Total found       : {total:,}")
    log.info(f"  Copied            : {counts['copied']:,}")
    log.info(f"  Verified OK       : {counts['verified']:,}")
    log.info(f"  Skipped           : {counts['skipped']:,}  (already at destination)")
    log.info(f"  Corrupt           : {corrupt_count:,}  (sent to _Corrupt folder)")
    log.info(f"  Undated           : {counts['undated']:,}  (sent to {UNDATED_DIR} folder)")
    log.info(f"  Unreliable date   : {counts['unreliable_date']:,}  (used filesystem timestamp)")
    log.info(f"  Errors            : {counts['error']:,}")
    log.info(f"  Time elapsed      : {format_eta(elapsed)}")
    log.info(f"  Data copied       : {format_size(bytes_copied)}")
    log.info("")
    log.info("  Date detection breakdown:")
    for m, n in sorted(method_tally.items(), key=lambda x: -x[1]):
        bar = '#' * (n * 30 // total) if total else ''
        log.info(f"    {m:<25} {n:>6,}  {bar}")
    log.info("=" * 65)
    log.info(f"  Done!  Check: {DESTINATION}")
    log.info(f"  Full log     : {log_path}")
    log.info(f"  Manifest CSV : {manifest_path}")
    log.info("")

    if corrupt_count > 0:
        log.warning(
            f"  {corrupt_count:,} corrupt files were found and placed in "
            f"the _Corrupt folder. Review them manually."
        )

    if counts['unreliable_date'] > 0:
        log.warning(
            f"  {counts['unreliable_date']:,} files were dated using filesystem "
            f"timestamps (unreliable). Check the manifest CSV and review them."
        )


def run_corrupt_scan(scan_path: Path):
    """Standalone mode to just scan a folder for corrupt media."""
    print(f"\nScanning {scan_path} for media files...")
    all_files = []
    for root, _, files in os.walk(scan_path):
        if '_Corrupt' in root:
            continue  # Don't scan inside the quarantine folder itself
        for f in files:
            p = Path(root) / f
            if p.suffix.lower() in ALL_MEDIA:
                all_files.append(p)

    total = len(all_files)
    if total == 0:
        print("No media files found in the selected folder.")
        return

    print(f"Found {total:,} media files. Starting integrity check...")
    corrupt_dir = scan_path / '_Corrupt'
    report_path = scan_path / 'corrupt_scan_report.txt'
    
    corrupt_count = 0
    start_time = wall_time()

    try:
        report_file = open(report_path, 'w', encoding='utf-8')
        report_file.write(f"CORRUPT SCAN REPORT\n")
        report_file.write(f"Folder: {scan_path}\n")
        report_file.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        report_file.write("=" * 60 + "\n\n")
    except Exception as e:
        print(f"Error creating report file: {e}")
        return

    for i, p in enumerate(all_files, 1):
        if is_corrupt(p):
            corrupt_count += 1
            # Move to corrupt dir
            corrupt_dest = corrupt_dir / p.name
            corrupt_dir.mkdir(exist_ok=True)
            
            # Handle collision in corrupt dir
            counter = 2
            stem = p.stem
            ext = p.suffix
            while corrupt_dest.exists():
                corrupt_dest = corrupt_dir / f"{stem}_{counter}{ext}"
                counter += 1
                
            try:
                shutil.move(str(p), str(corrupt_dest))
                report_file.write(f"[CORRUPT] {p} -> MOVED TO -> {corrupt_dest}\n")
            except Exception as e:
                report_file.write(f"[CORRUPT] {p} -> ERROR MOVING: {e}\n")
                
        if i % 100 == 0 or i == total:
            elapsed = wall_time() - start_time
            rate = i / elapsed if elapsed > 0 else 0
            rem = (total - i) / rate if rate > 0 else 0
            # Print over the same line for a clean progress bar
            sys.stdout.write(f"\r  [{i:>6}/{total}]  Found: {corrupt_count} corrupt files  (ETA: {format_eta(rem)})")
            sys.stdout.flush()

    report_file.write(f"\n\nTotal Scanned: {total}\n")
    report_file.write(f"Total Corrupt: {corrupt_count}\n")
    report_file.close()

    print("\n\n" + "=" * 60)
    print(" SCAN COMPLETE")
    print(f" Total files checked: {total:,}")
    print(f" Corrupt files found: {corrupt_count:,}")
    if corrupt_count > 0:
        print(f" The corrupt files have been moved to: {corrupt_dir}")
    print(f" Full report saved to: {report_path}")
    print("=" * 60 + "\n")


def run_space_freer():
    """Scan a laptop folder and an archive folder, and delete laptop files already backed up."""
    if not SEND2TRASH_OK:
        print("\n[ERROR] The 'send2trash' library is missing.")
        print("Please re-run RUN_ME.bat to automatically install it.")
        return

    root = tk.Tk()
    root.withdraw()
    print("\n============================================================")
    print(" SPACE FREER: Step 1 - Select Laptop/Source Folder to Clean")
    print("============================================================\n")
    source_dir = filedialog.askdirectory(title="Select SOURCE Folder to Clean")
    if not source_dir: return

    print("\n============================================================")
    print(" SPACE FREER: Step 2 - Select Archive/Destination Folder")
    print("============================================================\n")
    archive_dir = filedialog.askdirectory(title="Select ARCHIVE Folder")
    if not archive_dir: return

    src_path = Path(source_dir).resolve()
    arch_path = Path(archive_dir).resolve()

    print(f"\nScanning Archive ({arch_path}) for file sizes...")
    archive_sizes = {}
    for r, _, files in os.walk(arch_path):
        for f in files:
            p = Path(r) / f
            try:
                sz = p.stat().st_size
                if sz not in archive_sizes:
                    archive_sizes[sz] = []
                archive_sizes[sz].append(p)
            except Exception: pass

    print(f"\nScanning Source ({src_path}) for identical backed-up files...")
    to_delete = []
    space_saved = 0
    scanned = 0
    for r, _, files in os.walk(src_path):
        for f in files:
            p = Path(r) / f
            scanned += 1
            try:
                sz = p.stat().st_size
                if sz in archive_sizes:
                    # OPTIMIZATION: Do a lightning-fast partial hash check first
                    src_partial = partial_hash(p)
                    possible_matches = []
                    for arch_file in archive_sizes[sz]:
                        if partial_hash(arch_file) == src_partial:
                            possible_matches.append(arch_file)
                            
                    # If partial hashes match, confirm with slow full SHA-256 for safety
                    if possible_matches:
                        src_full = full_hash(p)
                        for arch_file in possible_matches:
                            if full_hash(arch_file) == src_full:
                                to_delete.append(p)
                                space_saved += sz
                                break
            except Exception: pass
            
            if scanned % 50 == 0:
                sys.stdout.write(f"\r  Scanned: {scanned:,} | Found Matches: {len(to_delete):,}")
                sys.stdout.flush()

    sys.stdout.write(f"\r  Scanned: {scanned:,} | Found Matches: {len(to_delete):,}\n")
    sys.stdout.flush()

    print("\n" + "=" * 60)
    print(f" Found {len(to_delete):,} files already safely backed up.")
    print(f" Total space to free: {format_size(space_saved)}")
    print("=" * 60)

    if not to_delete:
        print("Nothing to do. Your source drive is clean!")
        return

    ans = input("\nType 'DELETE' to safely move these files to the Recycle Bin: ")
    if ans == 'DELETE':
        print("\nDeleting...")
        success = 0
        for i, p in enumerate(to_delete, 1):
            try:
                send2trash.send2trash(str(p))
                success += 1
            except Exception as e:
                print(f"  [ERROR] Failed to trash {p.name}: {e}")
        print(f"\nDone! Successfully moved {success:,} files to the Recycle Bin.")
        
        # Auto-trigger Empty Folder cleanup
        print("\nSweeping source for empty folders left behind...")
        empty_folders = []
        for r, dirs, files in os.walk(src_path, topdown=False):
            if Path(r) == src_path: 
                continue
            try:
                if not os.listdir(r):
                    empty_folders.append(Path(r))
            except Exception: pass
            
        if empty_folders:
            print(f"Found {len(empty_folders)} empty folders.")
            ans_clean = input("Remove these empty folders too? (y/n): ").strip().lower()
            if ans_clean in ['y', 'yes']:
                cleaned = 0
                for d in empty_folders:
                    try:
                        d.rmdir()
                        cleaned += 1
                    except Exception: pass
                print(f"Cleaned up {cleaned} empty folders.")
    else:
        print("\nOperation cancelled. No files were deleted.")


def run_video_compressor():
    """Scan a folder for large videos and compress them using FFmpeg H.265."""
    try:
        subprocess.run(['ffmpeg', '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except Exception:
        print("\n============================================================")
        print(" [ERROR] FFmpeg is not installed or not on your PATH.")
        print(" This feature requires FFmpeg to perform the video compression.")
        print(" Download it from: https://ffmpeg.org/download.html")
        print("============================================================\n")
        return

    root = tk.Tk()
    root.withdraw()
    print("\n============================================================")
    print(" VIDEO COMPRESSOR: Select Folder to Scan")
    print("============================================================\n")
    scan_dir = filedialog.askdirectory(title="Select Folder with Videos")
    if not scan_dir: return
    
    scan_path = Path(scan_dir).resolve()
    threshold_mb = 50
    threshold_bytes = threshold_mb * 1024 * 1024
    
    print(f"\nScanning {scan_path} for videos larger than {threshold_mb} MB...")
    large_videos = []
    for r, _, files in os.walk(scan_path):
        for f in files:
            p = Path(r) / f
            if is_video(p):
                try:
                    if p.stat().st_size > threshold_bytes:
                        if '_original' not in p.name and '_compressed' not in p.name:
                            large_videos.append(p)
                except Exception: pass

    if not large_videos:
        print("No large videos found matching criteria.")
        return
        
    print("\n" + "=" * 60)
    print(f" Found {len(large_videos)} large videos.")
    print("=" * 60)
    for v in large_videos:
        print(f" - {v.name} ({format_size(v.stat().st_size)})")
        
    ans = input("\nCompress these videos with H.265 to save space? (y/n): ").strip().lower()
    if ans in ['y', 'yes']:
        for i, v in enumerate(large_videos, 1):
            print(f"\n[{i}/{len(large_videos)}] Compressing {v.name}...")
            out_file = v.with_name(f"{v.stem}_compressed.mp4")
            try:
                cmd = [
                    'ffmpeg', '-y', '-i', str(v), 
                    '-vcodec', 'libx265', '-crf', '28', '-preset', 'fast', 
                    '-acodec', 'aac', '-b:a', '128k', 
                    str(out_file)
                ]
                subprocess.run(cmd, check=True)
                
                orig_renamed = v.with_name(f"{v.stem}_original{v.suffix}")
                v.rename(orig_renamed)
                
                final_name = v.with_suffix('.mp4')
                out_file.rename(final_name)
                print(f"  [SUCCESS] Saved compressed version. Original kept as '{orig_renamed.name}'.")
            except Exception as e:
                print(f"  [ERROR] Compression failed for {v.name}: {e}")
                if out_file.exists():
                    try: out_file.unlink()
                    except: pass
        print("\nAll compressions finished.")
    else:
        print("\nCancelled.")


def run_clean_empty_folders():
    """Scan a directory bottom-up and remove all completely empty folders."""
    root = tk.Tk()
    root.withdraw()
    print("\n============================================================")
    print(" CLEAN EMPTY FOLDERS: Select Source Folder")
    print("============================================================\n")
    scan_dir = filedialog.askdirectory(title="Select Folder to Clean")
    if not scan_dir: return
    
    scan_path = Path(scan_dir).resolve()
    print(f"\nScanning {scan_path} for empty folders...")
    
    empty_folders = []
    for r, dirs, files in os.walk(scan_path, topdown=False):
        if Path(r) == scan_path: 
            continue
        try:
            if not os.listdir(r):
                empty_folders.append(Path(r))
        except Exception: pass
        
    if not empty_folders:
        print("No empty folders found.")
        return
        
    print("\n" + "=" * 60)
    print(f" Found {len(empty_folders)} completely empty folders.")
    print("=" * 60)
    ans = input("Safely remove them all? (y/n): ").strip().lower()
    if ans in ['y', 'yes']:
        success = 0
        for d in empty_folders:
            try:
                d.rmdir()
                print(f"  Deleted: {d.relative_to(scan_path)}")
                success += 1
            except Exception as e:
                print(f"  Failed to delete {d.name}: {e}")
        print(f"\nDone! Removed {success} empty folders.")
    else:
        print("Cancelled.")


if __name__ == '__main__':
    main()
