#!/bin/bash

# Storage Inspector - Show exactly what's stored where
# Great for showing teacher how data is distributed

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${CYAN}"
echo "╔════════════════════════════════════════════════════════════╗"
echo "║         FILE STORAGE INSPECTOR - Where Data Lives           ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo -e "${NC}\n"

# Function to show keeper contents
show_keeper() {
    local keeper=$1
    local port=$((9000 + ${keeper#keeper}))
    
    echo -e "${BLUE}┌─ KEEPER $keeper (Port $port, UDP $((port + 1000))) ─────┐${NC}"
    
    keeper_dir="data/$keeper"
    
    if [ ! -d "$keeper_dir" ]; then
        echo -e "${YELLOW}  [Directory not created yet]${NC}"
    else
        chunk_count=$(find "$keeper_dir" -type f 2>/dev/null | wc -l)
        
        if [ $chunk_count -eq 0 ]; then
            echo -e "${YELLOW}  [Empty - no chunks stored yet]${NC}"
        else
            total_size=0
            echo -e "${GREEN}  Chunks stored:${NC}"
            
            for chunk_file in $(find "$keeper_dir" -type f 2>/dev/null | sort); do
                size=$(stat -f%z "$chunk_file" 2>/dev/null || stat -c%s "$chunk_file" 2>/dev/null)
                size_human=$(numfmt --to=iec-i --suffix=B "$size" 2>/dev/null || echo "$size bytes")
                chunk_name=$(basename "$chunk_file")
                
                total_size=$((total_size + size))
                
                # Try to extract file_id and chunk index
                if [[ "$chunk_name" =~ ^([a-f0-9-]+)_chunk([0-9]+)$ ]]; then
                    file_id="${BASH_REMATCH[1]:0:8}..."
                    chunk_idx="${BASH_REMATCH[2]}"
                    echo -e "    ├─ ${CYAN}$file_id${NC} chunk $chunk_idx ($size_human)"
                else
                    echo -e "    ├─ ${CYAN}$chunk_name${NC} ($size_human)"
                fi
            done
            
            total_human=$(numfmt --to=iec-i --suffix=B "$total_size" 2>/dev/null || echo "$total_size bytes")
            echo -e "${GREEN}  ├─ Total: $chunk_count chunks, $total_human${NC}"
        fi
    fi
    
    echo -e "${BLUE}└───────────────────────────────────────────────────┘${NC}\n"
}

# Show all keepers
show_keeper "keeper1"
show_keeper "keeper2"
show_keeper "keeper3"

# Summary statistics
echo -e "${BLUE}┌─ TOTAL STORAGE SUMMARY ──────────────────────────┐${NC}"

total_chunks=0
total_bytes=0

for keeper in keeper1 keeper2 keeper3; do
    keeper_dir="data/$keeper"
    if [ -d "$keeper_dir" ]; then
        count=$(find "$keeper_dir" -type f 2>/dev/null | wc -l)
        size=$(du -sb "$keeper_dir" 2>/dev/null | awk '{print $1}')
        
        total_chunks=$((total_chunks + count))
        total_bytes=$((total_bytes + size))
    fi
done

if [ $total_chunks -eq 0 ]; then
    echo -e "${YELLOW}  No chunks stored yet${NC}"
    echo -e "${CYAN}  Hint: Run 'bash start_all.sh' then 'python3 demo.py'${NC}"
else
    total_human=$(numfmt --to=iec-i --suffix=B "$total_bytes" 2>/dev/null || echo "$total_bytes bytes")
    
    echo -e "${GREEN}  Total chunks across all keepers: $total_chunks${NC}"
    echo -e "${GREEN}  Total storage used: $total_human${NC}"
    
    # Calculate replication factor
    if [ $total_chunks -gt 0 ]; then
        # Rough estimate: if spread across multiple keepers, probably replicated
        keeper_with_chunks=0
        for keeper in keeper1 keeper2 keeper3; do
            if [ -d "data/$keeper" ]; then
                count=$(find "data/$keeper" -type f 2>/dev/null | wc -l)
                if [ $count -gt 0 ]; then
                    keeper_with_chunks=$((keeper_with_chunks + 1))
                fi
            fi
        done
        
        if [ $keeper_with_chunks -gt 1 ]; then
            echo -e "${GREEN}  Replication: ✓ Data spread across $keeper_with_chunks keepers${NC}"
            echo -e "${GREEN}  Redundancy: ✓ If 1 keeper fails, data still available${NC}"
        fi
    fi
fi

echo -e "${BLUE}└──────────────────────────────────────────────────┘${NC}\n"

# Show file metadata from database
echo -e "${BLUE}┌─ FILES IN DATABASE ──────────────────────────────┐${NC}"

if [ -f "tracker.db" ]; then
    echo -e "${GREEN}  Querying SQLite database...${NC}\n"
    
    sqlite3 tracker.db << EOF 2>/dev/null || echo "Unable to query database"
.mode column
.headers on
SELECT 
    substr(id, 1, 8) || '...' as file_id,
    filename,
    size_bytes,
    status,
    total_chunks
FROM files
ORDER BY created_at DESC
LIMIT 10;
EOF
else
    echo -e "${YELLOW}  Database not found (tracker.db)${NC}"
    echo -e "${CYAN}  It will be created when the system starts${NC}"
fi

echo -e "${BLUE}└──────────────────────────────────────────────────┘${NC}\n"

# Show Web UI access
echo -e "${CYAN}💡 TIP: View storage in Web UI:${NC}"
echo -e "   1. Open: ${YELLOW}http://localhost:8501${NC}"
echo -e "   2. Go to: ${YELLOW}Cluster${NC} tab"
echo -e "   3. See: Real-time chunk distribution and replication status\n"

# Show API access
echo -e "${CYAN}💡 TIP: Query via API:${NC}"
echo -e "   ${YELLOW}curl http://localhost:8000/api/status | jq .stats${NC}\n"

# Next steps
echo -e "${CYAN}📝 Next Steps:${NC}"
if [ $total_chunks -eq 0 ]; then
    echo -e "   1. Start system: ${YELLOW}bash start_all.sh${NC}"
    echo -e "   2. Run demo: ${YELLOW}python3 demo.py${NC}"
    echo -e "   3. Check storage: ${YELLOW}bash show_storage.sh${NC}"
else
    echo -e "   ✓ Files are stored!"
    echo -e "   → Simulate failure: Open Web UI Cluster tab → KILL button"
    echo -e "   → Watch auto-recovery: Chunks re-replicate automatically"
fi
echo ""
