#!/usr/bin/env python3
"""
Quick reference: End-to-end file sharing workflow examples
This demonstrates the complete flow with UDP resumable downloads.
"""

# ============================================================================
# EXAMPLE 1: Web UI Workflow (Recommended for testing)
# ============================================================================
"""
1. Start the platform:
   $ bash start_all.sh
   
2. Open browser: http://localhost:8501

3. Register a user:
   - Click Auth tab
   - Enter username (e.g., "alice")
   - Enter password (e.g., "pass123")
   - Click "Create Account"

4. Login:
   - Enter username & password
   - Click "Login"
   - Copy the token displayed

5. Upload a file:
   - Click Upload tab
   - Select a file (e.g., test.txt - start small!)
   - Set chunk size: 1 MB (for testing)
   - Set parallelism: 4
   - Click "Upload"
   - Watch progress bar
   
6. Download file:
   - Click Files tab
   - Click "Refresh Files"
   - Select your file
   - Click "Prepare Download"
   - Click "Download File" button
   - File saved to browser downloads

7. Monitor cluster:
   - Click Cluster tab
   - See 3 nodes (keeper1, keeper2, keeper3)
   - See file status (available, degraded, etc)
   - See recent events
"""

# ============================================================================
# EXAMPLE 2: CLI Workflow
# ============================================================================
"""
Register:
$ python3 client/client.py register alice pass123

Login:
$ python3 client/client.py login alice pass123
# Output: Token: eyJ1c2VyX2lk...

Upload file:
$ python3 client/client.py upload ~/Documents/report.pdf \\
    --token eyJ1c2VyX2lk... \\
    --chunk-size 8388608 \\
    --parallelism 4
# Output: File ID: a1b2c3d4-e5f6-7890-abcd-ef1234567890
# Uploads in 4 parallel workers, 8MB chunks

Download file:
$ python3 client/client.py download a1b2c3d4-e5f6-7890-abcd-ef1234567890 \\
    ~/Downloads/report.pdf \\
    --token eyJ1c2VyX2lk... \\
    --parallelism 4
# Downloads via UDP (resumable!)
# If network fails midway, can resume with same command

Resume interrupted upload:
$ python3 client/client.py resume ~/Documents/report.pdf \\
    a1b2c3d4-e5f6-7890-abcd-ef1234567890 \\
    --token eyJ1c2VyX2lk...
# Retries only missing chunks
"""

# ============================================================================
# EXAMPLE 3: UDP Resumable Download in Detail
# ============================================================================
"""
UDP Protocol (for technical understanding):

Packet Format:
┌─────────────┬──────────────┬──────────────┬──────────────┐
│ Seq Number  │ Total Packets │ Payload Size │    Data      │
│  (4 bytes)  │  (4 bytes)   │  (4 bytes)   │   (N bytes)  │
└─────────────┴──────────────┴──────────────┴──────────────┘

Example: Download 100 MB file
- Chunk size: 8 MB
- Packets per chunk: 8 MB / 65 KB ≈ 123 packets
- Total: 12 chunks × 123 packets = 1,476 packets

Network Failure Scenario:
1. Download starts, packets 0-500 received
2. Network drops (connection lost)
3. Client reconnects
4. Client says: "I have packets 0-500, send me 501-1476"
5. Keeper sends only missing packets
6. Download continues without restart

Benefits:
- Saves bandwidth (no re-downloading completed packets)
- Faster resume on slow networks
- Suitable for mobile/unreliable connections
- Works even if network goes down multiple times
"""

# ============================================================================
# EXAMPLE 4: Cluster Failure Recovery (Web UI)
# ============================================================================
"""
Testing auto-failover:

1. Upload a file (creates 2x replication)
   - File is in "available" state
   - Each chunk on 2 nodes

2. Kill a keeper:
   - Cluster tab
   - Click "Kill" button next to keeper1
   - keeper1 status changes to "down"
   - Watch file status: "available" → "degraded" → "available"
   
3. Monitor recovery:
   - Recent events show:
     * "node.down" event for keeper1
     * "chunk.replicate_failed" events
     * Tracker starts "chunk.replicated" events as new copies created
   
4. Revive the keeper:
   - Click "Revive" button
   - keeper1 comes back online
   - Status returns to "up"

Why this matters:
- Automatic recovery means no manual intervention
- File remains accessible even during node failure
- Demonstrates high availability
"""

