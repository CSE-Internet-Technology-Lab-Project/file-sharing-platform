"""
Benchmark suite for the file-sharing platform.

Usage:  python benchmark/benchmark.py
  (requires at least one Data Keeper + Master Tracker running)

Produces:
  benchmark/results/throughput_vs_concurrency.png
  benchmark/results/throughput_vs_chunksize.png
  (prints failure-detection and re-replication latency numbers)
"""

import os
import sys
import time
import threading
import socket
import requests

# ── Ensure project root is on sys.path ──
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import matplotlib
matplotlib.use("Agg")  # headless backend
import matplotlib.pyplot as plt

from shared.wire import send_msg


# ═══════════════════════════════════════════════════════════════════════════
# Throughput helpers
# ═══════════════════════════════════════════════════════════════════════════

def timed_upload(host, port, file_id, idx, size_bytes):
    """Upload random data to a Data Keeper and return elapsed seconds."""
    data = os.urandom(size_bytes)
    start = time.time()
    with socket.create_connection((host, port), timeout=60) as s:
        send_msg(s, {
            "op": "UPLOAD",
            "file_id": file_id,
            "chunk_idx": idx,
            "size": len(data),
            # Benchmark: skip replication by pointing secondary to self
            "secondary": {"host": host, "port": port, "node_id": "self"},
        })
        s.sendall(data)
        s.recv(1)
    return time.time() - start


# ═══════════════════════════════════════════════════════════════════════════
# Sweep: concurrency levels
# ═══════════════════════════════════════════════════════════════════════════

def sweep_concurrency(host="localhost", port=9001, size_mb=10,
                      levels=(1, 4, 8, 16, 32)):
    """Upload N chunks in parallel for each concurrency level, measure throughput."""
    print("\n═══ Throughput vs Concurrency ═══")
    results = []
    for n in levels:
        errors = []
        start = time.time()
        threads = []
        for i in range(n):
            t = threading.Thread(
                target=_safe_upload,
                args=(host, port, f"bench_c{n}_{i}", 0, size_mb * 1024 * 1024, errors),
            )
            threads.append(t)
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.time() - start
        if errors:
            print(f"  concurrency={n}: ERRORS — {errors[:3]}")
            continue
        throughput = (n * size_mb) / elapsed
        results.append((n, throughput))
        print(f"  concurrency={n:>3}: {throughput:>8.1f} MB/s  ({elapsed:.2f}s)")

    if results:
        xs, ys = zip(*results)
        plt.figure(figsize=(8, 5))
        plt.plot(xs, ys, marker='o', linewidth=2, color='#6366f1')
        plt.fill_between(xs, ys, alpha=0.1, color='#6366f1')
        plt.xlabel("Concurrent Uploads")
        plt.ylabel("Throughput (MB/s)")
        plt.title("Throughput vs Concurrency")
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig("benchmark/results/throughput_vs_concurrency.png", dpi=150)
        print("  → saved benchmark/results/throughput_vs_concurrency.png")


def _safe_upload(host, port, file_id, idx, size, errors_list):
    try:
        timed_upload(host, port, file_id, idx, size)
    except Exception as e:
        errors_list.append(str(e))


# ═══════════════════════════════════════════════════════════════════════════
# Sweep: chunk sizes
# ═══════════════════════════════════════════════════════════════════════════

def sweep_chunk_size(host="localhost", port=9001, total_mb=64,
                     sizes_mb=(1, 2, 4, 8, 16)):
    """Upload a fixed total volume with different chunk sizes."""
    print("\n═══ Throughput vs Chunk Size ═══")
    results = []
    for cs in sizes_mb:
        n_chunks = total_mb // cs
        start = time.time()
        for i in range(n_chunks):
            timed_upload(host, port, f"bench_cs{cs}", i, cs * 1024 * 1024)
        elapsed = time.time() - start
        throughput = total_mb / elapsed
        results.append((cs, throughput))
        print(f"  chunk_size={cs:>3} MB: {throughput:>8.1f} MB/s  ({elapsed:.2f}s)")

    if results:
        xs, ys = zip(*results)
        plt.figure(figsize=(8, 5))
        plt.plot(xs, ys, marker='s', linewidth=2, color='#34d399')
        plt.fill_between(xs, ys, alpha=0.1, color='#34d399')
        plt.xlabel("Chunk Size (MB)")
        plt.ylabel("Throughput (MB/s)")
        plt.title("Throughput vs Chunk Size")
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig("benchmark/results/throughput_vs_chunksize.png", dpi=150)
        print("  → saved benchmark/results/throughput_vs_chunksize.png")


# ═══════════════════════════════════════════════════════════════════════════
# Failure injection timing
# ═══════════════════════════════════════════════════════════════════════════

def measure_failure_latency(tracker="http://localhost:8000",
                            node_id="keeper2", file_id=None, token=None):
    """
    Kill a node, measure:
      1. Detection latency  (time until /api/status shows the node as 'down')
      2. Re-replication latency (time until file status returns to 'available')
    """
    auth_headers = {"Authorization": f"Bearer {token}"} if token else {}
    print(f"\n═══ Failure Injection — {node_id} ═══")

    # Kill the node
    t0 = time.time()
    resp = requests.post(f"{tracker}/admin/nodes/{node_id}/kill")
    if resp.status_code != 200:
        print(f"  could not kill {node_id}: {resp.text}")
        return

    # Poll until node shows 'down'
    detection_latency = None
    for _ in range(30):
        time.sleep(0.5)
        status = requests.get(f"{tracker}/api/status").json()
        node = next((n for n in status["nodes"] if n["node_id"] == node_id), None)
        if node and node["status"] == "down":
            detection_latency = time.time() - t0
            break

    if detection_latency is None:
        print("  ⚠ detection timed out (15s)")
    else:
        print(f"  detection latency: {detection_latency:.2f}s")

    # If a file_id was given, poll until it returns to 'available'
    if file_id:
        t1 = time.time()
        rereplication_latency = None
        for _ in range(60):
            time.sleep(0.5)
            try:
                fdata = requests.get(f"{tracker}/files/{file_id}", headers=auth_headers).json()
                if fdata.get("status") == "available":
                    rereplication_latency = time.time() - t1
                    break
            except Exception:
                pass
        if rereplication_latency is None:
            print("  ⚠ re-replication timed out (30s)")
        else:
            print(f"  re-replication latency: {rereplication_latency:.2f}s")

    # Revive the node
    requests.post(f"{tracker}/admin/nodes/{node_id}/revive")
    print(f"  {node_id} revived")


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    os.makedirs("benchmark/results", exist_ok=True)

    # Check that at least one keeper is reachable
    try:
        with socket.create_connection(("localhost", 9001), timeout=2):
            pass
    except OSError:
        print("ERROR: cannot reach Data Keeper on port 9001. Start it first.")
        sys.exit(1)

    sweep_concurrency()
    sweep_chunk_size()
    measure_failure_latency()
    print("\n✓ benchmark complete")
