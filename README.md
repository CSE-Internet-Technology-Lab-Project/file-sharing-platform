# File Sharing Platform

A distributed, fault-tolerant file-sharing system with automatic data replication, resumable downloads via UDP, and real-time cluster health monitoring.

## Features

✅ **Distributed Storage**: Files are split into chunks and replicated across multiple data keepers
✅ **Automatic Failover**: When a node fails, data is automatically re-replicated to healthy nodes
✅ **Resumable Downloads**: UDP-based packet transfer allows downloads to resume from last received packet
✅ **Real-time Monitoring**: Live cluster health dashboard showing file status and node metrics
✅ **User Authentication**: Token-based auth with file ACLs
✅ **Chunk Verification**: SHA-256 checksums for data integrity
✅ **Web UI**: Streamlit-based interface for file management and cluster control

## Architecture

```
┌─────────────┐
│  Streamlit  │  Web UI
│   App       │
└──────┬──────┘
       │ HTTP
       │
┌──────▼──────────────────┐
│  Master Tracker (8000)  │  Control plane
│  - Flask API            │  - File metadata
│  - Upload/Download plan │  - Chunk location tracking
│  - Node health          │  - Re-replication logic
└──────┬──────────────────┘
       │ TCP Events (8001)
       │
    ┌──┴─┬────────┬────────┐
    │    │        │        │
 TCP/UDP TCP/UDP TCP/UDP   │
    │    │        │        │
┌───▼─┐ ┌▼────┐ ┌▼────┐  │
│Keep1│ │Keep2│ │Keep3│  │
│9001 │ │9002 │ │9003 │  │
└─────┘ └─────┘ └─────┘  │
  UDP:10001 10002 10003   │
                          │
Database (SQLite)◄────────┘
```

## Installation

1. **Clone and setup**:
   ```bash
   cd /path/to/FIle_sharing_Platform
   pip install -r requirements.txt
   ```

2. **Quick start** (all-in-one):
   ```bash
   bash start_all.sh
   ```
   This will start:
   - Master Tracker on `http://localhost:8000`
   - 3 Data Keepers (keeper1, keeper2, keeper3)
   - Streamlit UI on `http://localhost:8501`

## Manual Start (for debugging)

**Terminal 1 - Master Tracker**:
```bash
python master_tracker.py
# Serves on http://localhost:8000
# Event listener on port 8001
```

**Terminal 2 - Data Keepers** (run 3 times):
```bash
python data_keeper.py keeper1 9001  # TCP: 9001, UDP: 10001
python data_keeper.py keeper2 9002  # TCP: 9002, UDP: 10002
python data_keeper.py keeper3 9003  # TCP: 9003, UDP: 10003
```

**Terminal 3 - Streamlit UI**:
```bash
streamlit run streamlit_app.py
# Opens on http://localhost:8501
```

## Usage

### Web UI (Recommended)

1. **Register & Login**
   - Go to `http://localhost:8501` → Auth tab
   - Create account and login
   - Token appears after successful login

2. **Upload File**
   - Auth tab → Upload tab
   - Select file
   - Configure chunk size (1-32 MB) and parallelism (1-16)
   - Click Upload
   - Chunks are distributed across keepers with automatic replication

3. **Download File**
   - Files tab → Click "Refresh Files"
   - Select file → Click "Prepare Download"
   - Uses UDP protocol for resumable transfer
   - If connection drops, can resume from last packet
   - Click "Download File" button to save

4. **Monitor Cluster**
   - Cluster tab shows:
     - File status counts (available, degraded, uploading, failed)
     - Node health (status, active connections, disk free)
     - Recent events (chunk stored, replication, node failures)
   - Kill/Revive nodes to simulate failure scenarios

### Command Line Client

```bash
# Register
python client/client.py register username password

# Login
python client/client.py login username password

# Upload (parallelism=4, chunk-size=8MB default)
python client/client.py upload /path/to/file --token <token>

# Download
python client/client.py download <file_id> /output/path --token <token>

# Resume interrupted upload
python client/client.py resume /path/to/file <file_id> --token <token>
```

## How It Works

### Upload Flow

1. **Initialize**: Client requests upload from Tracker
2. **Plan**: Tracker assigns primary and secondary keeper for each chunk
3. **Upload**: Client sends chunk to primary keeper via TCP
4. **Replicate**: Primary keeper sends chunk to secondary keeper
5. **Notify**: Keepers notify Tracker when chunk is stored
6. **Complete**: When all chunks received, file marked as "available"

### Download Flow

1. **Request Plan**: Client asks Tracker for download location
2. **Receive Plan**: Tracker responds with healthiest keeper for each chunk
3. **UDP Transfer**: Client receives each chunk via UDP packets with sequence numbers
4. **Reassemble**: Chunks reassembled in order, checksum verified
5. **Return**: Client receives complete file

### Resumable Download (UDP)

- Each UDP packet has sequence number and total packet count
- Client tracks which packets received (even if connection drops)
- On reconnect, can request missing packets only
- Network failures don't require restart

