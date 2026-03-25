"""
AuraFS Smart Structure – background recommendation script.

Run standalone:  python smart_structure.py
Or triggered by the web server.

Reads TARGET_FOLDER and OLLAMA_API_URL from .env, walks the directory tree
(top 3 levels), then:
  - For each file, asks Ollama whether it should be moved to a better folder.
  - For each folder, asks Ollama whether it should be renamed.

Results are written to data/structure_recommendations.json.
"""
import json
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

TARGET_FOLDER = os.getenv("TARGET_FOLDER", os.path.expanduser("~"))
OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
OUTPUT_FILE = os.path.join(DATA_DIR, "structure_recommendations.json")
STATUS_FILE = os.path.join(DATA_DIR, "structure_status.json")

os.makedirs(DATA_DIR, exist_ok=True)

MAX_DEPTH = 3
MAX_FOLDER_ITEMS_FOR_PROMPT = 30  # Limit items sent to Ollama per folder to keep prompts concise


def _write_status(status, recommendations=None):
    payload = {"status": status}
    if recommendations is not None:
        payload["recommendations"] = recommendations
    with open(OUTPUT_FILE, "w") as f:
        json.dump(payload, f, indent=2)
    with open(STATUS_FILE, "w") as f:
        json.dump({"status": status}, f)


def _ollama_generate(prompt):
    """Call Ollama generate API and return the response text."""
    try:
        resp = requests.post(
            f"{OLLAMA_API_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as exc:
        print(f"Ollama error: {exc}", file=sys.stderr)
        return None


def _extract_json(text):
    """Extract first JSON object from a text blob."""
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        return None
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError:
        return None


def _walk_tree(root, max_depth=3):
    """Yield (path, is_dir, depth) for everything under root up to max_depth."""
    for dirpath, dirnames, filenames in os.walk(root):
        # Calculate depth relative to root
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth >= max_depth:
            dirnames[:] = []  # Don't descend further
            continue
        # Filter hidden
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fname in filenames:
            if fname.startswith("."):
                continue
            yield os.path.join(dirpath, fname), False, depth
        yield dirpath, True, depth


def _collect_all_folders(root, max_depth=3):
    folders = []
    for dirpath, dirnames, _files in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth >= max_depth:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        folders.append(dirpath)
    return folders


def recommend_file_move(filepath, all_folders, root):
    """Ask Ollama if a file should be moved to a better folder."""
    filename = os.path.basename(filepath)
    current_dir = os.path.dirname(filepath)
    folder_list = "\n".join(
        f"  - {os.path.relpath(f, root)}" for f in all_folders if f != current_dir
    )
    prompt = (
        f"You are a file organisation assistant.\n"
        f"File: '{filename}'\n"
        f"Current folder: '{os.path.relpath(current_dir, root)}'\n"
        f"Available folders:\n{folder_list}\n\n"
        f"Should this file be moved to a more appropriate folder based on its name?\n"
        f"Reply with a JSON object only (no extra text):\n"
        f'{{"should_move": true/false, "target_folder": "<relative path or empty>", "reason": "<brief reason>"}}'
    )
    text = _ollama_generate(prompt)
    result = _extract_json(text)
    if result and result.get("should_move") and result.get("target_folder"):
        target_abs = os.path.normpath(os.path.join(root, result["target_folder"]))
        return {
            "type": "move",
            "source": filepath,
            "target_folder": target_abs,
            "reason": result.get("reason", ""),
            "display": f"Move '{filename}' → '{result['target_folder']}'",
        }
    return None


def recommend_folder_rename(folderpath, root):
    """Ask Ollama if a folder should be renamed based on its contents."""
    try:
        entries = [
            e.name
            for e in os.scandir(folderpath)
            if not e.name.startswith(".")
        ]
    except PermissionError:
        return None
    if not entries:
        return None
    items_str = ", ".join(entries[:MAX_FOLDER_ITEMS_FOR_PROMPT])
    folder_name = os.path.basename(folderpath)
    prompt = (
        f"You are a file organisation assistant.\n"
        f"Folder name: '{folder_name}'\n"
        f"Contents: {items_str}\n\n"
        f"Should this folder be renamed to better reflect its contents?\n"
        f"Reply with a JSON object only (no extra text):\n"
        f'{{"should_rename": true/false, "new_name": "<name or empty>", "reason": "<brief reason>"}}'
    )
    text = _ollama_generate(prompt)
    result = _extract_json(text)
    if result and result.get("should_rename") and result.get("new_name"):
        new_name = result["new_name"].strip()
        if new_name and new_name != folder_name:
            return {
                "type": "rename",
                "source": folderpath,
                "new_name": new_name,
                "reason": result.get("reason", ""),
                "display": f"Rename '{folder_name}' → '{new_name}'",
            }
    return None


def main():
    _write_status("running", [])
    recommendations = []

    try:
        all_folders = _collect_all_folders(TARGET_FOLDER, MAX_DEPTH)

        # File move recommendations
        for dirpath, dirnames, filenames in os.walk(TARGET_FOLDER):
            rel = os.path.relpath(dirpath, TARGET_FOLDER)
            depth = 0 if rel == "." else rel.count(os.sep) + 1
            if depth >= MAX_DEPTH:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fname in filenames:
                if fname.startswith("."):
                    continue
                fpath = os.path.join(dirpath, fname)
                rec = recommend_file_move(fpath, all_folders, TARGET_FOLDER)
                if rec:
                    recommendations.append(rec)

        # Folder rename recommendations (skip root)
        for folder in all_folders:
            if os.path.normpath(folder) == os.path.normpath(TARGET_FOLDER):
                continue
            rec = recommend_folder_rename(folder, TARGET_FOLDER)
            if rec:
                recommendations.append(rec)

    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        _write_status("error", [])
        return

    _write_status("completed", recommendations)
    print(f"Done. {len(recommendations)} recommendations written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
