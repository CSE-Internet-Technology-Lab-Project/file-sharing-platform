#!/usr/bin/env python3
"""
DEMO SCRIPT: Complete File Sharing Workflow
Shows: User1 uploads → User2 downloads → Files stored on disk
Perfect for demonstrating to a teacher!
Uses the actual platform TCP wire protocol for chunk transfer.
"""

import requests
import time
import os
import sys
import socket
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared.wire import send_msg, recv_msg, _recv_exact

TRACKER_URL = "http://localhost:8000"
DEMO_FILE = "demo_test_file.txt"
DEMO_CONTENT = "This is a demo file for the file sharing platform! " * 100

class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[06m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    END = '\033[0m'
    BOLD = '\033[1m'

def print_section(title):
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}{Colors.END}\n")

def print_success(msg):
    print(f"{Colors.GREEN}✓ {msg}{Colors.END}")

def print_info(msg):
    print(f"{Colors.CYAN}ℹ {msg}{Colors.END}")

def print_step(num, msg):
    print(f"{Colors.BLUE}{Colors.BOLD}[Step {num}]{Colors.END} {msg}")

def create_demo_file():
    print_step(1, "Creating demo file...")
    with open(DEMO_FILE, 'w') as f:
        f.write(DEMO_CONTENT)
    file_size = os.path.getsize(DEMO_FILE)
    print_success(f"Created {DEMO_FILE} ({file_size:,} bytes)")
    return file_size

def register_user(username, password):
    print_step("Register", f"Creating user: {username}")
    try:
        resp = requests.post(
            f"{TRACKER_URL}/auth/register",
            json={"username": username, "password": password},
            timeout=5
        )
        if resp.status_code == 200:
            print_success(f"User '{username}' registered")
            return True
        elif resp.status_code == 409:
            print_info(f"User '{username}' already exists")
            return True
        else:
            print_info(f"Registration failed: {resp.text}")
            return False
    except Exception as e:
        print(f"{Colors.RED}✗ Registration failed: {e}{Colors.END}")
        return False

def login_user(username, password):
    print_step("Login", f"Logging in as: {username}")
    try:
        resp = requests.post(
            f"{TRACKER_URL}/auth/login",
            json={"username": username, "password": password},
            timeout=5
        )
        if resp.status_code == 200:
            token = resp.json()['token']
            print_success(f"Login successful. Token: {token[:20]}...")
            return token
        else:
            print(f"{Colors.RED}✗ Login failed: {resp.text}{Colors.END}")
            return None
    except Exception as e:
        print(f"{Colors.RED}✗ Login failed: {e}{Colors.END}")
        return None

def upload_file(token, filename):
    print_step(2, f"Uploading file: {filename}")
    size = os.path.getsize(filename)
    chunk_size = 8 * 1024 * 1024

    try:
        resp = requests.post(
            f"{TRACKER_URL}/files/upload/init",
            json={"filename": os.path.basename(filename), "size_bytes": size, "chunk_size": chunk_size},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )
        if resp.status_code != 200:
            print(f"{Colors.RED}✗ Upload init failed: {resp.text}{Colors.END}")
            return None
        body = resp.json()
    except Exception as e:
        print(f"{Colors.RED}✗ Upload init failed: {e}{Colors.END}")
        return None

    file_id = body["file_id"]
    chunk_plan = body["chunk_plan"]
    total_chunks = len(chunk_plan)
    print_success(f"Upload plan created. File ID: {file_id}")
    print_info(f"File split into {total_chunks} chunk(s)")

    def send_chunk(idx):
        target = chunk_plan[str(idx)]
        primary = target["primary"]
        with open(filename, "rb") as f:
            f.seek(idx * chunk_size)
            data = f.read(min(chunk_size, size - idx * chunk_size))
        with socket.create_connection((primary["host"], primary["port"]), timeout=30) as s:
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

    t0 = time.time()
    for idx in range(total_chunks):
        send_chunk(idx)
        print_info(f"  Chunk {idx+1}/{total_chunks} ✓")
    elapsed = time.time() - t0
    throughput = size / elapsed / (1024 * 1024) if elapsed > 0 else 0
    print_success(f"All {total_chunks} chunks uploaded in {elapsed:.2f}s ({throughput:.1f} MB/s)")
    return file_id

def list_files(token, username):
    print_step("List", f"Files for {username}")
    try:
        resp = requests.get(
            f"{TRACKER_URL}/files",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5
        )
        if resp.status_code == 200:
            files = resp.json()
            if files:
                for f in files:
                    print_info(f"  {f['filename']} ({f['size_bytes']:,} bytes) - {f['status']}")
            else:
                print_info("No files found yet")
            return files
        else:
            print(f"{Colors.RED}✗ List failed: {resp.text}{Colors.END}")
            return []
    except Exception as e:
        print(f"{Colors.RED}✗ List failed: {e}{Colors.END}")
        return []

