# course_utils Module

## Purpose
Shared utilities for BUSN30135 labs that handle environment setup for both Google Colab and local development.

## Files

### env_setup.py
Main module with functions for environment initialization.

**Key Functions:**
- `init()` - Detects environment (Colab vs local), mounts Drive if Colab, loads secrets into `os.environ`
- `setup_lab(lab_name)` - One-stop setup for notebooks: calls `init()`, changes to lab directory, installs requirements
- `parse_secrets_file(path)` - Parses KEY=VALUE format secrets files
- `find_and_load_secrets(base_path)` - Searches for secrets*.txt files

**Colab Detection:**
```python
IS_COLAB = 'google.colab' in sys.modules
```

**Important:** `setup_lab()` uses IPython `%pip` magic for requirements installation. This only works in Jupyter/Colab environments, not when running `python script.py` directly.

### setup.sh (Mac/Linux)
- Checks for Python 3.10+
- Installs via Homebrew (macOS) or apt (Debian/Ubuntu)
- Installs virtualenv

### setup.ps1 (Windows)
- Checks for Python 3.10+
- Attempts install via WinGet with fallback instructions
- Installs virtualenv using `python -m pip`

## Configuration
`COURSE_DRIVE_ROOT = 'MyDrive/BUSN30135'` - Expected path in Google Drive for Colab users.
