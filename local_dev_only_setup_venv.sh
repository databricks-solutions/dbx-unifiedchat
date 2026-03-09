#!/bin/bash
# Setup script for Multi-Agent System Virtual Environment

set -e  # Exit on error

echo "=========================================="
echo "Multi-Agent System - Virtual Environment Setup"
echo "=========================================="

# Check Python version
echo ""
echo "Checking Python version..."
python_version=$(python3 --version 2>&1 | awk '{print $2}')
echo "✓ Found Python $python_version"

# Check if Python 3.11+ is installed
required_version="3.11"
if [ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" != "$required_version" ]; then
    echo "✗ Error: Python 3.11 or higher is required"
    echo "  Current version: $python_version"
    exit 1
fi

# Create virtual environment
echo ""
echo "Creating virtual environment in .venv..."
if [ -d ".venv" ]; then
    echo "⚠ Virtual environment already exists. Removing..."
    rm -rf .venv
fi

python3 -m venv .venv
echo "✓ Virtual environment created"

# Activate virtual environment
echo ""
echo "Activating virtual environment..."
source .venv/bin/activate
echo "✓ Virtual environment activated"

# Upgrade pip
echo ""
echo "Upgrading pip..."
pip install --upgrade pip setuptools wheel
echo "✓ pip upgraded"

# Install requirements
echo ""
echo "Installing requirements from requirements.txt..."
pip install -r requirements.txt
echo "✓ All requirements installed"

# Verify key packages
echo ""
echo "Verifying key packages..."
python3 << 'EOF'
import sys
packages = [
    'langgraph_supervisor',
    'mlflow',
    'databricks_langchain',
    'databricks.sdk',
    'databricks.vector_search',
    'pydantic',
    'dotenv',
    'pandas'
]

missing = []
for pkg in packages:
    try:
        __import__(pkg.replace('-', '_'))
        print(f"  ✓ {pkg}")
    except ImportError:
        print(f"  ✗ {pkg} - MISSING")
        missing.append(pkg)

if missing:
    print(f"\n⚠ Warning: {len(missing)} package(s) could not be imported")
    sys.exit(1)
else:
    print("\n✓ All key packages verified")
EOF

echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "To activate the virtual environment, run:"
echo "  source .venv/bin/activate"
echo ""
echo "To deactivate, run:"
echo "  deactivate"
echo ""
echo "To verify installation:"
echo "  python -c 'import langgraph_supervisor; import mlflow; print(\"All imports successful!\")'"
echo ""

