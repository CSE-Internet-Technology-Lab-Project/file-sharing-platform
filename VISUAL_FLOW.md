# 📊 File Upload & Download Flow - Visual Diagrams

## Scenario: Alice uploads a 10 MB file

### **UPLOAD FLOW**

```
┌─────────────────────────────────────────────────────────────────────┐
│                    ALICE'S COMPUTER (Client)                        │
│                                                                      │
│  File: document.pdf (10 MB)                                          │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ Chunk 0 │ Chunk 1 │ Chunk 2 │ ... (5MB each)                │  │
│  └──────────────────────────────────────────────────────────────┘  │
└──────────────────────┬──────────────────────────────────────────────┘
                       │
                       │ Upload Request
                       │ filename, size, chunk_size
                       ▼
         ┌─────────────────────────────┐
         │   MASTER TRACKER            │
         │   (Port 8000)               │
         │                             │
         │ Creates upload plan:        │
         │ ✓ File ID: abc-123          │
         │ ✓ Total chunks: 2           │
         │ ✓ Chunk 0 → keeper1+2       │
         │ ✓ Chunk 1 → keeper2+3       │
         │ ✓ Stores in database        │
         └──────────┬──────────────────┘
                    │
        ┌───────────┼───────────┐
        │           │           │
        ▼           ▼           ▼
   KEEPER1      KEEPER2       KEEPER3
   (9001)       (9002)        (9003)
    
   ▼ Chunk 0    ▼ Chunk 0     ▼ Chunk 1
   ▼ Chunk 1    ▼ (replica)   ▼ (replica)
   
   /data/       /data/        /data/
   keeper1/     keeper2/      keeper3/
   
   ├─ abc123_   ├─ abc123_    ├─ abc123_
   │  chunk0    │  chunk0     │  chunk1
   │            │             │
   └─ abc123_   └─ abc123_    └─ (if more)
      chunk1       chunk1
```

### **Key Points:**
- **Chunk 0:** Keeper1 (primary) + Keeper2 (backup)
- **Chunk 1:** Keeper2 (primary) + Keeper3 (backup)
- **Data Location:** `/data/keeper1/abc123_chunk0`, `/data/keeper2/abc123_chunk0`, etc.
- **Database:** Master tracker stores metadata (file_id, chunks, locations)

---

## Download Flow

```
┌──────────────────────────────────────────────────────────┐
│            BOB'S COMPUTER (Client)                       │
│                                                          │
│  "Download file abc-123"                                 │
└───────────────────────┬──────────────────────────────────┘
                        │
                        │ Download request (file_id)
                        ▼
         ┌─────────────────────────────┐
         │   MASTER TRACKER            │
         │   (Port 8000)               │
         │                             │
         │ Creates download plan:      │
         │ ✓ Chunk 0 from keeper1      │
         │ ✓ Chunk 1 from keeper2      │
         │ ✓ (picks healthiest nodes)  │
         └──────────┬──────────────────┘
                    │
        ┌───────────┴───────────┐
        │                       │
        ▼                       ▼
    Get Chunk 0            Get Chunk 1
    from Keeper1           from Keeper2
    (9001)                 (9002)
    
    TCP Read:              TCP Read:
    /data/keeper1/         /data/keeper2/
    abc123_chunk0          abc123_chunk1
    (5 MB)                 (5 MB)
        │                       │
        └───────────┬───────────┘
                    │
                    ▼
    ┌──────────────────────────────┐
    │  Reconstruct file in memory  │
    │  Chunk0 + Chunk1 = Document  │
    └──────────────────────────────┘
                    │
                    ▼
    Save as: downloaded_document.pdf
```

---

## Replication Strategy (Why 2 copies?)

```
Scenario 1: Keeper2 dies after upload
─────────────────────────────────────

Before:                    After Keeper2 dies:
                          
Chunk 0:                   Chunk 0:
├─ Keeper1 ✓             ├─ Keeper1 ✓
└─ Keeper2 ✓             └─ Keeper2 ✗ (dead)

Chunk 1:                   Chunk 1:
├─ Keeper2 ✓             ├─ Keeper2 ✗ (dead)
└─ Keeper3 ✓             └─ Keeper3 ✓

Result: 
• Chunk 0: Still available (Keeper1 has it)
• Chunk 1: Still available (Keeper3 has it)
• File: 100% downloadable

System then auto-replicates:
├─ Chunk 1 → Keeper3 + Keeper1
└─ File: Back to 2x redundancy
```

