#!/bin/bash
set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${YELLOW}Starting File Sharing Platform...${NC}"

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo "Python 3 is not installed"
    exit 1
fi

cleanup_stale_processes() {
    echo -e "${YELLOW}Checking for stale services on required ports...${NC}"
    for port in 8000 8001 9001 9002 9003 8501; do
        if command -v fuser >/dev/null 2>&1; then
            if fuser "$port/tcp" >/dev/null 2>&1; then
                echo -e "${RED}Killing stale process on port $port${NC}"
                fuser -k "$port/tcp" >/dev/null 2>&1 || true
            fi
        fi
    done
}

cleanup_stale_processes

# Install requirements if needed
if ! python3 -c "import flask, streamlit, requests, matplotlib" 2>/dev/null; then
    echo -e "${YELLOW}Installing requirements...${NC}"
    python3 -m pip install --disable-pip-version-check -q -r requirements.txt || {
        echo -e "${RED}Dependency install failed. Check your network or install the packages manually.${NC}"
        echo -e "${YELLOW}Try: python3 -m pip install -r requirements.txt${NC}"
        exit 1
    }
fi

# Create data directories
mkdir -p data/keeper1 data/keeper2 data/keeper3

# Start Master Tracker in the background
echo -e "${GREEN}Starting Master Tracker on port 8000${NC}"
python3 master_tracker.py > /tmp/tracker.log 2>&1 &
TRACKER_PID=$!
echo "Tracker PID: $TRACKER_PID"

# Wait for tracker to start
sleep 2

# Start Data Keepers in the background
echo -e "${GREEN}Starting Data Keeper 1 on port 9001${NC}"
python3 data_keeper.py keeper1 9001 > /tmp/keeper1.log 2>&1 &
KEEPER1_PID=$!
echo "Keeper 1 PID: $KEEPER1_PID"

echo -e "${GREEN}Starting Data Keeper 2 on port 9002${NC}"
python3 data_keeper.py keeper2 9002 > /tmp/keeper2.log 2>&1 &
KEEPER2_PID=$!
echo "Keeper 2 PID: $KEEPER2_PID"

echo -e "${GREEN}Starting Data Keeper 3 on port 9003${NC}"
python3 data_keeper.py keeper3 9003 > /tmp/keeper3.log 2>&1 &
KEEPER3_PID=$!
echo "Keeper 3 PID: $KEEPER3_PID"

# Wait for keepers to start
sleep 2

# Start Streamlit UI
echo -e "${GREEN}Starting Streamlit app on port 8501${NC}"
streamlit run streamlit_app.py --logger.level=error --client.showErrorDetails=false

# Cleanup on exit
trap "kill $TRACKER_PID $KEEPER1_PID $KEEPER2_PID $KEEPER3_PID 2>/dev/null || true" EXIT
