"""
Load balancer — node selection logic for uploads, downloads, and re-replication.

All functions take the live `lookup_table` dict (node_id → info) maintained by
the Master Tracker so they always reflect current cluster state.
"""


def pick_replica_pair(lookup_table: dict) -> tuple[dict, dict]:
    """
    Choose the two least-loaded healthy nodes to serve as primary and secondary
    for a new chunk upload.  Returns (primary_info, secondary_info) dicts that
    include node_id, host, port.
    """
    healthy = sorted(
        (n for n in lookup_table.values() if n.get("status") == "up"),
        key=lambda n: n.get("active", 0),
    )
    if len(healthy) < 2:
        raise RuntimeError("need at least 2 healthy nodes for replication")
    return healthy[0], healthy[1]


def pick_replacement_node(lookup_table: dict, exclude: list[str]) -> dict:
    """
    Choose the least-loaded healthy node that is NOT in *exclude*.
    Used during re-replication after a node dies.
    """
    healthy = sorted(
        (n for n in lookup_table.values()
         if n.get("status") == "up" and n.get("node_id") not in exclude),
        key=lambda n: n.get("active", 0),
    )
    if not healthy:
        raise RuntimeError("no healthy node available for re-replication")
    return healthy[0]


def resolve_download_plan(file_id: str, lookup_table: dict, db) -> list[dict]:
    """
    Build a per-chunk download plan: for each chunk of *file_id*, pick the
    healthiest node that has a stored copy.
    """
    plan = []
    for chunk in db.get_chunks(file_id):
        candidates = [
            loc for loc in db.get_locations(chunk["id"])
            if lookup_table.get(loc["node_id"], {}).get("status") == "up"
        ]
        if not candidates:
            raise RuntimeError(
                f"chunk {chunk['chunk_index']} unavailable — all replicas down"
            )
        best = min(candidates, key=lambda loc: lookup_table[loc["node_id"]].get("active", 0))
        node = lookup_table[best["node_id"]]
        plan.append({
            "chunk_idx": chunk["chunk_index"],
            "node_id": node["node_id"],
            "host": node["host"],
            "port": node["port"],
            "checksum": chunk["checksum"],
        })
    return plan