```
Scenario 2: No replication (bad idea)
──────────────────────────────────────

Before:                    After Keeper1 dies:

Chunk 0:                   Chunk 0:
└─ Keeper1 ✓             └─ Keeper1 ✗ (LOST!)

Chunk 1:                   Chunk 1:
└─ Keeper2 ✓             └─ Keeper2 ✓

Result:
• Chunk 0: LOST (no backup!)
• Chunk 1: Available
• File: CORRUPTED / UNRECOVERABLE

❌ This is why replication matters!
```

---

## Node Failure Detection & Recovery

```
Timeline of Keeper2 Failure:

T=0s     ✓ Keeper2 running
         Sends heartbeat: "I'm alive!"
         
T=1s     ✓ Keeper2 running
         Sends heartbeat: "I'm alive!"
         
T=2s     ✓ Keeper2 running
         Sends heartbeat: "I'm alive!"
         
T=3s     ✗ No heartbeat received!
         ✗ Keeper2 marked as "DOWN"
         
         Master tracker checks:
         "Which chunks only exist on Keeper2?"
         
         Chunks needing re-replication:
         ├─ Chunk 1 (was: Keeper2+Keeper3)
         │  Now: Only Keeper3 has it
         │  Action: Replicate to Keeper1
         │
         └─ Chunk 2 (was: Keeper2+Keeper1)
            Still: Keeper1 has it
            No action needed
         
T=4s     Re-replication in progress
         ├─ Keeper3 → Keeper1 (Chunk 1)
         └─ (network transfer)
         
T=5s     ✓ Re-replication complete
         System back to normal:
         ├─ Chunk 1: Keeper3 + Keeper1
         ├─ Chunk 2: Keeper1 + Keeper3
         └─ File: Fully redundant again

Result: User sees no downtime, file always available!
```

---

## Database Schema (What Gets Stored)

```
┌─────────────────────────────────────────────────────┐
│                 SQLite DATABASE                      │
├─────────────────────────────────────────────────────┤
│                                                      │
│  [files]                   [chunks]                 │
│  ├─ id: abc-123            ├─ id: 100               │
│  ├─ owner: alice           ├─ file_id: abc-123      │
│  ├─ filename: document     ├─ index: 0              │
│  ├─ size: 10485760         ├─ checksum: abcd1234    │
│  ├─ status: available      └─ size: 5242880         │
│  └─ created: 2024-09-01                             │
│                            [chunk_locations]        │
│  [users]                   ├─ chunk_id: 100         │
│  ├─ id: 1                  ├─ keeper: keeper1       │
│  ├─ username: alice        ├─ role: primary         │
│  ├─ password: (hashed)     └─ stored_at: timestamp  │
│  └─ created: 2024-09-01                             │
│                            [events_log]             │
│  [upload_sessions]         ├─ type: chunk.stored    │
│  ├─ id: xyz                ├─ payload: {...}        │
│  ├─ file_id: abc-123       └─ ts: 2024-09-01        │
│  ├─ progress: 2/2                                   │
│  └─ status: complete       (recent events only)     │
│                                                      │
└─────────────────────────────────────────────────────┘
     ↑
     │ Located on Master Tracker
     │ (Port 8000)
```

---

## Checksum Verification

```
Upload Process:
───────────────
Alice's File
│
├─ Calculate SHA-256: A1B2C3D4...
│
├─ Split into chunks:
│  ├─ Chunk 0: SHA-256 = X1Y2Z3W4...
│  └─ Chunk 1: SHA-256 = P9Q8R7S6...
│
└─ Store in database:
   ├─ file.checksum = A1B2C3D4...
   ├─ chunk[0].checksum = X1Y2Z3W4...
   └─ chunk[1].checksum = P9Q8R7S6...


Download Process:
──────────────
Downloaded Chunks
│
├─ Verify Chunk 0: SHA-256 = X1Y2Z3W4... ✓ MATCH
│
├─ Verify Chunk 1: SHA-256 = P9Q8R7S6... ✓ MATCH
│
└─ Combine + verify full file:
   SHA-256 = A1B2C3D4... ✓ MATCH!
   
Result: File 100% identical to original ✓
```

