#!/bin/zsh

# Launch the UR10 Vision Control app from this project folder.
cd "$(dirname "$0")" || exit 1

if [[ ! -x ".venv/bin/python" ]]; then
  printf "找不到虛擬環境：.venv/bin/python\n"
  printf "請先完成安裝，詳見 README.md。\n"
  read -k 1 "?按任意鍵關閉..."
  exit 1
fi

exec ".venv/bin/python" "ur10_vision_qt_app.py"
