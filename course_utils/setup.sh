#!/bin/bash

echo "Checking system configuration..."

# 1. Detect OS
OS_TYPE="$(uname -s)"
echo "Operating System: $OS_TYPE"

# 2. Check for Python 3.10+
# Try to find the best available Python version (prefer newer)
PYTHON_CMD=""
for ver in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v $ver &> /dev/null; then
        version_output=$($ver --version 2>&1)
        if [[ $version_output =~ Python\ 3\.([0-9]+) ]]; then
            minor_version="${BASH_REMATCH[1]}"
            if [ "$minor_version" -ge 10 ]; then
                PYTHON_CMD=$ver
                echo "✅ Found $version_output"
                break
            fi
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "❌ Python 3.10+ not found. Attempting install..."

    if [ "$OS_TYPE" == "Darwin" ]; then
        # Check for Homebrew
        if ! command -v brew &> /dev/null; then
            echo "Installing Homebrew first..."
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
            # Add Homebrew to PATH for this session
            if [[ -f /opt/homebrew/bin/brew ]]; then
                eval "$(/opt/homebrew/bin/brew shellenv)"  # Apple Silicon
            elif [[ -f /usr/local/bin/brew ]]; then
                eval "$(/usr/local/bin/brew shellenv)"     # Intel Mac
            fi
        fi
        brew install python@3.12
        PYTHON_CMD="python3.12"
    elif [ "$OS_TYPE" == "Linux" ]; then
        echo "Note: This script uses apt (Debian/Ubuntu)."
        echo "If you use a different package manager, install Python 3.10+ manually."
        sudo apt update && sudo apt install -y python3.12 python3.12-venv
        PYTHON_CMD="python3.12"
    fi
fi

if [ -z "$PYTHON_CMD" ]; then
    echo "❌ Could not find or install Python 3.10+. Please install manually."
    exit 1
fi

# 3. Install virtualenv
echo "Installing virtualenv tool using $PYTHON_CMD..."
$PYTHON_CMD -m pip install --upgrade pip
$PYTHON_CMD -m pip install virtualenv

echo ""
echo "✅ Done! You can now create environments using: virtualenv .venv"
