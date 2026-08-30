"""
Data Keeper — multithreaded chunk-storage server.

Usage:  python data_keeper.py <node_id> <port>
Example: python data_keeper.py keeper1 9001

Each Data Keeper:
  - Accepts UPLOAD / DOWNLOAD / REPLICATE / ADMIN / RE_REPLICATE commands
  - Sends heartbeats to the Master Tracker every second
  - After a successful primary UPLOAD, replicates to the secondary node
    indicated in the request header (the client passes it through from the
    Tracker's chunk_plan).
"""

import sys
import os
import socket
import threading
import time
import hashlib
import shutil
from concurrent.futures import ThreadPoolExecutor

# ── Ensure the project root is on sys.path so `shared.*` resolves ──
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared.wire import send_msg, recv_msg, _recv_exact
from shared.events import send_event

# ── CLI arguments ──
NODE_ID = sys.argv[1]                       # e.g. "keeper1"
PORT    = int(sys.argv[2])                  # e.g. 9001
TRACKER_HOST, TRACKER_EVENT_PORT = "localhost", 8001
STORAGE_DIR = os.path.join("data", NODE_ID)
os.makedirs(STORAGE_DIR, exist_ok=True)

paused = threading.Event()          # flipped by ADMIN pause/resume — simulates node failure


# ── Helpers ──

def chunk_path(file_id: str, idx: int) -> str:
    return os.path.join(STORAGE_DIR, f"{file_id}_chunk{idx}")


def _disk_free_mb(path: str) -> int:
    return shutil.disk_usage(path).free // (1024 * 1024)


# ── Replication ──

def replicate_to(secondary_host: str, secondary_port: int,
                 secondary_node_id: str, file_id: str, idx: int):
    """
    Push a local chunk to the secondary keeper.
    Called asynchronously right after a primary UPLOAD succeeds.
    """
    path = chunk_path(file_id, idx)
    try:
        with open(path, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        send_event(TRACKER_HOST, TRACKER_EVENT_PORT, "chunk.replicate_failed",
                   {"file_id": file_id, "chunk_idx": idx})
        return

    try:
        with socket.create_connection((secondary_host, secondary_port), timeout=10) as s:
            send_msg(s, {
                "op": "REPLICATE",
                "file_id": file_id,
                "chunk_idx": idx,
                "size": len(data),
            })
            s.sendall(data)
            ack = s.recv(1)
        if ack == b"\x01":
            send_event(TRACKER_HOST, TRACKER_EVENT_PORT, "chunk.replicated", {
                "file_id": file_id,
                "chunk_idx": idx,
                "node_id": secondary_node_id,
            })
        else:
            send_event(TRACKER_HOST, TRACKER_EVENT_PORT, "chunk.replicate_failed", {
                "file_id": file_id,
                "chunk_idx": idx,
            })
    except OSError:
        send_event(TRACKER_HOST, TRACKER_EVENT_PORT, "chunk.replicate_failed", {
            "file_id": file_id,
            "chunk_idx": idx,
        })


# ── Connection handler ──

replication_pool = ThreadPoolExecutor(max_workers=8)


def handle_conn(conn: socket.socket):
    if paused.is_set():
        conn.close()
        return
    try:
        header = recv_msg(conn)
        op = header["op"]

        if op == "UPLOAD":
            file_id = header["file_id"]
            idx     = header["chunk_idx"]
            size    = header["size"]
            tmp = chunk_path(file_id, idx) + ".tmp"
            hasher = hashlib.sha256()
            with open(tmp, "wb") as f:
                remaining = size
                while remaining > 0:
                    data = conn.recv(min(65536, remaining))
                    if not data:
                        raise ConnectionError("client disconnected during upload")
                    f.write(data)
                    hasher.update(data)
                    remaining -= len(data)
            os.replace(tmp, chunk_path(file_id, idx))   # atomic commit
            conn.sendall(b"\x01")
            checksum = hasher.hexdigest()

            # Tell the tracker this chunk is stored
            send_event(TRACKER_HOST, TRACKER_EVENT_PORT, "chunk.stored", {
                "file_id": file_id,
                "chunk_idx": idx,
                "node_id": NODE_ID,
                "checksum": checksum,
            })

            # Kick off replication to secondary (if specified)
            secondary = header.get("secondary")
            if secondary and secondary.get("node_id") != NODE_ID:
                replication_pool.submit(
                    replicate_to,
                    secondary["host"],
                    secondary["port"],
                    secondary["node_id"],
                    file_id,
                    idx,
                )

        elif op == "DOWNLOAD":
            file_id = header["file_id"]
            idx     = header["chunk_idx"]
            path = chunk_path(file_id, idx)
            if not os.path.exists(path):
                send_msg(conn, {"error": "not_found"})
                return
            with open(path, "rb") as f:
                data = f.read()
            send_msg(conn, {
                "size": len(data),
                "checksum": hashlib.sha256(data).hexdigest(),
            })
            conn.sendall(data)

        elif op == "ADMIN":
            if header["action"] == "pause":
                paused.set()
            elif header["action"] == "resume":
                paused.clear()
            conn.sendall(b"\x01")

        elif op == "REPLICATE":
            # Keeper-to-Keeper: secondary receiving a copy
            file_id = header["file_id"]
            idx     = header["chunk_idx"]
            size    = header["size"]
            tmp = chunk_path(file_id, idx) + ".tmp"
            with open(tmp, "wb") as f:
                remaining = size
                while remaining > 0:
                    data = conn.recv(min(65536, remaining))
                    if not data:
                        raise ConnectionError("peer disconnected during replication")
                    f.write(data)
                    remaining -= len(data)
            os.replace(tmp, chunk_path(file_id, idx))
            conn.sendall(b"\x01")

        elif op == "RE_REPLICATE":
            # Tracker-initiated: read local chunk and push to a new target
            file_id = header["file_id"]
            idx     = header["chunk_idx"]
            target  = header["target"]        # {"host", "port", "node_id"}
            replication_pool.submit(
                replicate_to,
                target["host"],
                target["port"],
                target["node_id"],
                file_id,
                idx,
            )
            conn.sendall(b"\x01")

    except Exception:
        try:
            conn.sendall(b"\x00")
        except Exception:
            pass
    finally:
        conn.close()


# ── Heartbeat ──

def heartbeat_loop():
    while True:
        if not paused.is_set():
            send_event(TRACKER_HOST, TRACKER_EVENT_PORT, "node.heartbeat", {
                "node_id": NODE_ID,
                "host": "localhost",
                "port": PORT,
                "active": threading.active_count(),
                "disk_free_mb": _disk_free_mb(STORAGE_DIR),
            })
        time.sleep(1)


# ── Main ──

def main():
    pool = ThreadPoolExecutor(max_workers=32)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(64)
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    print(f"[{NODE_ID}] listening on port {PORT}")
    while True:
        conn, addr = srv.accept()
        pool.submit(handle_conn, conn)


if __name__ == "__main__":
    main()