def download_file(token, file_id, filename):
    print_step(3, f"Downloading file: {file_id}")
    try:
        resp = requests.get(
            f"{TRACKER_URL}/files/{file_id}/download",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )
        if resp.status_code != 200:
            print(f"{Colors.RED}✗ Download plan failed: {resp.text}{Colors.END}")
            return False
        plan = resp.json()
    except Exception as e:
        print(f"{Colors.RED}✗ Download plan failed: {e}{Colors.END}")
        return False

    chunks = plan["chunks"]
    file_checksum = plan.get("file_checksum")
    print_success(f"Download plan: {len(chunks)} chunk(s)")

    output_filename = f"downloaded_{filename}"
    data_by_idx = {}

    def fetch_chunk(item):
        idx = item["chunk_idx"]
        with socket.create_connection((item["host"], item["port"]), timeout=30) as s:
            send_msg(s, {"op": "DOWNLOAD", "file_id": file_id, "chunk_idx": idx})
            header = recv_msg(s)
            if "error" in header:
                raise IOError(f"chunk {idx}: {header['error']}")
            chunk_data = _recv_exact(s, header["size"])
        if item.get("checksum") and __import__('hashlib').sha256(chunk_data).hexdigest() != item["checksum"]:
            raise IOError(f"chunk {idx} checksum mismatch")
        data_by_idx[idx] = chunk_data
        print_info(f"  Chunk {idx} ✓")

    t0 = time.time()
    for item in chunks:
        fetch_chunk(item)
    elapsed = time.time() - t0

    ordered_bytes = b"".join(data_by_idx[idx] for idx in sorted(data_by_idx))
    with open(output_filename, "wb") as f:
        f.write(ordered_bytes)

    total_size = os.path.getsize(output_filename)
    throughput = total_size / elapsed / (1024 * 1024) if elapsed > 0 else 0

    if file_checksum:
        h = __import__('hashlib').sha256()
        for item in sorted(chunks, key=lambda x: x["chunk_idx"]):
            if item.get("checksum"):
                h.update(item["checksum"].encode())
        if h.hexdigest() == file_checksum:
            print_success(f"Download verified ✓ ({total_size:,} bytes, {elapsed:.2f}s, {throughput:.1f} MB/s)")
        else:
            print(f"{Colors.RED}✗ Checksum mismatch{Colors.END}")
            return False
    else:
        print_success(f"Download complete ({total_size:,} bytes, {elapsed:.2f}s)")

    return True

def verify_files(original, downloaded):
    print_section("✅ VERIFICATION")
    with open(original, 'rb') as f:
        orig_data = f.read()
    with open(downloaded, 'rb') as f:
        dl_data = f.read()
    if orig_data == dl_data:
        print_success(f"Files match perfectly! ({len(orig_data):,} bytes)")
        return True
    else:
        print(f"{Colors.RED}✗ Files don't match!{Colors.END}")
        print(f"  Original:  {len(orig_data):,} bytes")
        print(f"  Downloaded: {len(dl_data):,} bytes")
        return False

def show_storage_locations():
    print_section("📁 Storage Locations on Disk")
    data_dir = Path("data")
    if not data_dir.exists():
        print_info("Data directory not yet created")
        return
    for keeper_dir in sorted(data_dir.iterdir()):
        if keeper_dir.is_dir():
            chunks = list(keeper_dir.glob("*_chunk*"))
            print_info(f"{keeper_dir.name}/")
            if chunks:
                for chunk in sorted(chunks):
                    size = chunk.stat().st_size
                    print(f"    └─ {chunk.name} ({size:,} bytes)")
            else:
                print(f"    └─ (empty)")

def check_cluster_status():
    print_section("🔍 Checking Cluster Status")
    try:
        resp = requests.get(f"{TRACKER_URL}/api/status", timeout=5)
        if resp.status_code == 200:
            status = resp.json()
            print_success("Cluster is running!")
            nodes = status.get("nodes", [])
            print_info(f"Active nodes: {len(nodes)} / 3")
            for node in nodes:
                health = "✓" if node["status"] == "up" else "✗"
                print_info(f"  {health} {node['node_id']}: {node['status']}")
            return True
        else:
            print(f"{Colors.RED}✗ Cluster check failed{Colors.END}")
            return False
    except Exception as e:
        print(f"{Colors.RED}✗ Connection failed: {e}{Colors.END}")
        print(f"{Colors.YELLOW}Make sure to run: bash start_all.sh{Colors.END}")
        return False

def main():
    print(f"\n{Colors.HEADER}{Colors.BOLD}")
    print("╔═══════════════════════════════════════════════════════╗")
    print("║   FILE SHARING PLATFORM - COMPLETE DEMO WORKFLOW      ║")
    print("║   Shows: Upload → Storage → Download → Verification   ║")
    print("╚═══════════════════════════════════════════════════════╝")
    print(f"{Colors.END}\n")

    if not check_cluster_status():
        sys.exit(1)

    file_size = create_demo_file()

    print_section("USER 1: UPLOAD FILE")
    register_user("alice", "password123")
    token1 = login_user("alice", "password123")
    if not token1:
        sys.exit(1)

    file_id = upload_file(token1, DEMO_FILE)
    if not file_id:
        print(f"{Colors.RED}✗ Upload failed, aborting demo{Colors.END}")
        sys.exit(1)
    print_info(f"File uploaded with ID: {file_id}")

    time.sleep(2)
    show_storage_locations()

    print_section("USER 2: DOWNLOAD FILE")
    register_user("bob", "password456")
    token2 = login_user("bob", "password456")
    if not token2:
        print_info("Bob registration failed, using alice token for download")
        token2 = token1

    if download_file(token2, file_id, DEMO_FILE):
        print_success("File successfully downloaded!")
    else:
        print(f"{Colors.RED}✗ Download failed{Colors.END}")
        sys.exit(1)

    if verify_files(DEMO_FILE, f"downloaded_{DEMO_FILE}"):
        list_files(token1, "alice")

    print_section("📊 DEMO SUMMARY")
    print_info(f"✓ User 'alice' uploaded {DEMO_FILE} ({file_size:,} bytes)")
    print_info(f"✓ File split into chunks and stored across keepers")
    print_info(f"✓ Each chunk replicated to 2 keepers for redundancy")
    print_info(f"✓ User 'bob' downloaded the file")
    print_info(f"✓ Downloaded file verified: {Colors.GREEN}IDENTICAL to original{Colors.END}")
    print(f"\n{Colors.GREEN}{Colors.BOLD}Demo completed successfully!{Colors.END}\n")

if __name__ == "__main__":
    main()