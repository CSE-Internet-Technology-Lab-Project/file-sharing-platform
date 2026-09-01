# 🎓 Teacher Demo Guide - File Sharing Platform

## Quick Overview
This is a **distributed file-sharing system** where:
- ✅ User 1 (Alice) uploads a file
- ✅ File gets automatically **split into chunks**
- ✅ Each chunk is **stored on 2 different servers** (for safety)
- ✅ User 2 (Bob) downloads the file
- ✅ System proves files are **identical**

---

## 📋 Step-by-Step Demo for Teacher

### **Phase 0: Start the System**

```bash
cd /home/irshadsiddi/Desktop/Projects/FIle_sharing_Platform
bash start_all.sh
```

This starts:
- 1 **Master Tracker** (control center) - Port 8000
- 3 **Data Keepers** (storage nodes) - Ports 9001, 9002, 9003
- 1 **Streamlit Web UI** - Port 8501

**Wait 2-3 seconds** for all services to start.

---

### **Phase 1: Run the Complete Demo**

Open **another terminal** and run:

```bash
cd /home/irshadsiddi/Desktop/Projects/FIle_sharing_Platform
python3 demo.py
```

**What happens:**
1. ✅ Creates a 5 KB demo file
2. ✅ User "alice" registers and logs in
3. ✅ Alice uploads the file
4. ✅ Shows where file is stored on disk
5. ✅ User "bob" logs in
6. ✅ Bob downloads the file
7. ✅ Verifies download matches original (byte-for-byte)

**Expected output shows:**
```
✓ File uploaded with ID: abc-123-def
ℹ File will be split into 1 chunks
ℹ Each chunk will be replicated across 2 keepers

📁 Storage Locations:
  keeper1/
    └─ abc-123-def_chunk0 (5,000 bytes)
  keeper2/
    └─ abc-123-def_chunk0 (5,000 bytes)
  keeper3/
    └─ (empty)

✓ Files match perfectly! (5,000 bytes)
```

---

## 📊 Understanding the Storage

### **How It Works (Visual)**

```
                    MASTER TRACKER
                   (Control Plane)
                    Port 8000
                        |
            ____________|____________
           |            |            |
        KEEPER1       KEEPER2      KEEPER3
        Port 9001     Port 9002    Port 9003
        UDP 10001     UDP 10002    UDP 10003
           |            |            |
        /data/         /data/       /data/
       keeper1/        keeper2/     keeper3/
```

### **File Upload Example**

If you upload a **10 MB file** with **5 MB chunks**:

```
UPLOAD STARTS
    |
    ├─→ Chunk 0 (5MB)
    |    ├─→ Stored on Keeper1 (primary)
    |    └─→ Stored on Keeper2 (backup/replica)
    |
    └─→ Chunk 1 (5MB)
         ├─→ Stored on Keeper2 (primary)
         └─→ Stored on Keeper3 (backup/replica)
```

**File locations after upload:**
```
data/keeper1/
  └─ file123_chunk0  (5 MB)

data/keeper2/
  ├─ file123_chunk0  (5 MB) [replica]
  └─ file123_chunk1  (5 MB)

data/keeper3/
  └─ file123_chunk1  (5 MB) [replica]
```

---

## 🎬 Alternative Demo: Web UI

Instead of running the script, you can do it **live on screen**:

1. **Open browser** → http://localhost:8501
2. **Tab 1 - Auth:**
   - Click "Register"
   - Username: `alice`, Password: `pass123`
   - Click "Register"
3. **Tab 2 - Upload:**
   - Select any file (5-100 MB works best for demo)
   - Keep chunk size as default (5 MB)
   - Click "Upload"
   - Watch progress bar
4. **Tab 3 - Files:**
   - See uploaded file listed
   - Click "Download"
   - File saves to downloads
5. **Tab 4 - Cluster:**
   - Shows 3 keepers running
   - Shows replica distribution
   - Shows recent events

---

## 💾 How to Verify Storage

### **Method 1: Check Disk Directly**

```bash
# Terminal command to see stored chunks
ls -lah data/keeper*/

# Example output:
# data/keeper1/:
# -rw-r--r-- 1 user user 5.2M Sep  1 10:15 abc123_chunk0
#
# data/keeper2/:
# -rw-r--r-- 1 user user 5.2M Sep  1 10:15 abc123_chunk0
# -rw-r--r-- 1 user user 5.2M Sep  1 10:16 abc123_chunk1
#
# data/keeper3/:
# -rw-r--r-- 1 user user 5.2M Sep  1 10:16 abc123_chunk1
```

