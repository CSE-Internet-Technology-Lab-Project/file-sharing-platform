"""
SHA-256 checksum utilities for files and in-memory byte buffers.
"""

import hashlib


def sha256_file(path: str, chunk_size: int = 1 << 20) -> str:
    """Return the hex-digest SHA-256 of the file at *path*."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            data = f.read(chunk_size)
            if not data:
                break
            h.update(data)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """Return the hex-digest SHA-256 of an in-memory byte string."""
    return hashlib.sha256(data).hexdigest()


# Alias used in the client download path
sha256_chunk = sha256_bytes
