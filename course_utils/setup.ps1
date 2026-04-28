Write-Host "Checking system configuration..." -ForegroundColor Cyan

# 1. Check for Python 3.10+
$pythonCheck = Get-Command python -ErrorAction SilentlyContinue
$pythonVersion = $null
if ($pythonCheck) {
    $versionOutput = python --version 2>&1
    if ($versionOutput -match "Python 3\.(\d+)") {
        $minorVersion = [int]$Matches[1]
        if ($minorVersion -ge 10) {
            $pythonVersion = $versionOutput
        }
    }
}

if ($pythonVersion) {
    Write-Host "✅ $pythonVersion is installed." -ForegroundColor Green
} else {
    Write-Host "❌ Python 3.10+ not found. Attempting to install via WinGet..." -ForegroundColor Yellow

    # Check if WinGet is available
    $wingetCheck = Get-Command winget -ErrorAction SilentlyContinue
    if ($wingetCheck) {
        try {
            winget install Python.Python.3.12
            Write-Host "Please restart your terminal after installation, then re-run this script." -ForegroundColor Red
            exit 0
        } catch {
            Write-Host "❌ WinGet installation failed." -ForegroundColor Red
        }
    } else {
        Write-Host "❌ WinGet is not available on this system." -ForegroundColor Red
    }

    Write-Host ""
    Write-Host "Please install Python 3.10+ manually from: https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "After installing, restart your terminal and re-run this script." -ForegroundColor Yellow
    exit 1
}

# 2. Install virtualenv
Write-Host "Installing virtualenv tool..." -ForegroundColor Cyan
python -m pip install --upgrade pip
python -m pip install virtualenv

Write-Host ""
Write-Host "✅ Done! You can now create environments using: virtualenv .venv" -ForegroundColor Green
