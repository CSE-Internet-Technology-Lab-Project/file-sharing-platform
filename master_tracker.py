"""
Master Tracker — the control plane for the file-sharing cluster.

  python master_tracker.py

  Port 8000 — Flask HTTP API + static dashboard
  Port 8001 — TCP event listener (heartbeats, chunk events from Data Keepers)
"""

import os
import sys
import time
import uuid
import json
import socket
import hashlib
import threading

# ── Ensure project root is on sys.path ──
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, request, jsonify, send_from_directory

import db
from event_bus import bus
from load_balancer import pick_replica_pair, pick_replacement_node, resolve_download_plan
from shared.events import start_event_listener
from shared.wire import send_msg, recv_msg

# ── Flask app ──
app = Flask(__name__, static_folder="static")

# ── In-memory state ──
lookup_table: dict = {}          # node_id -> {status, host, port, active, disk_free_mb, last_seen}
lock = threading.Lock()


# ── Simple token-based auth ──
# Tokens are "<user_id>:<username>" base64-ish — good enough for a demo.

def _make_token(user_id: int, username: str) -> str:
    import base64
    return base64.b64encode(f"{user_id}:{username}".encode()).decode()


def _parse_token(token: str) -> int | None:
    """Return user_id from a Bearer token, or None."""
    import base64
    try:
        decoded = base64.b64decode(token).decode()
        user_id_str = decoded.split(":")[0]
        return int(user_id_str)
    except Exception:
        return None


