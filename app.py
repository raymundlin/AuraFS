"""
AuraFS Flask web server.

Run:  python app.py
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

import db as dbmod

load_dotenv()

TARGET_FOLDER = os.getenv("TARGET_FOLDER", os.path.expanduser("~"))
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
STRUCTURE_FILE = os.path.join(DATA_DIR, "structure_recommendations.json")
STRUCTURE_STATUS_FILE = os.path.join(DATA_DIR, "structure_status.json")
CONNECTION_STATUS_FILE = os.path.join(DATA_DIR, "connection_status.json")

os.makedirs(DATA_DIR, exist_ok=True)
dbmod.init_db()

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_total_folder_size(path):
    """Return total byte size of all files under *path*."""
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(dirpath, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def _get_last_used_date(filepath):
    """Return last-used datetime for *filepath* using mdls (macOS) or atime."""
    try:
        result = subprocess.run(
            ["mdls", "-name", "kMDItemLastUsedDate", filepath],
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = result.stdout.strip()
        # e.g. "kMDItemLastUsedDate = 2023-01-15 12:34:56 +0000"
        if "= (null)" in output or output == "":
            return None
        parts = output.split(" = ", 1)
        if len(parts) < 2:
            return None
        date_str = parts[1].strip()
        # Parse "2023-01-15 12:34:56 +0000"
        dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S %z")
        return dt
    except Exception:
        # Fallback: use file access time
        try:
            atime = os.path.getatime(filepath)
            return datetime.fromtimestamp(atime, tz=timezone.utc)
        except OSError:
            return None


PERIOD_DAYS = {"month": 30, "year": 365, "3years": 1095}


def _get_directory_tree(path, max_depth=3, current_depth=0):
    """Return a nested dict representing directory tree up to max_depth."""
    name = os.path.basename(path) or path
    node = {"name": name, "path": path, "type": "directory", "children": []}
    if current_depth >= max_depth:
        return node
    try:
        entries = sorted(os.scandir(path), key=lambda e: e.name)
    except PermissionError:
        return node
    for entry in entries:
        if entry.name.startswith("."):
            continue
        if entry.is_dir(follow_symlinks=False):
            child = _get_directory_tree(entry.path, max_depth, current_depth + 1)
            node["children"].append(child)
        else:
            node["children"].append(
                {"name": entry.name, "path": entry.path, "type": "file"}
            )
    return node


def _read_status_file(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"status": "idle"}


def _write_status_file(path, data):
    with open(path, "w") as f:
        json.dump(data, f)


# ---------------------------------------------------------------------------
# Routes – pages
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# Routes – disk info
# ---------------------------------------------------------------------------


@app.route("/api/disk-info")
def disk_info():
    try:
        usage = shutil.disk_usage(TARGET_FOLDER)
        folder_size = _get_total_folder_size(TARGET_FOLDER)
        return jsonify(
            {
                "folder_size": folder_size,
                "disk_total": usage.total,
                "disk_free": usage.free,
                "disk_used": usage.used,
                "target_folder": TARGET_FOLDER,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Routes – smart deletion
# ---------------------------------------------------------------------------


@app.route("/api/unused-files")
def unused_files():
    period = request.args.get("period", "year")
    days = PERIOD_DAYS.get(period, 365)
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    results = []
    try:
        for dirpath, _dirs, files in os.walk(TARGET_FOLDER):
            for fname in files:
                if fname.startswith("."):
                    continue
                fpath = os.path.join(dirpath, fname)
                try:
                    size = os.path.getsize(fpath)
                except OSError:
                    continue
                last_used = _get_last_used_date(fpath)
                if last_used is None or last_used < cutoff:
                    results.append(
                        {
                            "path": fpath,
                            "name": fname,
                            "size": size,
                            "last_used": last_used.isoformat() if last_used else None,
                        }
                    )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    results.sort(key=lambda x: x["size"], reverse=True)
    return jsonify(results)


@app.route("/api/delete-file", methods=["DELETE"])
def delete_file():
    data = request.get_json(force=True)
    path = data.get("path", "")
    # Security: ensure path is under TARGET_FOLDER
    real_target = os.path.realpath(TARGET_FOLDER)
    real_path = os.path.realpath(path)
    try:
        if os.path.commonpath([real_target, real_path]) != real_target:
            return jsonify({"error": "Path outside target folder"}), 400
    except ValueError:
        return jsonify({"error": "Path outside target folder"}), 400
    try:
        os.remove(real_path)
        return jsonify({"success": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Routes – smart structure
# ---------------------------------------------------------------------------


@app.route("/api/directory-tree")
def directory_tree():
    try:
        tree = _get_directory_tree(TARGET_FOLDER)
        return jsonify(tree)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/structure-status")
def structure_status():
    return jsonify(_read_status_file(STRUCTURE_STATUS_FILE))


@app.route("/api/trigger-structure", methods=["POST"])
def trigger_structure():
    status = _read_status_file(STRUCTURE_STATUS_FILE)
    if status.get("status") == "running":
        return jsonify({"message": "Already running"})
    _write_status_file(STRUCTURE_STATUS_FILE, {"status": "running"})
    _write_status_file(STRUCTURE_FILE, {"status": "running", "recommendations": []})
    script = os.path.join(os.path.dirname(__file__), "smart_structure.py")
    subprocess.Popen([sys.executable, script])
    return jsonify({"message": "Started"})


@app.route("/api/structure-recommendations")
def structure_recommendations():
    data = _read_status_file(STRUCTURE_FILE)
    return jsonify(data)


@app.route("/api/execute-recommendation", methods=["POST"])
def execute_recommendation():
    data = request.get_json(force=True)
    rec_type = data.get("type")
    real_target = os.path.realpath(TARGET_FOLDER)

    try:
        if rec_type == "move":
            src = os.path.realpath(data.get("source", ""))
            dst_dir = os.path.realpath(data.get("target_folder", ""))
            try:
                if os.path.commonpath([real_target, src]) != real_target:
                    return jsonify({"error": "Source outside target folder"}), 400
                if os.path.commonpath([real_target, dst_dir]) != real_target:
                    return jsonify({"error": "Destination outside target folder"}), 400
            except ValueError:
                return jsonify({"error": "Path outside target folder"}), 400
            dst = os.path.join(dst_dir, os.path.basename(src))
            os.makedirs(dst_dir, exist_ok=True)
            os.rename(src, dst)
            return jsonify({"success": True})

        elif rec_type == "rename":
            src = os.path.realpath(data.get("source", ""))
            new_name = data.get("new_name", "").strip()
            try:
                if os.path.commonpath([real_target, src]) != real_target:
                    return jsonify({"error": "Source outside target folder"}), 400
            except ValueError:
                return jsonify({"error": "Source outside target folder"}), 400
            if not new_name or os.sep in new_name or new_name in (".", ".."):
                return jsonify({"error": "Invalid new name"}), 400
            dst = os.path.join(os.path.dirname(src), new_name)
            os.rename(src, dst)
            return jsonify({"success": True})

        return jsonify({"error": "Unknown recommendation type"}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Routes – smart connection
# ---------------------------------------------------------------------------


@app.route("/api/trigger-connections", methods=["POST"])
def trigger_connections():
    status = _read_status_file(CONNECTION_STATUS_FILE)
    if status.get("status") == "running":
        return jsonify({"message": "Already running"})
    _write_status_file(CONNECTION_STATUS_FILE, {"status": "running"})
    script = os.path.join(os.path.dirname(__file__), "smart_connection.py")
    subprocess.Popen([sys.executable, script])
    return jsonify({"message": "Started"})


@app.route("/api/connection-status")
def connection_status():
    return jsonify(_read_status_file(CONNECTION_STATUS_FILE))


@app.route("/api/connection-recommendations")
def connection_recommendations():
    try:
        recs = dbmod.get_pending_recommendations()
        return jsonify(recs)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/connection-response", methods=["POST"])
def connection_response():
    data = request.get_json(force=True)
    rec_id = data.get("id")
    response = data.get("response")
    if response not in ("accepted", "rejected"):
        return jsonify({"error": "response must be accepted or rejected"}), 400
    try:
        dbmod.set_status(rec_id, response)
        return jsonify({"success": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/connected-files")
def connected_files():
    filename = request.args.get("filename", "").strip()
    if not filename:
        return jsonify({"error": "filename required"}), 400
    try:
        results = dbmod.get_connected_files(filename)
        return jsonify(results)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=FLASK_PORT, debug=False)
