@echo off
REM Setup script for Multi-Agent System Virtual Environment (Windows)

echo ==========================================
echo Multi-Agent System - Virtual Environment Setup
echo ==========================================

REM Check Python version
echo.
echo Checking Python version...
python --version
if %errorlevel% neq 0 (
    echo Error: Python is not installed or not in PATH
    exit /b 1
)

REM Create virtual environment
echo.
echo Creating virtual environment in .venv...
if exist .venv (
    echo Warning: Virtual environment already exists. Removing...
    rmdir /s /q .venv
)

python -m venv .venv
echo Virtual environment created

REM Activate virtual environment
echo.
echo Activating virtual environment...
call .venv\Scripts\activate.bat
echo Virtual environment activated

REM Upgrade pip
echo.
echo Upgrading pip...
python -m pip install --upgrade pip setuptools wheel
echo pip upgraded

REM Install requirements
echo.
echo Installing requirements from requirements.txt...
pip install -r requirements.txt
echo All requirements installed

echo.
echo ==========================================
echo Setup Complete!
echo ==========================================
echo.
echo To activate the virtual environment, run:
echo   .venv\Scripts\activate.bat
echo.
echo To deactivate, run:
echo   deactivate
echo.

pause

