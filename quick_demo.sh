#!/bin/bash

# Quick Teacher Demo - Shows file upload/download end-to-end
# Run this AFTER: bash start_all.sh

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}"
echo "╔════════════════════════════════════════════════════════════╗"
echo "║        FILE SHARING PLATFORM - QUICK TEACHER DEMO      ║"
echo "║   Shows: Upload → Storage → Download in 30 seconds         ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo -e "${NC}\n"

if ! curl -s http://localhost:8000/api/status > /dev/null 2>&1; then
    echo -e "${YELLOW}✗ Cluster not responding on localhost:8000${NC}"
    echo -e "${YELLOW}  Make sure to run: bash start_all.sh${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Cluster is running!${NC}\n"

echo -e "${BLUE}[Step 1] Cluster Status:${NC}"
curl -s http://localhost:8000/api/status | python3 -m json.tool | head -30
echo ""

echo -e "${BLUE}[Step 2] Creating test file...${NC}"
TEST_FILE="test_$(date +%s).txt"
echo "This is a test file for the file sharing platform! " > "$TEST_FILE"
for i in {1..100}; do echo "Line $i: Lorem ipsum dolor sit amet..." >> "$TEST_FILE"; done
FILE_SIZE=$(ls -lh "$TEST_FILE" | awk '{print $5}')
echo -e "${GREEN}✓ Created: $TEST_FILE ($FILE_SIZE)${NC}\n"

echo -e "${BLUE}[Step 3] Initial storage state:${NC}"
echo -e "  data/keeper1/: $(ls -1 data/keeper1/ 2>/dev/null | wc -l) chunks"
echo -e "  data/keeper2/: $(ls -1 data/keeper2/ 2>/dev/null | wc -l) chunks"
echo -e "  data/keeper3/: $(ls -1 data/keeper3/ 2>/dev/null | wc -l) chunks"
echo ""

echo -e "${BLUE}[Step 4] Running demo (upload + download)...${NC}"
echo -e "${CYAN}This will:${NC}"
echo "  1. Register users 'alice' and 'bob'"
echo "  2. Alice uploads the test file"
echo "  3. Show where file is stored on disk"
echo "  4. Bob downloads the file"
echo "  5. Verify downloaded = original"
echo ""

python3 demo.py

echo -e "\n${BLUE}[Step 5] Final storage state:${NC}"
echo -e "${CYAN}Files stored across keepers:${NC}"
find data/keeper* -type f -exec ls -lh {} \; 2>/dev/null | awk '{print "  " $9 " (" $5 ")"}' | sort || echo "  (no files)"
echo ""

echo -e "${BLUE}[Step 6] Files in database:${NC}"
sqlite3 data/app.db << EOF 2>/dev/null || echo "Unable to query database"
.mode column
.headers on
SELECT substr(id, 1, 8) || '...' as file_id, filename, size_bytes, status, total_chunks FROM files ORDER BY created_at DESC LIMIT 10;
EOF
echo ""

echo -e "${BLUE}[Step 7] Cluster Summary:${NC}"
curl -s http://localhost:8000/api/status | python3 -c "
import sys, json
data = json.load(sys.stdin)
nodes = data.get('nodes', [])
files = data.get('files', {})
recent = data.get('recent_events', [])
print(f'  Nodes: {len(nodes)} / 3')
print(f'  Files: {files.get(\"total\", 0)} total ({files.get(\"available\", 0)} available, {files.get(\"degraded\", 0)} degraded)')
print(f'  Events: {len(recent)}')
" 2>/dev/null || echo "  (unable to fetch)"
echo ""

echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗"
echo "║              DEMO COMPLETED SUCCESSFULLY!                   ║"
echo "║                                                              ║"
echo "║  ✓ File uploaded and split into chunks                      ║"
echo "║  ✓ Chunks replicated across multiple keepers                ║"
echo "║  ✓ File downloaded and verified                             ║"
echo "║                                                              ║"
echo "║  Next steps:                                                 ║"
echo "║  1. Check data/keeper1, keeper2, keeper3 for files      ║"
echo "║  2. Open http://localhost:8501 for Web UI                  ║"
echo "║  3. Go to Cluster tab to see live status                    ║"
echo "╚════════════════════════════════════════════════════════════╝${NC}"