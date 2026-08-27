# Hecate Windows installer (stub)
#
# Real Windows support is pending — for now, Windows users are encouraged to use
# WSL2 (Windows Subsystem for Linux) and run the bash installer:
#
#     wsl --install -d Ubuntu
#     curl -fsSL https://raw.githubusercontent.com/xueyufish/hecate/main/install.sh | bash
#
# Native PowerShell installer (idempotent prerequisites + uv + git clone +
# exec install.py) is tracked separately.
#
# Prereqs to do manually today on Windows:
#   - Install WSL2 + Ubuntu (recommended), or
#   - Install Python 3.12+, Git, Docker Desktop, and uv manually, then clone
#     the repo and run `uv run python install.py` from the repo root.

Write-Host "Hecate Windows installer is not yet implemented. Please use WSL2 with the bash installer:"
Write-Host "  curl -fsSL https://raw.githubusercontent.com/xueyufish/hecate/main/install.sh | bash"