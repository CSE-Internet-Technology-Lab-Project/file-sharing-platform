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


def _list_users(tracker_url: str, token: str) -> list[dict]:
    r, req_err = _safe_request(
        "GET",
        f"{tracker_url}/users",
        headers=_auth_headers(token),
        timeout=10,
    )
    if req_err or r is None or r.status_code != 200:
        return []
    return r.json()


def _share_file_to_user(tracker_url: str, token: str, file_id: str, username: str, permission: str):
    r, req_err = _safe_request(
        "POST",
        f"{tracker_url}/files/{file_id}/share",
        json={"username": username, "permission": permission},
        headers=_auth_headers(token),
        timeout=10,
    )
    if req_err:
        raise RuntimeError(req_err)
    if r.status_code != 200:
        raise RuntimeError(r.text)
    return r.json()


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
    # Fetch file detail to get the real filename
    detail_resp, detail_err = _safe_request(
        "GET",
        f"{tracker_url}/files/{file_id}",
        headers=_auth_headers(token),
        timeout=10,
    )
    real_filename = f"{file_id}.bin"
    if not detail_err and detail_resp is not None and detail_resp.status_code == 200:
        detail = detail_resp.json()
        real_filename = detail.get("filename", real_filename)

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

    return ordered_bytes, real_filename


