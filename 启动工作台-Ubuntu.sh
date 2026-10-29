#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if python3 "$ROOT/scripts/workbench.py" start; then
  exit 0
else
  result=$?
  if [[ -t 0 ]]; then
    read -r -p "启动未完成，按回车关闭此窗口……" || true
  fi
  exit "$result"
fi
