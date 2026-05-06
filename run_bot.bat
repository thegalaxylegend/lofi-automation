@echo off
cd /d %~dp0
title Ouroboros Ingestion Bot
echo Starting Telegram Bot...
python telegram_bot.py
pause