---

## Network Protocols

### **TCP (Reliable Upload/Download)**
```
Client → Server

Step 1: [4 bytes length] [JSON metadata] [binary data]
        └─ Length tells server how many bytes coming
           
Step 2: Server reads exactly that many bytes
        └─ No data loss
        
Step 3: Checksum verification
        └─ If mismatch, retry
```

### **UDP (Resumable Download)**
```
Client ← Server (sequenced packets)

Packet format:
┌──────────────────────────────────────────┐
│ Seq# │ Total │ PaySize │ Payload(65KB)   │
│ [0]  │ [50]  │ [size]  │ [data...]       │
└──────────────────────────────────────────┘
 4B      4B      4B        up to 65KB

Receiver tracks:
├─ Packet 0 ✓
├─ Packet 1 ✓
├─ Packet 2 ✗ (lost)
├─ Packet 3 ✓
└─ Packet 4 ✓

Connection drops?
└─ Client reconnects
   └─ Only resends Packet 2
   └─ NOT the entire file!
   └─ Much faster recovery
```

---

## Summary: From Upload to Download

```
┌─────────────────────────────────────────────────────────────┐
│                     COMPLETE FLOW                            │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. ALICE UPLOADS                                            │
│     File (10 MB) → Master Tracker                           │
│     ↓                                                        │
│  2. MASTER CREATES PLAN                                      │
│     ├─ File ID: abc-123                                     │
│     ├─ Chunks: 2 (5MB each)                                │
│     ├─ Replication: 2x copies each                          │
│     └─ Storage assignment                                   │
│     ↓                                                        │
│  3. ALICE'S CLIENT UPLOADS CHUNKS                            │
│     ├─ Chunk 0 → Keeper1 + Keeper2 (parallel)             │
│     └─ Chunk 1 → Keeper2 + Keeper3 (parallel)             │
│     ↓                                                        │
│  4. FILES ON DISK                                            │
│     ├─ /data/keeper1/abc123_chunk0 (5 MB)                 │
│     ├─ /data/keeper2/abc123_chunk0 (5 MB)                 │
│     ├─ /data/keeper2/abc123_chunk1 (5 MB)                 │
│     └─ /data/keeper3/abc123_chunk1 (5 MB)                 │
│     ↓                                                        │
│  5. MASTER TRACKER TRACKS EVERYTHING                         │
│     ├─ Database: file metadata, chunks, locations          │
│     ├─ Checksums: SHA-256 per chunk                        │
│     └─ Events log: All activities                           │
│     ↓                                                        │
│  6. BOB DOWNLOADS                                            │
│     Request: "Download abc-123" → Master Tracker           │
│     ↓                                                        │
│  7. MASTER CREATES DOWNLOAD PLAN                             │
│     ├─ Chunk 0 from Keeper1 (or Keeper2)                  │
│     └─ Chunk 1 from Keeper2 (or Keeper3)                  │
│     ↓                                                        │
│  8. BOB'S CLIENT DOWNLOADS CHUNKS                            │
│     ├─ Get Chunk 0 (TCP or UDP)                           │
│     ├─ Get Chunk 1 (TCP or UDP)                           │
│     └─ Verify checksums                                    │
│     ↓                                                        │
│  9. BOB RECEIVES FILE                                        │
│     ├─ Reconstructed from chunks                            │
│     ├─ Checksum verified: IDENTICAL to original             │
│     └─ Save: downloaded_document.pdf                        │
│                                                              │
│  ✓ FILE SHARING COMPLETE!                                   │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## Key Takeaways for Teacher

| Concept | Why It Matters |
|---------|---|
| **Chunking** | Large files split into pieces, can upload/download in parallel |
| **Replication** | 2 copies of each chunk means if 1 server fails, file still available |
| **Distributed** | Data spread across 3 servers means load is balanced |
| **Automatic** | System detects failures and re-replicates without user action |
| **Verifiable** | Checksums prove data integrity (no corruption) |
| **Fast** | Parallel transfers + UDP resumption = efficient |

