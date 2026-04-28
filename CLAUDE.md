# BUSN30135 Course Repository

## Overview
This is the base repository for BUSN30135, a Python-based AI/ML course. Students complete labs that run either in Google Colab or locally in VS Code/Cursor/Antigravity.

## Repository Structure
```
BUSN30135/
├── AGENTS.md           # Setup instructions for AI agents (source of truth)
├── CLAUDE.md           # This file
├── setup.py            # Makes course_utils installable as a package
├── secrets.txt         # API keys (gitignored, distributed separately)
├── .vscode/            # IDE settings (platform-agnostic base)
├── course_utils/       # Shared utilities module
│   ├── env_setup.py    # Environment initialization (Colab + local)
│   ├── setup.sh        # Mac/Linux setup script
│   └── setup.ps1       # Windows setup script
└── lab_XX/             # Individual lab folders
    ├── requirements.txt
    └── ...
```

## Key Design Decisions

### Dual Environment Support
- **Colab**: Students upload folder to Drive, run notebooks
- **Local**: Students use IDE with AI assistant following AGENTS.md

### Platform-Specific IDE Configuration
The `.vscode/settings.json` shipped with the repo is minimal. AGENTS.md Phase 4 instructs AI assistants to create platform-appropriate settings with the correct Python interpreter path.

### Python Version
Requires Python 3.10 or higher. Setup scripts check for this and install if needed.

### Secrets Management
- `secrets.txt` is gitignored
- Distributed to students separately
- Format: `KEY=VALUE` (one per line)
- Required keys: `OPENAI_API_KEY`, `GEMINI_API_KEY`

## For AI Agents

When setting up this environment:
1. Read and follow AGENTS.md exactly
2. Detect the OS and create platform-appropriate `.vscode/settings.json`
3. Use `.venv` as the virtual environment name (required)
4. Install with `pip install -e .` for the course_utils module
