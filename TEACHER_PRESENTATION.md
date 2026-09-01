# 🎓 Complete Teacher Presentation Guide

## Your 30-Minute Classroom Demo

This guide walks you through a complete, impressive demo that shows students how a real distributed file-sharing system works.

---

## 🎬 Demo Overview (30 minutes total)

| Step | Activity | Duration | Tools |
|------|----------|----------|-------|
| 1 | Explain architecture (slides) | 5 min | ARCHITECTURE.md |
| 2 | Start the system | 2 min | `bash start_all.sh` |
| 3 | Show Web UI | 5 min | http://localhost:8501 |
| 4 | Run upload demo | 8 min | `python3 demo.py` |
| 5 | Inspect storage | 5 min | `bash show_storage.sh` |
| 6 | Simulate failure | 5 min | Web UI cluster tab |
| **Total** | | **30 min** | |

---

## 📊 Part 1: Architecture Explanation (5 minutes)

**What to explain to students:**

### System Design
```
3 Data Keepers (Storage)
     ↑     ↑     ↑
     ├─────┼─────┤
         ↓
Master Tracker (Control)
         ↓
   Web UI + API
```

**Key Points:**
1. **Master Tracker** = Brain of the system
   - Decides where to store chunks
   - Tracks where files are
   - Detects failures
   
2. **Data Keepers** = Storage nodes
   - Hold actual file chunks
   - Send heartbeats
   - Respond to upload/download requests
   
3. **Replication** = Safety mechanism
   - Every chunk stored on 2 different keepers
   - If 1 keeper dies, other copy survives
   - Automatic re-replication (no human intervention)

### File Journey
```
User uploads 10 MB file
        ↓
Split into 2 chunks (5 MB each)
        ↓
Chunk 0 → Keeper 1 + Keeper 2
Chunk 1 → Keeper 2 + Keeper 3
        ↓
Files stored on disk (/data/keeper1/, etc)
        ↓
User downloads → Master finds healthy keepers
        ↓
File reconstructed from chunks
```

---

## 🚀 Part 2: Start the System (2 minutes)

**Terminal 1 - Start services:**
```bash
cd /home/irshadsiddi/Desktop/Projects/FIle_sharing_Platform
bash start_all.sh
```

