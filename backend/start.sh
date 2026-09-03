#!/bin/bash
# Start script for Investigation Agent Client backend
# Runs on port 8001 to avoid conflict with Risk Platform (port 8000)

cd "$(dirname "$0")"

echo "Starting Investigation Agent Client backend on port 8001..."
echo "Risk Platform should be running on port 8000"

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Install dependencies if needed
if [ ! -d "venv" ] && [ ! -f ".venv/bin/activate" ]; then
    echo "Installing dependencies..."
    pip install -r requirements.txt
fi

# Start the server on port 8001
uvicorn app.main:app --host 127.0.0.1 --port 8001 --log-level info
