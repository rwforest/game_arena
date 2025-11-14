#!/bin/bash
# Build the Game Arena frontend

cd /Users/yipman/Downloads/github/game_arena/app

echo "Building frontend..."
npm run build

echo "Copying built files to frontend/dist..."
rm -rf frontend/dist
cp -r dist frontend/dist

echo "Copying additional assets..."
mkdir -p frontend/dist/src/assets
cp frontend/src/assets/chessboard-arrow.js frontend/dist/src/assets/

echo "✓ Frontend build complete!"
echo "The chess UI is now available at http://localhost:8000/chess-ui/"
