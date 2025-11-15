#!/bin/bash
# Game Arena Server Startup Script
# This script starts both the backend API and serves the integrated chess UI

# Kill any existing server on port 8000
echo "Checking for existing server on port 8000..."
lsof -ti:8000 | xargs kill -9 2>/dev/null && echo "Killed existing server" || echo "No existing server found"

# Load environment variables from .env file
if [ -f /Users/yipman/Downloads/github/game_arena/.env ]; then
    export $(cat /Users/yipman/Downloads/github/game_arena/.env | grep -v '^#' | xargs)
    echo "Loaded environment variables from .env"
else
    echo "Warning: .env file not found"
fi

cd /Users/yipman/Downloads/github/game_arena/app

# Start the FastAPI server
echo "Starting server on port 8000..."
PYTHONPATH=/Users/yipman/Downloads/github/game_arena:$PYTHONPATH ../venv/bin/uvicorn server:app --host 0.0.0.0 --port 8000 --reload
