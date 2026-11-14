@echo off
title OrbitNexus - Autonomous Astronaut Health Monitoring System
echo.
echo  ╔════════════════════════════════════════════════════════════════════╗
echo  ║   ___       _     _ _     _   _                      _            ║
echo  ║  / _ \ _ __| |__ (_) |_  | \ | | _____  ___   _ ___  ^| ^|           ║
echo  ║ ^| ^| ^| ^| '__^| '_ \^| ^| __^| ^|  \^| ^|/ _ \ \/ / ^| ^| / __^| ^| ^|           ║
echo  ║ ^| ^|_^| ^| ^|  ^| ^|_^) ^| ^| ^|_  ^| ^|\  ^|  __/^>  ^<^| ^|_^| \__ \ ^|_^|           ║
echo  ║  \___/^|_^|  ^|_.__/^|_^|\__^| ^|_^| \_^|\___/_/\_\\__,_^|___/ ^(_^)           ║
echo  ║                                                                    ║
echo  ║  Autonomous Astronaut Health Monitoring System                     ║
echo  ║  NASA Space Apps Hackathon 2026                                    ║
echo  ╚════════════════════════════════════════════════════════════════════╝
echo.

if "%GROQ_API_KEY%"=="" (
    echo  [!] GROQ_API_KEY not set. OrbitNexus AI will remain offline until a valid key is configured.
    echo  [!] Set LLM_PROVIDER=groq and GROQ_API_KEY to enable Groq chat.
    echo.
) else (
    echo  [+] GROQ_API_KEY configured - OrbitNexus AI Assistant online!
    echo.
)

echo  [*] Starting OrbitNexus Mission Control Server...
echo  [*] Dashboard will be accessible at: http://localhost:5000
echo.

cd /d "%~dp0"
call venv\Scripts\activate
cd ASTRA\website
python app.py

pause
