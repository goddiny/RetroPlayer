@echo off
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconsole --onefile --name RetroPlayer --add-data "assets;assets" --collect-submodules pynput ui.py
pause