"""
AuraFS Smart Connection – background recommendation script.

Run standalone:  python smart_connection.py
Or triggered by the web server.

Reads TARGET_FOLDER from .env, lists all files, computes pairwise name
similarity using difflib, and inserts "related" recommendations into the
SQLite DB for any pairs above the similarity threshold that are not already
rejected.
"""
import json
import os
import sys
from difflib import SequenceMatcher

from dotenv import load_dotenv

import db as dbmod

load_dotenv()

TARGET_FOLDER = os.getenv("TARGET_FOLDER", os.path.expanduser("~"))

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
STATUS_FILE = os.path.join(DATA_DIR, "connection_status.json")

os.makedirs(DATA_DIR, exist_ok=True)

SIMILARITY_THRESHOLD = 0.5


def _write_status(status):
    with open(STATUS_FILE, "w") as f:
        json.dump({"status": status}, f)


def _collect_files(root):
    """Return list of all file paths under root (relative names as keys)."""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fname in filenames:
            if fname.startswith("."):
                continue
            files.append(os.path.join(dirpath, fname))
    return files


def _name_similarity(a, b):
    """Return similarity ratio between two file base names (without extension)."""
    stem_a = os.path.splitext(os.path.basename(a))[0].lower()
    stem_b = os.path.splitext(os.path.basename(b))[0].lower()
    return SequenceMatcher(None, stem_a, stem_b).ratio()


def main():
    dbmod.init_db()
    _write_status("running")

    try:
        files = _collect_files(TARGET_FOLDER)
        n = len(files)
        inserted = 0

        for i in range(n):
            for j in range(i + 1, n):
                f1 = files[i]
                f2 = files[j]
                # Skip if already rejected
                if dbmod.is_rejected(f1, f2) or dbmod.is_rejected(f2, f1):
                    continue
                sim = _name_similarity(f1, f2)
                if sim >= SIMILARITY_THRESHOLD:
                    # Ensure canonical order to avoid duplicates
                    a, b = sorted([f1, f2])
                    dbmod.upsert_pending(a, b, "related")
                    inserted += 1

        print(f"Done. {inserted} recommendations added/updated.")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        _write_status("error")
        return

    _write_status("completed")


if __name__ == "__main__":
    main()
