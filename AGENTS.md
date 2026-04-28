# Standard Operating Procedure: Environment Setup

This document serves as the **Source of Truth** for setting up the development environment for `BUSN30135`. Whether you are a student or an AI Agent, you must follow this protocol exactly to ensure consistency.

---

## Phase 1: System Discovery

Before taking action, identify the system state.

1.  **Identify Operating System**
    *   **Mac/Linux**: Run `uname -s`
        *   Output `Darwin` = macOS
        *   Output `Linux` = Linux
    *   **Windows**: Check `$PSVersionTable` in PowerShell.

2.  **Verify Python Version**
    *   Run: `python3 --version` (or `python --version` on Windows)
    *   **Requirement**: Must be `Python 3.10` or higher (3.10, 3.11, 3.12, 3.13, etc.).

---

## Phase 2: Remediation

If Phase 1 reveals that Python 3.10+ is missing or tools are not configured:

### macOS: Homebrew Prerequisite

Before running the setup script on macOS, check if Homebrew is installed:
```bash
command -v brew
```

If Homebrew is **not installed**, the setup script will fail because Homebrew installation requires admin privileges that AI agents cannot provide.

> [!NOTE]
> **AI Agents**: If Homebrew is missing, instruct the student to run this command in their terminal:
> ```bash
> /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
> ```
> Wait for the student to confirm Homebrew is installed, then continue with the setup script.

### Running the Setup Scripts

*   **macOS / Linux**: Run the auto-setup script:
    ```bash
    bash course_utils/setup.sh
    ```
    > [!NOTE]
    > The Linux script uses `apt` (Debian/Ubuntu). If you use a different package manager (Fedora, Arch, etc.), install Python 3.10+ manually.

*   **Windows**: Run the auto-setup script (Administrator PowerShell):
    ```powershell
    ./course_utils/setup.ps1
    ```
    > [!NOTE]
    > If WinGet is not available, install Python 3.10+ from [python.org](https://www.python.org/downloads/) manually.

---

## Phase 3: Initialization Protocol

Once the system is ready (Python 3.10+ present), perform the following **Immutable Sequence** inside the project root (`BUSN30135`).

### 1. Create Virtual Environment
Use `virtualenv` to create a hidden environment named `.venv`.

> [!IMPORTANT]
> You **MUST** name the directory `.venv`. The course's VS Code settings are hardcoded to look for this specific path. Using any other name (like `venv` or `env`) will break the auto-configuration.

```bash
# Mac/Linux/Windows
virtualenv .venv
```

### 2. Activate Environment
You must activate the environment before installing packages.
*   **Mac/Linux**:
    ```bash
    source .venv/bin/activate
    ```
*   **Windows**:
    ```powershell
    .venv\Scripts\activate
    ```

### 3. Install Course Utilities
Install the shared utilities package in editable mode.
```bash
pip install -e .
```

### 4. Hydrate Lab Dependencies
Install the specific requirements for the lab you are working on.
```bash
pip install -r lab_00/requirements.txt
```

---

## Phase 4: IDE Configuration

After creating the virtual environment, configure the IDE to use it. This step creates platform-specific settings so VS Code, Cursor, and Antigravity will automatically detect and use the correct Python interpreter.

> [!IMPORTANT]
> AI Agents: You **MUST** detect the operating system and create the appropriate settings file for the user's platform.

### Mac/Linux
Create or update `.vscode/settings.json`:
```json
{
    "files.exclude": {
        "**/*.egg-info": true,
        "**/__pycache__": true,
        "**/*.pyc": true,
        "**/.DS_Store": true
    },
    "python.terminal.activateEnvironment": true,
    "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python"
}
```

### Windows
Create or update `.vscode/settings.json`:
```json
{
    "files.exclude": {
        "**/*.egg-info": true,
        "**/__pycache__": true,
        "**/*.pyc": true,
        "**/.DS_Store": true
    },
    "python.terminal.activateEnvironment": true,
    "python.defaultInterpreterPath": "${workspaceFolder}/.venv/Scripts/python.exe"
}
```

> [!NOTE]
> VS Code, Cursor, and Antigravity all use the same `.vscode/settings.json` format.

---

## Reference: Virtual Env Best Practices

### 1. Creating Virtual Environments
The standard tool included with Python is **`virtualenv`** (or `venv`).
**Why?** Using a dedicated environment prevents "dependency hell"—where different projects require conflicting versions of the same library.

### 2. Naming Conventions: Use `.venv`
*   **Standardization:** Tools like VS Code and PyCharm automatically look for a folder named `.venv`.
*   **Hidden by Default:** The leading dot (`.`) keeps the folder hidden in Unix-based file explorers.
*   **Easier `.gitignore`:** Only one line needed: `.venv/`.

> [!IMPORTANT]
> **Never** check your virtual environment folder into version control (Git).

### 3. Usage and Workflow
| Step | Action | Command (macOS/Linux) | Command (Windows) |
| --- | --- | --- | --- |
| **1** | **Activate** | `source .venv/bin/activate` | `.venv\Scripts\activate` |
| **2** | **Install** | `pip install <package>` | `pip install <package>` |
| **3** | **Save** | `pip freeze > requirements.txt` | `pip freeze > requirements.txt` |
| **4** | **Deactivate** | `deactivate` | `deactivate` |

### Automating with VS Code
1.  Open a Python file.
2.  Press `Cmd/Ctrl + Shift + P` -> "Python: Select Interpreter".
3.  Select **'.venv': venv**.
VS Code will now automatically activate the environment every time you open a new terminal.