**Wait for:**
- `✓ Master Tracker started`
- `✓ Data Keeper 1 started`
- `✓ Data Keeper 2 started`
- `✓ Data Keeper 3 started`
- Then Streamlit starts (opens browser at http://localhost:8501)

**What students see:**
- 5 services starting up
- Logs showing connections
- Web UI opening automatically

---

## 🖥️ Part 3: Show Web UI (5 minutes)

**Browser shows:** http://localhost:8501

### Tab 1: Auth
```
Input:
- Username: alice
- Password: demo123

Click: Register
Then: Login
```
**Show students:** Token generation, authentication flow

### Tab 2: Upload
```
Input:
- Choose file (any 1-10 MB)
- Chunk size: 5 MB (default)
- Parallelism: 4 (default)

Click: Upload
Wait for progress bar
```
**Show students:**
- Real-time upload progress
- How many chunks created
- Status: "available"

### Tab 3: Files
```
Shows:
- filename, size, status, created time

Click: Download
File saves to your computer
```
**Show students:**
- File listing
- Download capability
- Status indicators (available/degraded)

### Tab 4: Cluster
```
Shows:
- 3 keepers (keeper1, keeper2, keeper3)
- Status: all "up" ✓
- Disk free, active connections
- Recent events log
```
**Show students:**
- Real-time cluster status
- Chunk distribution table
- Replication factor = 2
- Click "KILL" to simulate failure

---

## 📤 Part 4: Run Upload Demo (8 minutes)

**Terminal 2 - Run automated demo:**
```bash
cd /home/irshadsiddi/Desktop/Projects/FIle_sharing_Platform
python3 demo.py
```

**What happens (automated):**

1. Creates 5 KB test file
2. User "alice" registers & logs in
3. **Upload Phase:**
   - File split into chunks
   - Upload starts to keepers
   - Progress shown
   - Chunks stored on disk
4. **Storage Phase:**
   - Shows `/data/keeper1/`, `/data/keeper2/`, `/data/keeper3/`
   - Students see actual files on disk
5. **Download Phase:**
   - User "bob" logs in
   - Downloads file
   - Checksum verified
   - Shows "Files match perfectly!"

**Key teaching moment:**
```
Files stored across multiple keepers:
  keeper1/: abc123_chunk0 (5,000 bytes)
  keeper2/: abc123_chunk0 (5,000 bytes) [replica]
  keeper3/: abc123_chunk1 (5,000 bytes)

Data verified identical: ✓ (byte-for-byte match)
```

---

## 💾 Part 5: Inspect Storage (5 minutes)

**Terminal 3 - Check what's actually on disk:**
```bash
bash show_storage.sh
```

**Shows students:**
```
KEEPER1 (Port 9001)
  ├─ file123_chunk0 (5 KB)
  └─ Total: 1 chunk, 5 KB

KEEPER2 (Port 9002)
  ├─ file123_chunk0 (5 KB) [replica]
  ├─ file123_chunk1 (5 KB)
  └─ Total: 2 chunks, 10 KB

KEEPER3 (Port 9003)
  ├─ file123_chunk1 (5 KB) [replica]
  └─ Total: 1 chunk, 5 KB

TOTAL STORAGE: 3 chunks, 15 KB across 3 keepers
```

**Key points:**
- ✓ Same data replicated (chunk0 on keeper1 AND keeper2)
- ✓ Load balanced (keeper2 has 2 chunks, others have 1)
- ✓ Can actually see/verify files on disk
- ✓ Database tracks all metadata

---

## ⚠️ Part 6: Simulate Failure (5 minutes)

**Web UI - Cluster Tab:**

1. **Current state:** All 3 keepers "up"
2. **Click: "KILL" button next to Keeper2**
3. **Watch:**
   - Status changes to "down" (after 3 seconds)
   - File status changes to "degraded"
   - System identifies missing chunks
4. **Wait 2 seconds...**
   - **Auto-replication begins!**
   - Missing chunks copied to Keeper3
   - Status returns to "available"
5. **Result:** File still 100% downloadable despite server failure

**Script view (Terminal 1 logs):**
```
Keeper2 heartbeat timeout → marked DOWN
Missing chunk: file123_chunk1
Re-replication: Keeper3 → Keeper1
Re-replication complete → File status: AVAILABLE
```

**Teaching moment:**
```
Without replication:
  Keeper2 dies → Chunk lost → File CORRUPTED ❌

With replication (2x):
  Keeper2 dies → Chunk still on Keeper3 → File INTACT ✓
  System auto-replicates → File HEALTHY ✓
```

---

## 📝 Talking Points During Demo

### What Makes This Impressive:

1. **Distributed Storage**
   - "Data doesn't live in one place"
   - "Multiple servers = multiple failures needed to lose data"

2. **Redundancy**
   - "Every piece of data has a backup"
   - "Automatic backup - no manual intervention"

3. **Automatic Failover**
   - "System detects failures in real-time"
   - "Self-healing - chunks re-replicate automatically"
   - "Users don't notice anything happened"

4. **Scalability**
   - "Add more keepers → more storage"
   - "Add more replication → more safety"
   - "Add more parallelism → faster transfers"

5. **Verification**
   - "Every download verified with SHA-256"
   - "Bit-for-bit identical to original"
   - "Data integrity guaranteed"

---

## 🎯 Success Criteria: What to Show Teacher

✅ **During upload:**
- "Watch the progress bar - parallel uploads"
- "File split into chunks automatically"

✅ **Storage phase:**
- "Open file manager → show /data/keeper1/, etc"
- "Point to actual chunk files on disk"
- "Run `show_storage.sh` - proves storage distribution"

✅ **During download:**
- "Same file downloads perfectly"
- "Checksum verification passes"
- "Downloaded file identical to original"

✅ **Failure demo:**
- "Kill keeper2 - watch status change to 'degraded'"
- "Wait 3 seconds - system marks as 'down'"
- "Observe automatic re-replication"
- "File becomes 'available' again"

✅ **Database check:**
- Show `tracker.db` has all metadata
- Demonstrate query: `SELECT * FROM files;`

---

## 🐛 Troubleshooting During Demo

### "Connection refused"
```bash
# Check services are running
ps aux | grep python3

# Check ports are open
lsof -i :8000
lsof -i :9001
lsof -i :8501
```

### "Web UI not loading"
```bash
# Streamlit takes 5-10 seconds to start
# Check logs
tail -f /tmp/tracker.log
```

### "Demo script fails at upload"
```bash
# Make sure all keepers are running
curl http://localhost:9001/health
curl http://localhost:9002/health
curl http://localhost:9003/health
```

### "Files not showing in `/data/keeper*/`"
```bash
# Check permissions
ls -la data/
chmod 755 data/keeper*

# Restart services
bash start_all.sh
```

---

## 📚 Reference Documents

For deeper dives, point students to:

| Document | Purpose | Read Time |
|----------|---------|-----------|
| `TEACHER_DEMO.md` | Complete demo guide | 10 min |
| `VISUAL_FLOW.md` | Architecture diagrams | 15 min |
| `README.md` | Full documentation | 20 min |
| `QUICK_REFERENCE.md` | Code examples | 10 min |

---

## 💾 File Locations Quick Reference

```
System files:
├─ master_tracker.py      (Control plane)
├─ data_keeper.py         (Storage nodes)
├─ streamlit_app.py       (Web UI)
├─ start_all.sh          (One-command startup)
└─ db.py                 (Database layer)

Demo tools:
├─ demo.py              (Automated demo)
├─ quick_demo.sh        (Quick version)
├─ show_storage.sh      (Storage inspector)
└─ TEACHER_DEMO.md      (This guide!)

Documentation:
├─ README.md            (Full docs)
├─ VISUAL_FLOW.md       (Diagrams)
├─ QUICK_REFERENCE.md   (Examples)
└─ PROJECT_COMPLETE.md  (Status)

Data storage:
└─ data/
   ├─ keeper1/         (Node 1 storage)
   ├─ keeper2/         (Node 2 storage)
   └─ keeper3/         (Node 3 storage)

Database:
└─ tracker.db          (SQLite, created at runtime)
```

---

## 🎬 Demo Script (Minimal Version)

If time is tight, here's the 10-minute version:

```bash
# Terminal 1: Start system
bash start_all.sh
# (wait 3 seconds for all services)

# Terminal 2: Run demo
python3 demo.py

# Terminal 3: Check storage
bash show_storage.sh

# Show Web UI (automatic)
# Open http://localhost:8501
# Click Cluster tab → show replication status
```

That's it! Students see:
1. ✓ Upload with automatic chunking
2. ✓ Data replicated across multiple servers
3. ✓ Download with verification
4. ✓ Live cluster status
5. ✓ Files on actual disk

---

## ✅ Final Checklist Before Demo

- [ ] Start system: `bash start_all.sh`
- [ ] Wait 3-5 seconds for services to start
- [ ] Check cluster: `curl http://localhost:8000/api/status`
- [ ] Open browser: http://localhost:8501
- [ ] Run demo: `python3 demo.py`
- [ ] Show storage: `bash show_storage.sh`
- [ ] Test failure simulation in Web UI
- [ ] Cleanup: Remove test files after demo

---

## 🎓 Teaching Outcomes

After this demo, students understand:

1. **Why distributed systems exist**
   - Single server = single failure point
   - Multiple servers = redundancy

2. **How data replication works**
   - Copies on multiple nodes
   - Automatic failover
   - No data loss

3. **Practical concepts**
   - TCP vs UDP protocols
   - Parallel uploads/downloads
   - Chunk-based transfer
   - Checksum verification
   - Database metadata tracking

4. **Real-world trade-offs**
   - More replication = safer but uses more disk
   - Bigger chunks = faster but uses more bandwidth
   - More parallelism = better throughput but uses more CPU

---

**You're all set for an impressive demonstration! Good luck with your presentation! 🚀**
