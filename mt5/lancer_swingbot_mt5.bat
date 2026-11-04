@echo off
REM Lancement quotidien du Swingbot v2 sur MetaTrader 5 (compte DEMO).
REM A planifier chaque jour de semaine vers 23h15 (heure de Paris) avec le Planificateur de taches Windows.
REM Le terminal MT5 doit etre ouvert et connecte au compte DEMO.
cd /d "%~dp0\.."
call venv\Scripts\activate.bat
set SWINGBOT_BROKER=mt5
set SWINGBOT_MODE=demo
set SWINGBOT_SYSTEM=v4
set SWINGBOT_CAPITAL=10000
set NEWS_LLM=none
python -m swingbot.live >> logs\lancement.log 2>&1