# ============================================================================
# EXAMPLE 5: Testing with Test Files
# ============================================================================
"""
Create test files:

Small file (1 MB) - test basic upload/download:
$ dd if=/dev/urandom of=test_1mb.bin bs=1M count=1

Medium file (50 MB) - test replication:
$ dd if=/dev/urandom of=test_50mb.bin bs=1M count=50

Large file (500 MB) - stress test:
$ dd if=/dev/urandom of=test_500mb.bin bs=1M count=500

Or use real files:
$ cp /path/to/video.mp4 test_video.mp4
$ cp /path/to/document.pdf test_doc.pdf

Verify integrity after download:
$ md5sum test_1mb.bin
$ md5sum downloaded_file.bin
# Should match!
"""

# ============================================================================
# EXAMPLE 6: Direct API Calls
# ============================================================================
"""
Using curl to test REST API:

Register:
$ curl -X POST http://localhost:8000/auth/register \\
  -H "Content-Type: application/json" \\
  -d '{"username":"alice","password":"pass123"}'

Login:
$ curl -X POST http://localhost:8000/auth/login \\
  -H "Content-Type: application/json" \\
  -d '{"username":"alice","password":"pass123"}'
# Returns: {"token":"..."}

Get cluster status:
$ curl http://localhost:8000/api/status
# Shows nodes, files, recent events

List files:
$ curl -H "Authorization: Bearer <token>" \\
  http://localhost:8000/files

Get file details:
$ curl -H "Authorization: Bearer <token>" \\
  http://localhost:8000/files/<file_id>

Get download plan:
$ curl -H "Authorization: Bearer <token>" \\
  http://localhost:8000/files/<file_id>/download
# Returns: {"chunks":[...], "file_checksum":"..."}

Kill a keeper (simulate failure):
$ curl -X POST http://localhost:8000/admin/nodes/keeper1/kill

Revive keeper:
$ curl -X POST http://localhost:8000/admin/nodes/keeper1/revive
"""

# ============================================================================
# EXAMPLE 7: Monitoring & Debugging
# ============================================================================
"""
Check service logs:

Tracker log:
$ tail -f /tmp/tracker.log
# Shows: heartbeats, chunk events, re-replication

Keeper logs:
$ tail -f /tmp/keeper1.log
$ tail -f /tmp/keeper2.log
$ tail -f /tmp/keeper3.log
# Shows: upload/download/replication events

Check database:
$ sqlite3 data/app.db
sqlite> SELECT filename, status, size_bytes FROM files;
sqlite> SELECT type, COUNT(*) FROM events_log GROUP BY type;

Check file storage:
$ ls -lh data/keeper1/
$ ls -lh data/keeper2/
$ ls -lh data/keeper3/
# Should see chunk files like: uuid_chunk0, uuid_chunk1, ...

Monitor network (UDP):
$ tcpdump -i lo 'port 10001 or port 10002 or port 10003'
# See UDP packets flowing during downloads
"""

# ============================================================================
# EXPECTED BEHAVIOR
# ============================================================================
"""
✓ Upload completes in seconds (depends on file size)
✓ Download happens via UDP packets
✓ If network drops during download, reconnect and continue
✓ Chunks appear on 2 different keepers (replication)
✓ Killing a keeper triggers re-replication
✓ File status shows recovery progress
✓ Can delete files
✓ Can share files via ACL
✓ All data verified with checksums

Known Characteristics:
- Single machine (all on localhost)
- Heartbeat check every 1 second
- Node marked down after 3 seconds no heartbeat
- Re-replication starts within seconds
- Small files: <1 second upload, <1 second download
- Larger files: proportional to size and parallelism
- Chunk size default: 8 MB (configurable 1-32 MB)
"""

# ============================================================================
# TROUBLESHOOTING
# ============================================================================
"""
Tracker won't start:
$ python3 master_tracker.py
# Check if port 8000 is in use: lsof -i :8000
# Check logs: cat /tmp/tracker.log

Keepers won't connect:
$ python3 data_keeper.py keeper1 9001
# Check if port 9001 is in use: lsof -i :9001
# Verify tracker is running first

Streamlit won't open:
$ streamlit run streamlit_app.py
# Check if port 8501 is in use: lsof -i :8501
# Verify browser can access http://localhost:8501

Upload fails:
- Check tracker is running
- Check all 3 keepers are running
- Check disk space in data/ directory
- Try smaller file first

Download fails:
- Check file exists (refresh file list)
- Try via Cluster tab → status to see nodes
- Check keeper logs for download errors
- Try smaller file first

UDP download slow:
- Increase parallelism (1-16) in settings
- Reduce chunk size if bandwidth limited
- Check network latency: ping localhost

Data corruption check:
$ md5sum original_file.bin
$ md5sum downloaded_file.bin
# If different, network issue or storage problem
"""

if __name__ == "__main__":
    print(__doc__)
