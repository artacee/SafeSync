# ChronoVault

**ChronoVault** is an intelligent, completely safe photo and video organizer script designed to rescue messy media scattered across old hard drives, SD cards, and laptops.

It deeply scans your media, extracts the true original capture dates (even from raw video bytes), and meticulously organizes them into a pristine `Year > Month > Photos/Videos` hierarchy—all without ever modifying your original files.

## Features

- **True Duplicate Finder:** Cross-run hash checking ensures you never copy the same file twice, even if the filename is completely different.
- **Corrupt File Detection:** Uses `Pillow` to verify the physical integrity of photos before copying them. Broken files are safely quarantined.
- **HEIC to JPEG Conversion:** Automatically detects iPhone `.heic` formats and prompts to create highly-compatible JPEG copies alongside them.
- **Deep Date Extraction:** Reads EXIF data for photos and performs binary byte-parsing on MP4/MOV atoms to find the true capture date.
- **GUI Folder Picker:** Plug-and-play UI out of the box using `tkinter`. No hardcoded paths.
- **100% Safe:** Operates in copy-only mode. Your original messy drives are never touched or modified.
- **Full Audit Trail:** Generates a detailed `manifest.csv` logging exactly where every file came from, where it went, and how its date was calculated.
- **Standalone Corrupt Scanner:** Includes a dedicated mode to sweep any existing directory for "bit-rot" and quarantine broken media.

## Prerequisites
- Windows OS (for the `RUN_ME.bat` launcher)
- Python 3.8+

## How to Run

Simply double-click the **`RUN_ME.bat`** file. 

The batch script will automatically ensure you have Python installed, install the required libraries (`Pillow`, `exifread`, `pillow-heif`), and present you with a clean interactive menu:

```text
  ============================================================
   CHOOSE HOW TO RUN:

   [1]  DRY RUN first  (safe preview - nothing is copied)

   [2]  LIVE RUN       (copies files to destination)

   [3]  SCAN FOR CORRUPT FILES  (checks existing folders for broken files)

   [4]  EXIT
  ============================================================
```

If you select Live Run, you'll be prompted via Windows Folder Pickers to select your **Source** folder (messy photos) and your **Destination** folder (where you want them organized).

## Logs & Verification

After a run, the destination folder will contain:
1. `copy_log.txt`: A human-readable execution log.
2. `manifest.csv`: A spreadsheet detailing every single file processed, including skips and errors.