### **Method 2: Check via Web UI**

Go to **Cluster tab** → Shows:
- ✓ Total files stored
- ✓ Total chunks
- ✓ Replication status
- ✓ Disk usage per keeper

### **Method 3: Check via API**

```bash
# Get all files and their storage locations
curl http://localhost:8000/api/status

# Returns JSON with:
# - 3 nodes (keeper1, keeper2, keeper3)
# - Status: "up" or "down"
# - Disk free
# - Active connections
# - Recent events
```

---

## 🔴 Disaster Recovery Demo (Advanced)

### **Simulate Node Failure**

```bash
# In Web UI, go to Cluster tab
# Click "KILL" button next to keeper2
```

**What happens:**
1. Keeper2 stops responding
2. After 3 seconds, marked as "down"
3. Master tracker identifies data loss (chunk X needs replica)
4. **Automatically re-replicates** chunk to keeper3
5. System back to normal (2x redundancy)

**On disk, you'll see:**
```
# Before kill:
data/keeper2/: chunk0, chunk1

# After kill (chunk2 lost):
data/keeper2/: [EMPTY - service stopped]

# After re-replication (auto-recovered):
data/keeper3/: chunk0_replica, chunk1_replica [NEW COPIES]
```

---

## 📈 Key Numbers for Teacher

| Metric | Value |
|--------|-------|
| **Storage Nodes** | 3 keepers |
| **Replication Factor** | 2x (each chunk on 2 keepers) |
| **Upload Parallelism** | 1-16 parallel connections |
| **Download Protocol** | TCP (reliable) or UDP (resumable) |
| **Chunk Size** | 1-32 MB (configurable) |
| **Data Integrity** | SHA-256 checksums |
| **Failover Time** | ~3 seconds |
| **Re-replication Time** | Automatic, ~1-2 seconds |

---

## 📝 Database Schema (What's Being Tracked)

```
SQLite Database stores:

[users table]
├─ username: "alice", "bob"
└─ password: hashed

[files table]
├─ file_id: "abc-123"
├─ filename: "document.pdf"
├─ size_bytes: 10485760 (10 MB)
├─ status: "available" (or "degraded" during recovery)
└─ created_at: timestamp

[chunks table]
├─ chunk_id, file_id
├─ chunk_index: 0, 1, 2...
├─ size_bytes: 5242880 (5 MB per chunk)
└─ checksum: SHA-256 hash

[chunk_locations table]
├─ chunk_id
├─ node_id: "keeper1", "keeper2", etc
└─ role: "primary" or "replica"

[events_log table]
├─ type: "chunk.stored", "node.heartbeat", etc
├─ payload: {details in JSON}
└─ ts: timestamp
```

---

## 🎯 Why This Matters (Teaching Points)

1. **Distributed Storage**
   - Data split across multiple servers
   - No single point of failure

2. **Redundancy**
   - Every chunk stored on 2 servers
   - If server fails, other copy still available

3. **Automatic Recovery**
   - System detects failures automatically
   - Re-replicates lost data without manual intervention
   - User doesn't notice anything happened

4. **Scalability**
   - Can add more keepers
   - Can handle larger files
   - Can handle more users

5. **Network Resilience**
   - UDP protocol with packet sequencing
   - Downloads can resume if connection drops
   - Chunk-based transfer (not entire file at once)

---

## 🐛 Troubleshooting

### **"Connection refused" error**
→ Make sure `bash start_all.sh` is still running in another terminal

### **Demo script says "Cluster check failed"**
→ Keepers might be slow to start. Wait 3 seconds and try again.

### **Files not appearing in `/data/keeper*/`**
→ Check if services are running: `ps aux | grep python3`

### **Downloaded file is corrupt**
→ Should not happen - demo verifies byte-for-byte match

---

## ✅ Success Criteria

After running the demo, you should be able to show teacher:

✓ **Upload phase:** File sent from client to master to keepers
✓ **Storage phase:** Chunks visible on disk in `/data/keeper1`, `/data/keeper2`, `/data/keeper3`
✓ **Download phase:** Another user (or same user) downloads file
✓ **Verification phase:** Downloaded file is identical to original
✓ **Cluster status:** Web UI shows 3 nodes, replication status, events log

---

**That's it! This demo shows a complete end-to-end file sharing system with redundancy, automatic failover, and verification.**

Questions? Check `README.md` or `QUICK_REFERENCE.md`
