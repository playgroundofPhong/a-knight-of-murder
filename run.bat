@echo off
title A Knight of Murder - Server Launcher
color 0E

echo ========================================================
echo       A KNIGHT OF MURDER - MEDIEVAL MYSTERY
echo ========================================================
echo Starting local game server...
echo.

start "" "http://localhost:8000"
python server.py

pause
