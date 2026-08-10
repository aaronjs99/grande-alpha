@echo off
title GRANDE Alpha CLI
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0cli.ps1" %*
