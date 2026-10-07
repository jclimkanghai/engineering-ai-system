#!/bin/zsh
set -euo pipefail
platform_root="${0:A:h:h}"
cd "$platform_root"
platform_python="${ENGINEERING_PLATFORM_PYTHON:-python3}"
"$platform_python" -m venv .platform-venv
.platform-venv/bin/python -m pip install 'setuptools>=69' wheel
.platform-venv/bin/python -m pip install --no-build-isolation ./engineering_registry ./engineering_execution ./engineering_document_ai_brain ./engineering_ai_reviewer
.platform-venv/bin/python -m pip install --no-build-isolation '.[ai]'
.platform-venv/bin/python -m pip install 'mcp==1.30.0' 'pytest>=8,<9' 'pyyaml>=6,<7'
print "Setup complete. Double-click Start Engineering AI System.command."
