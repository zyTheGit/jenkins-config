# AGENTS.md

Jenkins automation tool for triggering and monitoring builds. Contains shell scripts and Python utilities for configuration management.

## Build / Test Commands

```bash
# Run Python tests
cd jenkins-config
uv run python test/test-config.py

# Test config loader directly
uv run python load-config.py jenkins-config.json dev

# Run the main script (dry-run via --list-projects)
./jenkins-auto-build.sh --list-projects

# Build specific environment
./jenkins-auto-build.sh --env dev

# Build specific jobs
./jenkins-auto-build.sh --jobs dev:pms-biz-plan-web
```

**Note:** No lint/typecheck commands configured. Use `shellcheck` for shell scripts and `ruff` for Python if needed.

## Project Structure

```
jenkins-config/
├── jenkins-auto-build.sh    # Main shell script (entry point)
├── load-config.py           # Python config parser
├── jenkins-config.json      # Configuration file
├── jenkins-config.example.json
├── README.md / USAGE.md     # Documentation
└── test/
    └── test-config.py       # Test suite
```

## Code Style Guidelines

### Shell Script (bash)

**Imports / Sourcing:**
- Use `uv run python` for Python execution
- Config loaded via `eval "$(uv run python load-config.py ...)"`

**Formatting:**
- 4-space indentation
- Functions use `snake_case`
- Constants at top: `UPPER_CASE`
- Associative arrays: `declare -A NAME` or `declare -gA NAME` for global

**Variable Conventions:**
```bash
# Constants at top
JENKINS_URL="..."
JENKINS_TOKEN="..."

# Global associative arrays (must use -g for global in functions)
declare -gA JOBS
declare -gA JOB_BRANCHES

# Local variables in functions
local job_key="$1"
local job_path="${JOBS[$job_key]}"
```

**Error Handling:**
- Use `log_error`, `log_info`, `log_warn`, `log_success` functions
- Exit with non-zero on errors: `exit 1`
- Do NOT use `set -euo pipefail` (causes unexpected exits)

**Function Pattern:**
```bash
my_function() {
    local arg1="$1"
    local arg2="${2:-default}"
    
    [ -z "$arg1" ] && { log_error "arg1 required"; return 1; }
    
    # ... logic ...
}
```

**Key Patterns:**
- Job key format: `env_project_name` (dashes → underscores, e.g., `dev_pms_biz_plan_web`)
- URL encoding: `url_encode()` function
- CSRF token: `get_crumb()` function

### Python

**Imports:**
```python
import json
import sys
from pathlib import Path
```

**Formatting:**
- 4-space indentation
- Functions use `snake_case`
- Use f-strings for formatting
- UTF-8 encoding: `# -*- coding: utf-8 -*-`

**Error Handling:**
```python
try:
    with open(config_file) as f:
        config = json.load(f)
except FileNotFoundError:
    print(f"错误：配置文件不存在：{config_file}", file=sys.stderr)
    sys.exit(1)
except json.JSONDecodeError as e:
    print(f"错误：配置文件格式错误：{e}", file=sys.stderr)
    sys.exit(1)
```

**Output for Shell Integration:**
- Print `export VAR='value'` for shell variables
- Use `declare -gA NAME=()` for global associative arrays
- Use `file=sys.stderr` for error messages

### JSON Configuration

```json
{
  "server": { "url": "...", "token": "..." },
  "build": { "mode": "parallel", "poll_interval": 10, "build_timeout": 3600 },
  "environments": {
    "dev": {
      "default_branch": "develop",
      "params": "skip_tests=false",
      "projects": [
        { "name": "project-name", "branch": "develop", "params": "..." }
      ]
    }
  }
}
```

## Important Conventions

1. **Job Matching** (in `jenkins-auto-build.sh`):
   - `--jobs dev:project-name` → exact match for specific env
   - `--jobs project-name` → exact match across all envs (strips `_test` suffix)
   - NO fuzzy/prefix matching to avoid incorrect matches

2. **Parameter Priority** (high to low):
   - Command line `--params`
   - Project-specific `projects[].params`
   - Environment default `environments.xxx.params`
   - Global defaults (date, branch)

3. **Variable Naming**:
   - Avoid naming conflicts: Python exports `_CONFIG_SELECTED_JOBS` (not `SELECTED_JOBS`)
   - Shell uses `SELECTED_JOBS` for matched results

4. **Global Associative Arrays in Bash**:
   - When setting in functions called via `eval`, use `declare -gA` (not `declare -A`)
   - Local arrays would not persist after function returns