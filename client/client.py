"""
CLI client for the file-sharing platform.

Usage:
    python client/client.py register <username> <password>
    python client/client.py login    <username> <password>
    python client/client.py upload   <filepath>  --token <t>  [--parallelism 4] [--chunk-size 8388608]
    python client/client.py download <file_id> <dest_path> --token <t> [--parallelism 4]
    python client/client.py resume   <filepath> <file_id>  --token <t> [--parallelism 4] [--chunk-size 8388608]
"""

import argparse
import os
import sys
import socket
import json
import time
from concurrent.futures import ThreadPoolExecutor

import requests

# ── Ensure project root is on sys.path ──
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from shared.wire import send_msg, recv_msg, _recv_exact
from shared.checksums import sha256_file, sha256_bytes


# ═══════════════════════════════════════════════════════════════════════════
# Upload
# ═══════════════════════════════════════════════════════════════════════════

def upload(path: str, token: str, tracker: str = "http://localhost:8000",
           parallelism: int = 4, chunk_size: int = 8 * 1024 * 1024):
    """Split *path* into chunks and upload them in parallel."""
    size = os.path.getsize(path)
    total_chunks = (size + chunk_size - 1) // chunk_size

    # 1. Initialise upload with the tracker
    resp = requests.post(
        f"{tracker}/files/upload/init",
        json={
            "filename": os.path.basename(path),
            "size_bytes": size,
            "chunk_size": chunk_size,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    if resp.status_code != 200:
        print(f"upload init failed: {resp.text}")
        sys.exit(1)

    body = resp.json()
    file_id      = body["file_id"]
    resume_token = body["resume_token"]
    chunk_plan   = body["chunk_plan"]

    print(f"uploading {os.path.basename(path)} ({size} bytes, {total_chunks} chunks)")
    print(f"  file_id      = {file_id}")
    print(f"  resume_token = {resume_token}")

    # 2. Send chunks in parallel
    def send_one(idx):
        target = chunk_plan[str(idx)]
        with open(path, "rb") as f:
            f.seek(idx * chunk_size)
            data = f.read(chunk_size)
        with socket.create_connection(
            (target["primary"]["host"], target["primary"]["port"]), timeout=30
        ) as s:
            send_msg(s, {
                "op": "UPLOAD",
                "file_id": file_id,
                "chunk_idx": idx,
                "size": len(data),
                "secondary": target["secondary"],
            })
            s.sendall(data)
            ack = s.recv(1)
        if ack != b"\x01":
            raise IOError(f"chunk {idx} upload failed")
        print(f"  chunk {idx}/{total_chunks - 1} ✓")

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=parallelism) as pool:
        list(pool.map(send_one, range(total_chunks)))
    elapsed = time.time() - t0

    throughput = size / elapsed / (1024 * 1024) if elapsed > 0 else 0
    print(f"upload complete in {elapsed:.2f}s ({throughput:.1f} MB/s)")
    print(f"  file_id = {file_id}")
    return file_id


# ═══════════════════════════════════════════════════════════════════════════
# Resume upload
# ═══════════════════════════════════════════════════════════════════════════

def resume_upload(path: str, file_id: str, token: str,
                  tracker: str = "http://localhost:8000",
                  parallelism: int = 4, chunk_size: int = 8 * 1024 * 1024):
    """Resume an interrupted upload by re-sending only missing chunks."""
    resp = requests.get(
        f"{tracker}/files/upload/{file_id}/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    if resp.status_code != 200:
        print(f"status check failed: {resp.text}")
        sys.exit(1)

    body = resp.json()
    missing    = body["missing_chunk_indices"]
    chunk_plan = body["chunk_plan_for_missing"]

    if not missing:
        print("nothing to resume — all chunks already uploaded")
        return file_id

    print(f"resuming upload for {len(missing)} missing chunk(s)")

    def send_one(idx):
        target = chunk_plan[str(idx)]
        with open(path, "rb") as f:
            f.seek(idx * chunk_size)
            data = f.read(chunk_size)
        with socket.create_connection(
            (target["primary"]["host"], target["primary"]["port"]), timeout=30
        ) as s:
            send_msg(s, {
                "op": "UPLOAD",
                "file_id": file_id,
                "chunk_idx": idx,
                "size": len(data),
                "secondary": target["secondary"],
            })
            s.sendall(data)
            ack = s.recv(1)
        if ack != b"\x01":
            raise IOError(f"chunk {idx} upload failed")
        print(f"  chunk {idx} ✓")

    with ThreadPoolExecutor(max_workers=parallelism) as pool:
        list(pool.map(send_one, missing))

    print("resume complete")
    return file_id


# ═══════════════════════════════════════════════════════════════════════════
# Download
# ═══════════════════════════════════════════════════════════════════════════

def download(file_id: str, dest_path: str, token: str,
             tracker: str = "http://localhost:8000", parallelism: int = 4):
    """Download all chunks in parallel and reassemble the file."""
    resp = requests.get(
        f"{tracker}/files/{file_id}/download",
        headers={"Authorization": f"Bearer {token}"},
    )
    if resp.status_code != 200:
        print(f"download plan failed: {resp.text}")
        sys.exit(1)

    body = resp.json()
    chunk_items   = body["chunks"]
    file_checksum = body.get("file_checksum")

    print(f"downloading {len(chunk_items)} chunk(s) → {dest_path}")

    chunks: dict[int, bytes] = {}

    def fetch_one(item):
        with socket.create_connection(
            (item["host"], item["port"]), timeout=30
        ) as s:
            send_msg(s, {
                "op": "DOWNLOAD",
                "file_id": file_id,
                "chunk_idx": item["chunk_idx"],
            })
            header = recv_msg(s)
            if "error" in header:
                raise IOError(f"chunk {item['chunk_idx']}: {header['error']}")
            data = _recv_exact(s, header["size"])
        # Verify chunk checksum
        if item.get("checksum") and sha256_bytes(data) != item["checksum"]:
            raise IOError(f"chunk {item['chunk_idx']} corrupted (checksum mismatch)")
        chunks[item["chunk_idx"]] = data
        print(f"  chunk {item['chunk_idx']} ✓")

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=parallelism) as pool:
        list(pool.map(fetch_one, chunk_items))

    # Reassemble
    with open(dest_path, "wb") as out:
        for idx in sorted(chunks):
            out.write(chunks[idx])
    elapsed = time.time() - t0

    total_size = os.path.getsize(dest_path)
    throughput = total_size / elapsed / (1024 * 1024) if elapsed > 0 else 0

    # Verify whole-file composite checksum (hash of chunk hashes, matching tracker's approach)
    if file_checksum:
        import hashlib
        h = hashlib.sha256()
        for item in sorted(chunk_items, key=lambda x: x["chunk_idx"]):
            if item.get("checksum"):
                h.update(item["checksum"].encode())
        composite = h.hexdigest()
        if composite != file_checksum:
            print(f"WARNING: composite checksum mismatch (expected {file_checksum}, got {composite})")
        else:
            print(f"download verified ✓ ({total_size} bytes, {elapsed:.2f}s, {throughput:.1f} MB/s)")
    else:
        print(f"download complete ({total_size} bytes, {elapsed:.2f}s, {throughput:.1f} MB/s)")

    return dest_path


# ═══════════════════════════════════════════════════════════════════════════
# Auth helpers
# ═══════════════════════════════════════════════════════════════════════════

def register(username: str, password: str, tracker: str = "http://localhost:8000"):
    resp = requests.post(f"{tracker}/auth/register", json={"username": username, "password": password})
    if resp.status_code == 200:
        print(f"registered user '{username}'")
    else:
        print(f"registration failed: {resp.text}")


def login(username: str, password: str, tracker: str = "http://localhost:8000") -> str:
    resp = requests.post(f"{tracker}/auth/login", json={"username": username, "password": password})
    if resp.status_code == 200:
        token = resp.json()["token"]
        print(f"token: {token}")
        return token
    else:
        print(f"login failed: {resp.text}")
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════
# CLI entry point
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="File sharing platform CLI client")
    sub = parser.add_subparsers(dest="command", required=True)

    # register
    p_reg = sub.add_parser("register")
    p_reg.add_argument("username")
    p_reg.add_argument("password")

    # login
    p_login = sub.add_parser("login")
    p_login.add_argument("username")
    p_login.add_argument("password")

    # upload
    p_up = sub.add_parser("upload")
    p_up.add_argument("filepath")
    p_up.add_argument("--token", required=True)
    p_up.add_argument("--parallelism", type=int, default=4)
    p_up.add_argument("--chunk-size", type=int, default=8 * 1024 * 1024)

    # download
    p_dl = sub.add_parser("download")
    p_dl.add_argument("file_id")
    p_dl.add_argument("dest")
    p_dl.add_argument("--token", required=True)
    p_dl.add_argument("--parallelism", type=int, default=4)

    # resume
    p_res = sub.add_parser("resume")
    p_res.add_argument("filepath")
    p_res.add_argument("file_id")
    p_res.add_argument("--token", required=True)
    p_res.add_argument("--parallelism", type=int, default=4)
    p_res.add_argument("--chunk-size", type=int, default=8 * 1024 * 1024)

    args = parser.parse_args()

    if args.command == "register":
        register(args.username, args.password)
    elif args.command == "login":
        login(args.username, args.password)
    elif args.command == "upload":
        upload(args.filepath, args.token, parallelism=args.parallelism, chunk_size=args.chunk_size)
    elif args.command == "download":
        download(args.file_id, args.dest, args.token, parallelism=args.parallelism)
    elif args.command == "resume":
        resume_upload(args.filepath, args.file_id, args.token,
                      parallelism=args.parallelism, chunk_size=args.chunk_size)


if __name__ == "__main__":
    main()