def _auth_required():
    """Extract and validate the Authorization header. Returns (user_id, error_response)."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None, (jsonify({"error": "missing or invalid Authorization header"}), 401)
    token = auth[len("Bearer "):]
    user_id = _parse_token(token)
    if user_id is None:
        return None, (jsonify({"error": "invalid token"}), 401)
    if db.get_user_by_id(user_id) is None:
        return None, (jsonify({"error": "user not found"}), 401)
    return user_id, None


# ═══════════════════════════════════════════════════════════════════════════
# Event dispatch — called by the TCP event listener on port 8001
# ═══════════════════════════════════════════════════════════════════════════

def dispatch(event_type: str, payload: dict):
    if event_type == "node.heartbeat":
        with lock:
            lookup_table[payload["node_id"]] = {
                **payload,
                "status": "up",
                "last_seen": time.time(),
            }

    elif event_type == "chunk.stored":
        db.mark_chunk_stored(
            payload["file_id"], payload["chunk_idx"],
            payload["node_id"], role="primary", checksum=payload.get("checksum"),
        )
        received = db.increment_upload_session(payload["file_id"])
        _maybe_complete_upload(payload["file_id"], received)

    elif event_type == "chunk.replicated":
        db.mark_chunk_stored(
            payload["file_id"], payload["chunk_idx"],
            payload["node_id"], role="secondary",
        )
        db.recompute_file_status(payload["file_id"])

    elif event_type == "chunk.replicate_failed":
        db.mark_chunk_under_replicated(payload["file_id"], payload["chunk_idx"])

    # Fan out to all bus subscribers (event logging, etc.)
    bus.publish(event_type, payload)


def _maybe_complete_upload(file_id: str, chunks_received: int):
    """Check if all chunks are in; if so, mark file available."""
    session = db.get_upload_session(file_id)
    if not session:
        return
    if chunks_received >= session["total_chunks"]:
        db.complete_upload_session(file_id)
        db.update_file_status(file_id, "available")
        # Compute aggregate checksum from individual chunk checksums
        chunks = db.get_chunks(file_id)
        if chunks and all(c["checksum"] for c in chunks):
            h = hashlib.sha256()
            for c in sorted(chunks, key=lambda c: c["chunk_index"]):
                h.update(c["checksum"].encode())
            db.update_file_checksum(file_id, h.hexdigest())
        bus.publish("file.upload.completed", {"file_id": file_id})


# ═══════════════════════════════════════════════════════════════════════════
# Failure-sweep thread — runs every 1s, marks nodes down after 3s no heartbeat
# ═══════════════════════════════════════════════════════════════════════════

def _failure_sweep():
    while True:
        time.sleep(1)
        now = time.time()
        with lock:
            for node_id, info in lookup_table.items():
                if info["status"] == "up" and now - info["last_seen"] > 3:
                    info["status"] = "down"
                    bus.publish("node.down", {"node_id": node_id})
                    threading.Thread(
                        target=_trigger_rereplication,
                        args=(node_id,),
                        daemon=True,
                    ).start()


def _trigger_rereplication(dead_node_id: str):
    """
    For every chunk that had a copy on the dead node, find the surviving
    replica and tell it to push the chunk to a new healthy node.
    """
    affected_chunks = db.get_chunks_on_node(dead_node_id)
    # Remove dead node's locations from DB
    affected_file_ids = db.mark_node_chunks_lost(dead_node_id)

    for chunk_info in affected_chunks:
        chunk_id = chunk_info["chunk_id"]
        file_id  = chunk_info["file_id"]
        idx      = chunk_info["chunk_index"]

        # Find surviving replica
        surviving_locs = db.get_locations(chunk_id)
        if not surviving_locs:
            continue  # no surviving replica — chunk is lost

        surviving = surviving_locs[0]
        surviving_node_id = surviving["node_id"]

        with lock:
            surviving_info = lookup_table.get(surviving_node_id)
            if not surviving_info or surviving_info["status"] != "up":
                continue  # surviving node also down — can't re-replicate

        # Pick a new target
        try:
            with lock:
                target = pick_replacement_node(lookup_table, exclude=[dead_node_id, surviving_node_id])
        except RuntimeError:
            continue

        # Tell the surviving keeper to push the chunk to the new target
        try:
            with socket.create_connection(
                (surviving_info["host"], surviving_info["port"]), timeout=5
            ) as s:
                send_msg(s, {
                    "op": "RE_REPLICATE",
                    "file_id": file_id,
                    "chunk_idx": idx,
                    "target": {
                        "host": target["host"],
                        "port": target["port"],
                        "node_id": target["node_id"],
                    },
                })
                s.recv(1)  # ack
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# Flask routes
# ═══════════════════════════════════════════════════════════════════════════

# ── Auth ──

@app.route("/auth/register", methods=["POST"])
def register():
    data = request.get_json(force=True)
    username = data.get("username", "").strip()
    password = data.get("password", "")
    if not username or not password:
        return jsonify({"error": "username and password required"}), 400
    pw_hash = hashlib.sha256(password.encode()).hexdigest()
    try:
        db.create_user(username, pw_hash)
    except Exception:
        return jsonify({"error": "username already taken"}), 409
    return jsonify({"ok": True})


@app.route("/auth/login", methods=["POST"])
def login():
    data = request.get_json(force=True)
    username = data.get("username", "").strip()
    password = data.get("password", "")
    user = db.get_user_by_username(username)
    if not user:
        return jsonify({"error": "invalid credentials"}), 401
    pw_hash = hashlib.sha256(password.encode()).hexdigest()
    if user["password_hash"] != pw_hash:
        return jsonify({"error": "invalid credentials"}), 401
    token = _make_token(user["id"], user["username"])
    return jsonify({"token": token})


@app.route("/users", methods=["GET"])
def list_all_users():
    user_id, err = _auth_required()
    if err:
        return err
    return jsonify(db.list_users())


# ── Upload ──

@app.route("/files/upload/init", methods=["POST"])
def upload_init():
    user_id, err = _auth_required()
    if err:
        return err

    data = request.get_json(force=True)
    filename   = data.get("filename", "untitled")
    size_bytes = data["size_bytes"]
    chunk_size = data.get("chunk_size", 8 * 1024 * 1024)

    total_chunks = (size_bytes + chunk_size - 1) // chunk_size
    file_id = str(uuid.uuid4())
    resume_token = str(uuid.uuid4())

    # Create file record
    db.create_file(file_id, user_id, filename, size_bytes, chunk_size, total_chunks)

    # Build chunk plan — one (primary, secondary) pair per chunk
    chunk_plan = {}
    for idx in range(total_chunks):
        chunk_id = str(uuid.uuid4())
        remaining = min(chunk_size, size_bytes - idx * chunk_size)
        db.create_chunk(chunk_id, file_id, idx, remaining)

        with lock:
            primary, secondary = pick_replica_pair(lookup_table)

        db.create_chunk_location(chunk_id, primary["node_id"], "primary")
        db.create_chunk_location(chunk_id, secondary["node_id"], "secondary")

        chunk_plan[str(idx)] = {
            "primary": {
                "node_id": primary["node_id"],
                "host": primary["host"],
                "port": primary["port"],
            },
            "secondary": {
                "node_id": secondary["node_id"],
                "host": secondary["host"],
                "port": secondary["port"],
            },
        }

    # Create upload session
    db.create_upload_session(resume_token, file_id, user_id, total_chunks)

    return jsonify({
        "file_id": file_id,
        "resume_token": resume_token,
        "chunk_plan": chunk_plan,
    })


@app.route("/files/upload/<file_id>/status", methods=["GET"])
def upload_status(file_id):
    missing = db.get_missing_chunks(file_id)
    # Rebuild chunk_plan for missing chunks
    chunk_plan = {}
    for idx in missing:
        chunk_id = db.get_chunk_id(file_id, idx)
        if not chunk_id:
            continue
        with lock:
            primary, secondary = pick_replica_pair(lookup_table)
        db.create_chunk_location(chunk_id, primary["node_id"], "primary")
        db.create_chunk_location(chunk_id, secondary["node_id"], "secondary")
        chunk_plan[str(idx)] = {
            "primary": {
                "node_id": primary["node_id"],
                "host": primary["host"],
                "port": primary["port"],
            },
            "secondary": {
                "node_id": secondary["node_id"],
                "host": secondary["host"],
                "port": secondary["port"],
            },
        }

    return jsonify({
        "missing_chunk_indices": missing,
        "chunk_plan_for_missing": chunk_plan,
    })


# ── File versions ──

@app.route("/files/<file_id>/versions", methods=["POST"])
def create_version(file_id):
    user_id, err = _auth_required()
    if err:
        return err
    if not db.check_permission(file_id, user_id, "editor"):
        return jsonify({"error": "forbidden"}), 403

    f = db.get_file(file_id)
    if not f:
        return jsonify({"error": "file not found"}), 404

    data = request.get_json(force=True)
    size_bytes = data.get("size_bytes", f["size_bytes"])
    chunk_size = data.get("chunk_size", f["chunk_size"])
    total_chunks = (size_bytes + chunk_size - 1) // chunk_size
    new_version = f["current_version"] + 1
    resume_token = str(uuid.uuid4())

    db.create_file_version(file_id, new_version, size_bytes)
    db.update_file_status(file_id, "uploading")

    chunk_plan = {}
    for idx in range(total_chunks):
        chunk_id = str(uuid.uuid4())
        remaining = min(chunk_size, size_bytes - idx * chunk_size)
        db.create_chunk(chunk_id, file_id, idx, remaining)
        with lock:
            primary, secondary = pick_replica_pair(lookup_table)
        db.create_chunk_location(chunk_id, primary["node_id"], "primary")
        db.create_chunk_location(chunk_id, secondary["node_id"], "secondary")
        chunk_plan[str(idx)] = {
            "primary": {
                "node_id": primary["node_id"],
                "host": primary["host"],
                "port": primary["port"],
            },
            "secondary": {
                "node_id": secondary["node_id"],
                "host": secondary["host"],
                "port": secondary["port"],
            },
        }

    db.create_upload_session(resume_token, file_id, user_id, total_chunks)

    return jsonify({
        "file_id": file_id,
        "resume_token": resume_token,
        "version": new_version,
        "chunk_plan": chunk_plan,
    })


# ── File listing / detail / download ──

@app.route("/files", methods=["GET"])
def list_files():
    user_id, err = _auth_required()
    if err:
        return err
    files = db.list_files_for_user(user_id)
    return jsonify(files)


@app.route("/files/<file_id>", methods=["GET"])
def file_detail(file_id):
    user_id, err = _auth_required()
    if err:
        return err
    f = db.get_file(file_id)
    if not f:
        return jsonify({"error": "not found"}), 404
    if not db.check_permission(file_id, user_id, "viewer"):
        return jsonify({"error": "forbidden"}), 403
    chunks = db.get_chunks(file_id)
    total_replicas = sum(len(db.get_locations(c["id"])) for c in chunks)
    return jsonify({
        **f,
        "chunk_count": len(chunks),
        "total_replicas": total_replicas,
    })


@app.route("/files/<file_id>/download", methods=["GET"])
def download_plan(file_id):
    user_id, err = _auth_required()
    if err:
        return err
    if not db.check_permission(file_id, user_id, "viewer"):
        return jsonify({"error": "forbidden"}), 403
    f = db.get_file(file_id)
    if not f:
        return jsonify({"error": "not found"}), 404

    try:
        with lock:
            plan = resolve_download_plan(file_id, lookup_table, db)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503

    return jsonify({
        "chunks": plan,
        "file_checksum": f.get("checksum"),
    })


# ── ACL ──

@app.route("/files/<file_id>/share", methods=["POST"])
def share_file_by_username(file_id):
    user_id, err = _auth_required()
    if err:
        return err
    if not db.check_permission(file_id, user_id, "owner"):
        return jsonify({"error": "forbidden — owner only"}), 403

    data = request.get_json(force=True)
    target_username = (data.get("username") or "").strip()
    permission = data.get("permission", "viewer")
    if permission not in ("owner", "editor", "viewer"):
        return jsonify({"error": "invalid permission"}), 400
    if not target_username:
        return jsonify({"error": "username is required"}), 400

    target_user = db.get_user_by_username(target_username)
    if not target_user:
        return jsonify({"error": f"user '{target_username}' not found"}), 404

    db.set_acl(file_id, target_user["id"], permission)
    return jsonify({
        "ok": True,
        "shared_with": target_user["username"],
        "permission": permission,
    })


@app.route("/files/<file_id>/acl", methods=["PUT"])
def set_file_acl(file_id):
    user_id, err = _auth_required()
    if err:
        return err
    if not db.check_permission(file_id, user_id, "owner"):
        return jsonify({"error": "forbidden — owner only"}), 403
    data = request.get_json(force=True)
    target_user = data.get("user_id")
    permission  = data.get("permission")
    if permission not in ("owner", "editor", "viewer"):
        return jsonify({"error": "invalid permission"}), 400
    db.set_acl(file_id, target_user, permission)
    return jsonify({"ok": True})


# ── Delete ──

@app.route("/files/<file_id>", methods=["DELETE"])
def delete_file_route(file_id):
    user_id, err = _auth_required()
    if err:
        return err
    if not db.check_permission(file_id, user_id, "owner"):
        return jsonify({"error": "forbidden — owner only"}), 403
    db.delete_file(file_id)
    return jsonify({"ok": True})


# ── Dashboard API ──

@app.route("/api/status", methods=["GET"])
def api_status():
    with lock:
        nodes = [
            {
                "node_id": info["node_id"],
                "status": info["status"],
                "active": info.get("active", 0),
                "disk_free_mb": info.get("disk_free_mb", 0),
            }
            for info in lookup_table.values()
        ]
    files = db.get_file_status_counts()
    recent = bus.recent(20)
    return jsonify({
        "nodes": nodes,
        "files": files,
        "recent_events": recent,
    })


# ── Admin: kill / revive ──

@app.route("/admin/nodes/<node_id>/kill", methods=["POST"])
def kill_node(node_id):
    with lock:
        info = lookup_table.get(node_id)
    if not info:
        return jsonify({"error": "unknown node"}), 404
    try:
        with socket.create_connection((info["host"], info["port"]), timeout=3) as s:
            send_msg(s, {"op": "ADMIN", "action": "pause"})
            s.recv(1)
    except OSError as e:
        return jsonify({"error": f"could not reach node: {e}"}), 502
    return jsonify({"ok": True})


@app.route("/admin/nodes/<node_id>/revive", methods=["POST"])
def revive_node(node_id):
    with lock:
        info = lookup_table.get(node_id)
    if not info:
        return jsonify({"error": "unknown node"}), 404
    try:
        with socket.create_connection((info["host"], info["port"]), timeout=3) as s:
            send_msg(s, {"op": "ADMIN", "action": "resume"})
            s.recv(1)
        # Mark as up immediately so the dashboard doesn't lag
        with lock:
            lookup_table[node_id]["status"] = "up"
            lookup_table[node_id]["last_seen"] = time.time()
    except OSError as e:
        return jsonify({"error": f"could not reach node: {e}"}), 502
    return jsonify({"ok": True})


# ── Static files (dashboard) ──

@app.route("/")
def serve_index():
    return send_from_directory("static", "index.html")


@app.route("/<path:filename>")
def serve_static(filename):
    return send_from_directory("static", filename)


# ═══════════════════════════════════════════════════════════════════════════
# Startup
# ═══════════════════════════════════════════════════════════════════════════

def main():
    # 1. Init database
    db.init_schema()
    print("[tracker] database initialised")

    # 2. Start TCP event listener on port 8001
    try:
        start_event_listener(8001, dispatch)
        print("[tracker] event listener on port 8001")
    except OSError as exc:
        print(f"[tracker] failed to bind event listener on port 8001: {exc}")
        print("[tracker] another tracker instance may still be running. Stop it and retry.")
        raise SystemExit(1)

    # 3. Start failure-sweep background thread
    threading.Thread(target=_failure_sweep, daemon=True).start()
    print("[tracker] failure-sweep thread started")

    # 4. Subscribe the event logger to the bus
    bus.subscribe(db.log_event)

    # 5. Start Flask on port 8000
    print("[tracker] starting Flask on port 8000")
    app.run(host="0.0.0.0", port=8000, debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
