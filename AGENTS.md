# AGENTS.md

Jenkins automation tool for triggering and monitoring builds. Refactored to pure Python with modular architecture.

## Build / Test Commands

```bash
# Run all tests
uv run pytest tests/ -v

# Run specific test file
uv run pytest tests/test_config.py -v

# Run the CLI directly
uv run python -m jenkins_config.cli --help

# Use shell wrapper
./jenkins-auto-build.sh --list-envs
./jenkins-auto-build.sh -i          # Interactive mode
./jenkins-auto-build.sh -e dev      # Build dev environment
```

## Package to EXE

```bash
# Install PyInstaller
uv pip install pyinstaller

# Build single exe file
uv run python build.py

# Build with directory mode (faster startup)
uv run python build.py --dir

# Clean and rebuild
uv run python build.py --clean

# Output: dist/jenkins-build.exe (~14 MB)
```

## Project Structure

```
jenkins-config/
├── jenkins-auto-build.sh       # Shell wrapper (calls Python CLI)
├── pyproject.toml              # Python project config
├── jenkins-config.json         # Configuration file
├── jenkins-config.example.json # Example config
├── build.py                    # PyInstaller build script
├── entry_point.py              # EXE entry point
├── jenkins_config/             # Python package
│   ├── __init__.py
│   ├── cli.py                  # CLI entry point
│   ├── config.py               # Config loading/parsing
│   ├── jenkins.py              # Jenkins API client
│   ├── builder.py              # Build orchestration
│   ├── history.py              # Build history persistence
│   └── utils.py                # Logging utilities
├── tests/                      # Test suite
│   ├── test_config.py
│   ├── test_jenkins.py
│   ├── test_builder.py
│   ├── test_history.py
│   └── test_utils.py
├── data/                       # Data directory
│   └── build_history.json      # Build history (generated)
└── dist/                       # Built executables (generated)
    └── jenkins-build.exe
```

## Architecture

```
cli.py (entry)
    ├── config.py (Config, Job, Environment)
    ├── jenkins.py (JenkinsClient, BuildStatus)
    ├── builder.py (Builder, BuildResult)
    ├── history.py (HistoryManager, BuildRecord)
    └── utils.py (logging functions)
```

## CLI Commands

```bash
# List environments
jenkins-build --list-envs

# List projects
jenkins-build --list-projects [ENV]

# Interactive build selection
jenkins-build -i

# Build specific environment
jenkins-build -e dev

# Build specific projects
jenkins-build -j dev:project-a,test:project-b

# View build history
jenkins-build --history

# View history statistics
jenkins-build --history-stats

# Use custom config file
jenkins-build -c /path/to/config.json --list-envs
```

## Code Style

### Python

- **Formatting**: 4-space indentation, snake_case functions
- **Type hints**: Use type annotations for function parameters and returns
- **Docstrings**: Use Chinese docstrings with Args/Returns/Example sections
- **Imports**: Standard library → Third-party → Local modules

### Key Patterns

1. **Job Key Format**: `env_project_name` (dashes → underscores)
2. **Parameter Priority**: CLI params > Project params > Environment params > Default
3. **Config Path Resolution**: 
   - Source mode: relative to project root
   - EXE mode: current working directory first, then exe directory