### Failover (Auto Re-replication)

1. **Heartbeat Timeout**: No heartbeat for 3+ seconds → node marked "down"
2. **Trigger Re-replication**: For each chunk on dead node
3. **Find Survivor**: Locate healthy node with copy
4. **Push New**: Tell survivor to push chunk to new healthy target
5. **Restore**: File status restored to "available" when re-replication done

## Database Schema

**users**: Username, password hash, created_at
**files**: File metadata (name, size, chunk_size, status, owner)
**chunks**: Individual chunks (file_id, chunk_index, size, checksum, status)
**chunk_locations**: Where each chunk replica is stored (node_id, role=primary/secondary)
**upload_sessions**: Track upload progress (file_id, chunks_received, status)
**file_acl**: Access control (user_id, permission=viewer/editor/owner)
**events_log**: All cluster events (type, payload, timestamp)

## Protocols

### TCP Wire Protocol (Upload, Replication)
- 4 bytes: message length (big-endian)
- N bytes: JSON payload
- Then: binary data

Example UPLOAD:
```json
{
  "op": "UPLOAD",
  "file_id": "uuid",
  "chunk_idx": 0,
  "size": 8388608,
  "secondary": {"host": "localhost", "port": 9002, "node_id": "keeper2"}
}
```

### UDP Packet Format (Download)
- 4 bytes: sequence number
- 4 bytes: total packets
- 4 bytes: payload size
- N bytes: payload (max 65536 bytes per packet)

### Event Bus (Heartbeat, Status)
TCP connection to port 8001 sends JSON events:
```json
{"type": "chunk.stored", "payload": {"file_id": "...", "chunk_idx": 0, "node_id": "keeper1"}}
{"type": "node.heartbeat", "payload": {"node_id": "keeper1", "disk_free_mb": 1024}}
```

## Testing

### Small File (1-100 MB)
1. Start all services (`bash start_all.sh`)
2. Register and login
3. Create small test file
4. Upload and download
5. Verify file integrity

### Network Disruption Simulation
1. Upload a file
2. Wait for replication to complete
3. Click "Kill" on a keeper node
4. Observe re-replication in Cluster tab
5. File should stay available with replicas re-created

### Large File Download Interruption
1. Disable network temporarily (mock in UDP receiver)
2. Download starts via UDP
3. Network restores
4. UDP receiver resumes from last packet
5. Download completes without restart

## Troubleshooting

**Tracker not connecting**
- Check if master_tracker.py is running
- Verify port 8000 is free
- Check `/tmp/tracker.log` for errors

**Keepers not appearing**
- Verify data_keeper.py running with correct node IDs
- Check keeper logs in `/tmp/keeper*.log`
- Ensure ports 9001-9003 and 10001-10003 are free

**Upload fails**
- Check chunk_size not too large (max 32 MB recommended)
- Verify disk space in data/ directories
- Check keeper TCP connections not full (max 32 concurrent)

**Download slow/fails**
- Check parallelism setting (1-16)
- Verify UDP packets not blocked (check firewall)
- Monitor keeper active connections in Cluster tab

## Performance

Tested on localhost with 3 keepers:
- **Small files (1-50 MB)**: ~2-5 seconds with 4 parallel connections
- **Medium files (100-500 MB)**: ~10-30 seconds
- **Throughput**: ~50-100 MB/s per connection (SSD-dependent)
- **Replication lag**: <500ms after primary upload completes

## File Structure

```
.
├── master_tracker.py      # Control plane (Flask)
├── data_keeper.py         # Chunk storage (TCP + UDP)
├── db.py                  # SQLite database layer
├── event_bus.py           # In-process event bus
├── load_balancer.py       # Node selection logic
├── streamlit_app.py       # Web UI
├── client/
│   └── client.py          # CLI client
├── benchmark/
│   └── benchmark.py       # Performance tests
├── shared/
│   ├── wire.py            # TCP message format
│   ├── udp_wire.py        # UDP packet format + resumable transfer
│   ├── events.py          # Event publishing
│   └── checksums.py       # SHA-256 verification
├── static/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── data/
│   ├── keeper1/           # Chunk storage
│   ├── keeper2/
│   └── keeper3/
├── requirements.txt
├── start_all.sh           # All-in-one startup
└── README.md
```

## Future Enhancements

- [ ] Multi-datacenter replication
- [ ] Compression (gzip before chunk storage)
- [ ] Tiered storage (hot/cold)
- [ ] Partial file sharing (specific chunks)
- [ ] WebRTC peer-to-peer download
- [ ] Kubernetes operator for cluster deployment

## License

MIT

## Contributing

Pull requests welcome! Test with:
```bash
python benchmark/benchmark.py
```

---

**Questions?** Check cluster logs:
```bash
tail -f /tmp/tracker.log      # Tracker events
tail -f /tmp/keeper1.log      # Keeper 1 debug
```
