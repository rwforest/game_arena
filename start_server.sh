#!/bin/bash
# Game Arena Server Startup Script
# This script starts both the backend API and serves the integrated chess UI

# Load environment variables from .env file
if [ -f /Users/yipman/Downloads/github/game_arena/.env ]; then
    export $(cat /Users/yipman/Downloads/github/game_arena/.env | grep -v '^#' | xargs)
    echo "Loaded environment variables from .env"
else
    echo "Warning: .env file not found"
fi

cd /Users/yipman/Downloads/github/game_arena/app

# Start the FastAPI server
PYTHONPATH=/Users/yipman/Downloads/github/game_arena:$PYTHONPATH ../venv/bin/uvicorn server:app --host 0.0.0.0 --port 8000 --reload
