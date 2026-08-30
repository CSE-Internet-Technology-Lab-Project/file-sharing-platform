import hashlib
import os
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import streamlit as st

from shared.wire import _recv_exact, recv_msg, send_msg


DEFAULT_TRACKER = "http://localhost:8000"
DEFAULT_KEEPERS = [
    ("keeper1", 9001),
    ("keeper2", 9002),
    ("keeper3", 9003),
]


def _init_state():
    if "token" not in st.session_state:
        st.session_state.token = ""
    if "username" not in st.session_state:
        st.session_state.username = ""
    if "processes" not in st.session_state:
        st.session_state.processes = {}
    if "download_blob" not in st.session_state:
        st.session_state.download_blob = None
    if "download_name" not in st.session_state:
        st.session_state.download_name = "download.bin"


def _is_process_alive(proc: subprocess.Popen | None) -> bool:
    return proc is not None and proc.poll() is None


def _spawn(name: str, cmd: list[str]):
    existing = st.session_state.processes.get(name)
    if _is_process_alive(existing):
        return
    proc = subprocess.Popen(
        cmd,
        cwd=os.path.dirname(os.path.abspath(__file__)),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    st.session_state.processes[name] = proc


def _stop(name: str):
    proc = st.session_state.processes.get(name)
    if not _is_process_alive(proc):
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


def _tracker_status(tracker_url: str) -> tuple[bool, dict | None, str | None]:
    try:
        r = requests.get(f"{tracker_url}/api/status", timeout=2)
        if r.status_code != 200:
            return False, None, f"tracker returned {r.status_code}"
        return True, r.json(), None
    except Exception as e:
        return False, None, str(e)


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _safe_request(method: str, url: str, **kwargs) -> tuple[requests.Response | None, str | None]:
    try:
        return requests.request(method, url, **kwargs), None
    except requests.RequestException as e:
        return None, str(e)


def _upload_bytes(
    blob: bytes,
    filename: str,
    token: str,
    tracker_url: str,
    chunk_size: int,
    parallelism: int,
    progress_bar,
    progress_text,
) -> str:
    size = len(blob)
    total_chunks = (size + chunk_size - 1) // chunk_size

    init_resp, req_err = _safe_request(
        "POST",
        f"{tracker_url}/files/upload/init",
        json={
            "filename": filename,
            "size_bytes": size,
            "chunk_size": chunk_size,
        },
        headers=_auth_headers(token),
        timeout=15,
    )
    if req_err:
        raise RuntimeError(f"tracker request failed: {req_err}")
    if init_resp.status_code != 200:
        raise RuntimeError(f"upload init failed: {init_resp.text}")

    body = init_resp.json()
    file_id = body["file_id"]
    chunk_plan = body["chunk_plan"]

    def _send_one(idx: int):
        start = idx * chunk_size
        end = min(start + chunk_size, size)
        data = blob[start:end]
        target = chunk_plan[str(idx)]
        with socket.create_connection(
            (target["primary"]["host"], target["primary"]["port"]), timeout=30
        ) as s:
            send_msg(
                s,
                {
                    "op": "UPLOAD",
                    "file_id": file_id,
                    "chunk_idx": idx,
                    "size": len(data),
                    "secondary": target["secondary"],
                },
            )
            s.sendall(data)
            ack = s.recv(1)
            if ack != b"\x01":
                raise IOError(f"chunk {idx} upload failed")

    completed = 0
    with ThreadPoolExecutor(max_workers=parallelism) as pool:
        futures = [pool.submit(_send_one, idx) for idx in range(total_chunks)]
        for future in as_completed(futures):
            future.result()
            completed += 1
            ratio = completed / total_chunks if total_chunks else 1
            progress_bar.progress(ratio)
            progress_text.write(f"Uploaded {completed}/{total_chunks} chunks")

    progress_text.write("Upload complete")
    return file_id


def _download_file(file_id: str, token: str, tracker_url: str, parallelism: int) -> tuple[bytes, str]:
    resp, req_err = _safe_request(
        "GET",
        f"{tracker_url}/files/{file_id}/download",
        headers=_auth_headers(token),
        timeout=15,
    )
    if req_err:
        raise RuntimeError(f"tracker request failed: {req_err}")
    if resp.status_code != 200:
        raise RuntimeError(f"download plan failed: {resp.text}")

    plan = resp.json()
    chunks = plan["chunks"]
    expected_file_checksum = plan.get("file_checksum")

    data_by_idx: dict[int, bytes] = {}

    def _fetch_one(item: dict):
        with socket.create_connection((item["host"], item["port"]), timeout=30) as s:
            send_msg(
                s,
                {
                    "op": "DOWNLOAD",
                    "file_id": file_id,
                    "chunk_idx": item["chunk_idx"],
                },
            )
            header = recv_msg(s)
            if "error" in header:
                raise IOError(f"chunk {item['chunk_idx']}: {header['error']}")
            chunk_data = _recv_exact(s, header["size"])

        expected_chunk_checksum = item.get("checksum")
        if expected_chunk_checksum:
            got = hashlib.sha256(chunk_data).hexdigest()
            if got != expected_chunk_checksum:
                raise IOError(f"chunk {item['chunk_idx']} checksum mismatch")
        return item["chunk_idx"], chunk_data

    with ThreadPoolExecutor(max_workers=parallelism) as pool:
        futures = [pool.submit(_fetch_one, item) for item in chunks]
        for f in as_completed(futures):
            idx, b = f.result()
            data_by_idx[idx] = b

    ordered_bytes = b"".join(data_by_idx[idx] for idx in sorted(data_by_idx))

    if expected_file_checksum:
        h = hashlib.sha256()
        for item in sorted(chunks, key=lambda x: x["chunk_idx"]):
            if item.get("checksum"):
                h.update(item["checksum"].encode())
        if h.hexdigest() != expected_file_checksum:
            raise IOError("composite file checksum mismatch")

    return ordered_bytes, f"{file_id}.bin"


def main():
    st.set_page_config(page_title="File Sharing Platform", layout="wide")
    _init_state()
    st.title("File Sharing Platform")
    st.caption("End-to-end distributed file sharing with replication, failover, and live cluster control")

    with st.sidebar:
        st.header("Settings")
        tracker_url = st.text_input("Tracker URL", value=DEFAULT_TRACKER)
        parallelism = st.slider("Transfer parallelism", min_value=1, max_value=16, value=4)
        chunk_size_mb = st.slider("Chunk size (MB)", min_value=1, max_value=32, value=8)
        chunk_size = chunk_size_mb * 1024 * 1024

        st.divider()
        st.subheader("Cluster Lifecycle")
        c1, c2 = st.columns(2)
        if c1.button("Start Tracker", use_container_width=True):
            _spawn("tracker", [sys.executable, "master_tracker.py"])
        if c2.button("Stop Tracker", use_container_width=True):
            _stop("tracker")

        c3, c4 = st.columns(2)
        if c3.button("Start Keepers", use_container_width=True):
            for node_id, port in DEFAULT_KEEPERS:
                _spawn(node_id, [sys.executable, "data_keeper.py", node_id, str(port)])
        if c4.button("Stop Keepers", use_container_width=True):
            for node_id, _ in DEFAULT_KEEPERS:
                _stop(node_id)

        if st.button("Stop All", use_container_width=True):
            _stop("tracker")
            for node_id, _ in DEFAULT_KEEPERS:
                _stop(node_id)

        st.divider()
        alive = []
        for name, proc in st.session_state.processes.items():
            alive.append({"service": name, "running": _is_process_alive(proc)})
        st.write("Local managed processes")
        st.dataframe(alive, use_container_width=True, hide_index=True)

    ok, status_payload, err = _tracker_status(tracker_url)
    if ok:
        st.success("Tracker is reachable")
    else:
        st.warning(f"Tracker is not reachable: {err}")

    tab_auth, tab_upload, tab_files, tab_cluster = st.tabs(
        ["Auth", "Upload", "Files", "Cluster"]
    )

    with tab_auth:
        st.subheader("Register")
        with st.form("register_form"):
            reg_user = st.text_input("Username", key="reg_user")
            reg_pass = st.text_input("Password", type="password", key="reg_pass")
            reg_submit = st.form_submit_button("Create Account", disabled=not ok)
        if reg_submit:
            r, req_err = _safe_request(
                "POST",
                f"{tracker_url}/auth/register",
                json={"username": reg_user, "password": reg_pass},
                timeout=10,
            )
            if req_err:
                st.error(f"Registration failed: {req_err}")
            elif r.status_code == 200:
                st.success("Registration successful")
            else:
                st.error(f"Registration failed: {r.text}")

        st.subheader("Login")
        with st.form("login_form"):
            login_user = st.text_input("Username", key="login_user")
            login_pass = st.text_input("Password", type="password", key="login_pass")
            login_submit = st.form_submit_button("Login", disabled=not ok)
        if login_submit:
            r, req_err = _safe_request(
                "POST",
                f"{tracker_url}/auth/login",
                json={"username": login_user, "password": login_pass},
                timeout=10,
            )
            if req_err:
                st.error(f"Login failed: {req_err}")
            elif r.status_code == 200:
                token = r.json()["token"]
                st.session_state.token = token
                st.session_state.username = login_user
                st.success("Logged in")
            else:
                st.error(f"Login failed: {r.text}")

        if not ok:
            st.info("Tracker is offline. Start it from Cluster Lifecycle in the sidebar, then retry.")

        if st.session_state.token:
            st.info(f"Active session for user: {st.session_state.username}")
            st.code(st.session_state.token)

    with tab_upload:
        st.subheader("Upload File")
        if not st.session_state.token:
            st.warning("Login first to upload files")
        uploaded = st.file_uploader("Pick a file", type=None)
        if st.button("Upload", disabled=(uploaded is None or not st.session_state.token)):
            try:
                progress_bar = st.progress(0)
                progress_text = st.empty()
                file_id = _upload_bytes(
                    blob=uploaded.getvalue(),
                    filename=uploaded.name,
                    token=st.session_state.token,
                    tracker_url=tracker_url,
                    chunk_size=chunk_size,
                    parallelism=parallelism,
                    progress_bar=progress_bar,
                    progress_text=progress_text,
                )
                st.success(f"Upload complete. File ID: {file_id}")
            except Exception as e:
                st.error(f"Upload failed: {e}")

    with tab_files:
        st.subheader("Your Files")
        if not st.session_state.token:
            st.warning("Login first to list and download files")
        files = []
        if st.session_state.token and st.button("Refresh Files"):
            r, req_err = _safe_request(
                "GET",
                f"{tracker_url}/files",
                headers=_auth_headers(st.session_state.token),
                timeout=10,
            )
            if req_err:
                st.error(f"Could not fetch files: {req_err}")
            elif r.status_code == 200:
                files = r.json()
                st.session_state["files_cache"] = files
            else:
                st.error(f"Could not fetch files: {r.text}")

        files = st.session_state.get("files_cache", files)
        if files:
            st.dataframe(files, use_container_width=True, hide_index=True)
            file_ids = [f["id"] for f in files]
            selected_file = st.selectbox("Select file", options=file_ids)

            c1, c2 = st.columns(2)
            if c1.button("Prepare Download", disabled=not st.session_state.token):
                try:
                    blob, default_name = _download_file(
                        file_id=selected_file,
                        token=st.session_state.token,
                        tracker_url=tracker_url,
                        parallelism=parallelism,
                    )
                    st.session_state.download_blob = blob
                    st.session_state.download_name = default_name
                    st.success("Download prepared. Use the button below to save it.")
                except Exception as e:
                    st.error(f"Download failed: {e}")

            if c2.button("Delete File", disabled=not st.session_state.token):
                r, req_err = _safe_request(
                    "DELETE",
                    f"{tracker_url}/files/{selected_file}",
                    headers=_auth_headers(st.session_state.token),
                    timeout=10,
                )
                if req_err:
                    st.error(f"Delete failed: {req_err}")
                elif r.status_code == 200:
                    st.success("File deleted")
                else:
                    st.error(f"Delete failed: {r.text}")

            if st.session_state.download_blob is not None:
                st.download_button(
                    label="Download File",
                    data=st.session_state.download_blob,
                    file_name=st.session_state.download_name,
                    mime="application/octet-stream",
                )
        else:
            st.caption("No files loaded yet. Click Refresh Files.")

    with tab_cluster:
        st.subheader("Cluster Health")
        if not ok:
            st.warning("Tracker offline. Start services from the sidebar.")
        else:
            nodes = status_payload.get("nodes", [])
            files_state = status_payload.get("files", {})
            recent = status_payload.get("recent_events", [])

            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Total Files", files_state.get("total", 0))
            m2.metric("Available", files_state.get("available", 0))
            m3.metric("Degraded", files_state.get("degraded", 0))
            m4.metric("Uploading", files_state.get("uploading", 0))
            m5.metric("Failed", files_state.get("failed", 0))

            st.write("Nodes")
            st.dataframe(nodes, use_container_width=True, hide_index=True)

            for n in nodes:
                n1, n2, n3 = st.columns([2, 1, 1])
                n1.write(f"{n['node_id']} ({n['status']})")
                if n2.button("Kill", key=f"kill_{n['node_id']}"):
                    r, req_err = _safe_request(
                        "POST", f"{tracker_url}/admin/nodes/{n['node_id']}/kill", timeout=5
                    )
                    if req_err:
                        st.error(req_err)
                    elif r.status_code == 200:
                        st.success(f"Paused {n['node_id']}")
                    else:
                        st.error(r.text)
                if n3.button("Revive", key=f"revive_{n['node_id']}"):
                    r, req_err = _safe_request(
                        "POST", f"{tracker_url}/admin/nodes/{n['node_id']}/revive", timeout=5
                    )
                    if req_err:
                        st.error(req_err)
                    elif r.status_code == 200:
                        st.success(f"Resumed {n['node_id']}")
                    else:
                        st.error(r.text)

            st.write("Recent events")
            st.dataframe(recent, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()