def _format_size(size_bytes: int) -> str:
    """Return a human-friendly file size string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def _packet_demo_state() -> dict:
    if "packet_demo" not in st.session_state:
        st.session_state.packet_demo = {
            "total": 12,
            "received": list(range(8)),
            "missing": [],
            "resent": [],
        }
    return st.session_state.packet_demo


def _set_packet_demo_state(received: list[int] | None = None, missing: list[int] | None = None, resent: list[int] | None = None):
    state = _packet_demo_state()
    if received is not None:
        state["received"] = received
    if missing is not None:
        state["missing"] = missing
    if resent is not None:
        state["resent"] = resent


def _render_packet_demo():
    state = _packet_demo_state()
    total = state["total"]
    received = sorted(set(state["received"]))
    missing = sorted(set(state["missing"]))
    resent = sorted(set(state["resent"]))
    received_set = set(received)
    missing_set = set(missing)
    resent_set = set(resent)

    st.subheader("Packet Transfer Demo")
    p1, p2, p3, p4 = st.columns(4)
    if p1.button("Run transfer"):
        _set_packet_demo_state(received=list(range(8)), missing=[], resent=[])
    if p2.button("Simulate drop"):
        _set_packet_demo_state(received=[i for i in range(total) if i not in {4, 9}], missing=[4, 9], resent=[])
    if p3.button("Resume missing"):
        if missing:
            resumed = sorted(set(received) | set(missing))
            _set_packet_demo_state(received=resumed, missing=[], resent=missing)
    if p4.button("Reset"):
        _set_packet_demo_state(received=list(range(8)), missing=[], resent=[])

    progress = (len(received) / total) * 100 if total else 0
    st.progress(progress / 100)
    st.caption(f"{len(received)} / {total} packets received")

    packet_cols = st.columns(6)
    for packet_id in range(total):
        if packet_id in received_set:
            status = "Received"
            color = "green"
        elif packet_id in missing_set:
            status = "Missing"
            color = "red"
        elif packet_id in resent_set:
            status = "Resent"
            color = "orange"
        else:
            status = "Pending"
            color = "gray"

        with packet_cols[packet_id % 6]:
            st.markdown(
                f"<div style='padding:0.6rem;border:1px solid {color};border-radius:8px;text-align:center;margin-bottom:0.5rem;background:rgba(255,255,255,0.03);'>"
                f"<div style='font-weight:700'>#{packet_id}</div>"
                f"<div style='font-size:0.7rem;color:{color};'>{status}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    st.caption("This demo shows the file being split into sequenced packets. When a connection drops, the receiver asks for only the missing packets and resumes from there.")


def main():
    st.set_page_config(page_title="FileVault — File Sharing Platform", layout="wide", page_icon="📁")
    _init_state()

    st.markdown(
        """
        <style>
        .stApp {
            background: linear-gradient(135deg, #1e1e2f, #2b2b3d);
            color: #f0f0f0;
        }
        .stButton > button {
            background: rgba(255,255,255,0.1);
            border: 1px solid #6366f1;
            color: #fff;
            border-radius: 8px;
            padding: 0.5rem 1rem;
            transition: all 0.2s ease;
        }
        .stButton > button:hover {
            background: rgba(255,255,255,0.2);
            border-color: #a78bfa;
        }
        .stSelectbox > div > div {
            background: rgba(255,255,255,0.05) !important;
            color: #fff !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    tracker_url = DEFAULT_TRACKER
    parallelism = 4
    chunk_size = 8 * 1024 * 1024

    with st.sidebar:
        st.header("⚙️ Settings")
        tracker_url = st.text_input("Tracker URL", value=DEFAULT_TRACKER)
        parallelism = st.slider("Transfer parallelism", min_value=1, max_value=16, value=4)
        chunk_size_mb = st.slider("Chunk size (MB)", min_value=1, max_value=32, value=8)
        chunk_size = chunk_size_mb * 1024 * 1024

        if st.session_state.token:
            st.divider()
            st.subheader(f"👤 {st.session_state.username}")
            if st.button("🚪 Logout", use_container_width=True, type="primary"):
                st.session_state.token = ""
                st.session_state.username = ""
                st.session_state.download_blob = None
                st.session_state.download_name = "download.bin"
                st.session_state.pop("files_cache", None)
                st.rerun()

        st.divider()
        st.subheader("🖥️ Cluster Lifecycle")
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

    title_col, status_col = st.columns([3, 1])
    with title_col:
        st.title("📁 FileVault")
        st.caption("Distributed file sharing with replication, failover & live cluster control")
    with status_col:
        if ok:
            st.success("🟢 Tracker Online")
        else:
            st.error("🔴 Tracker Offline")

    if not st.session_state.token:
        st.divider()
        if not ok:
            st.info("⚡ Start the tracker from *Cluster Lifecycle* in the sidebar, then log in.")

        login_col, spacer, register_col = st.columns([1, 0.2, 1])

        with login_col:
            st.subheader("🔑 Sign In")
            with st.form("login_form"):
                login_user = st.text_input("Username", key="login_user", placeholder="Enter your username")
                login_pass = st.text_input("Password", type="password", key="login_pass", placeholder="Enter your password")
                login_submit = st.form_submit_button("Sign In", disabled=not ok, use_container_width=True, type="primary")
            if login_submit:
                if not login_user or not login_pass:
                    st.error("Please enter both username and password.")
                else:
                    r, req_err = _safe_request(
                        "POST",
                        f"{tracker_url}/auth/login",
                        json={"username": login_user, "password": login_pass},
                        timeout=10,
                    )
                    if req_err:
                        st.error(f"❌ Connection error: {req_err}")
                    elif r.status_code == 200:
                        st.session_state.token = r.json()["token"]
                        st.session_state.username = login_user
                        st.success(f"✅ Welcome back, **{login_user}**!")
                        st.rerun()
                    else:
                        st.error("❌ Invalid username or password.")

        with register_col:
            st.subheader("✨ Create Account")
            with st.form("register_form"):
                reg_user = st.text_input("Choose username", key="reg_user", placeholder="Pick a username")
                reg_pass = st.text_input("Choose password", type="password", key="reg_pass", placeholder="Min 4 characters")
                reg_confirm = st.text_input("Confirm password", type="password", key="reg_confirm", placeholder="Re-enter password")
                reg_submit = st.form_submit_button("Create Account", disabled=not ok, use_container_width=True)
            if reg_submit:
                if not reg_user or not reg_pass:
                    st.error("Please fill in all fields.")
                elif reg_pass != reg_confirm:
                    st.error("❌ Passwords do not match.")
                elif len(reg_pass) < 4:
                    st.error("❌ Password must be at least 4 characters.")
                else:
                    r, req_err = _safe_request(
                        "POST",
                        f"{tracker_url}/auth/register",
                        json={"username": reg_user, "password": reg_pass},
                        timeout=10,
                    )
                    if req_err:
                        st.error(f"❌ Connection error: {req_err}")
                    elif r.status_code == 200:
                        st.success("✅ Account created! Sign in with your new credentials.")
                    elif r.status_code == 409:
                        st.error("❌ Username already taken. Try a different one.")
                    else:
                        st.error(f"❌ Registration failed: {r.text}")

        st.stop()

    tab_files, tab_upload, tab_cluster = st.tabs(["📂 My Files", "⬆️ Upload", "🖥️ Cluster"])

    with tab_files:
        st.subheader("Your Files")
        files = st.session_state.get("files_cache", [])
        if st.button("🔄 Refresh Files") or not files:
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

        if files:
            display_data = []
            for f in files:
                display_data.append({
                    "📄 Filename": f.get("filename", "untitled"),
                    "📦 Size": _format_size(f.get("size_bytes", 0)),
                    "🔄 Status": f.get("status", "unknown").capitalize(),
                    "📋 Version": f.get("current_version", 1),
                })
            st.dataframe(display_data, use_container_width=True, hide_index=True)

            file_options = {}
            for f in files:
                fname = f.get("filename", "untitled")
                short_id = f["id"][:8]
                label = f"{fname}  ({short_id}…)"
                file_options[label] = f["id"]

            selected_label = st.selectbox("Select a file", options=list(file_options.keys()))
            selected_file = file_options[selected_label]

            st.markdown("---")
            share_col1, share_col2 = st.columns([2, 1])
            share_username = share_col1.text_input("Share with username", value="", placeholder="Enter a username")
            share_permission = share_col2.selectbox("Permission", ["viewer", "editor"], index=0)
            if st.button("🔗 Share File", disabled=not share_username):
                try:
                    result = _share_file_to_user(
                        tracker_url=tracker_url,
                        token=st.session_state.token,
                        file_id=selected_file,
                        username=share_username,
                        permission=share_permission,
                    )
                    st.success(f"✅ Shared with **{result['shared_with']}** ({result['permission']})")
                    st.session_state.pop("files_cache", None)
                    st.rerun()
                except Exception as e:
                    st.error(f"Share failed: {e}")

            st.markdown("---")
            c1, c2 = st.columns(2)
            if c1.button("⬇️ Prepare Download", use_container_width=True):
                try:
                    with st.spinner("Fetching file from cluster…"):
                        blob, download_name = _download_file(
                            file_id=selected_file,
                            token=st.session_state.token,
                            tracker_url=tracker_url,
                            parallelism=parallelism,
                        )
                    st.session_state.download_blob = blob
                    st.session_state.download_name = download_name
                    st.success(f"✅ Ready to download: **{download_name}** ({_format_size(len(blob))})")
                except Exception as e:
                    st.error(f"Download failed: {e}")

            if c2.button("🗑️ Delete File", use_container_width=True):
                r, req_err = _safe_request(
                    "DELETE",
                    f"{tracker_url}/files/{selected_file}",
                    headers=_auth_headers(st.session_state.token),
                    timeout=10,
                )
                if req_err:
                    st.error(f"Delete failed: {req_err}")
                elif r.status_code == 200:
                    st.success("✅ File deleted")
                    st.session_state.pop("files_cache", None)
                    st.rerun()
                else:
                    st.error(f"Delete failed: {r.text}")

            if st.session_state.download_blob is not None:
                st.download_button(
                    label=f"💾 Save {st.session_state.download_name}",
                    data=st.session_state.download_blob,
                    file_name=st.session_state.download_name,
                    mime="application/octet-stream",
                    use_container_width=True,
                )
        else:
            st.info("📭 No files yet. Upload your first file in the **Upload** tab!")

    with tab_upload:
        st.subheader("⬆️ Upload File")
        uploaded = st.file_uploader("Pick a file to upload", type=None)
        if uploaded:
            st.info(f"📄 **{uploaded.name}** — {_format_size(uploaded.size)}")
        if st.button("🚀 Upload", disabled=(uploaded is None), use_container_width=True, type="primary"):
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
                st.session_state.pop("files_cache", None)
                st.success(f"✅ **{uploaded.name}** uploaded successfully!")
                st.caption(f"File ID: `{file_id}`")
            except Exception as e:
                st.error(f"❌ Upload failed: {e}")

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

            _render_packet_demo()

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