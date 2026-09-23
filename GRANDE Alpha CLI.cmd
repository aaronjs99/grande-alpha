@echo off
title GRANDE Alpha CLI
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0grande.ps1" cli %*
