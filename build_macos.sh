#!/bin/sh
# สร้างตัวติดตั้ง macOS -> dist/VideoTranscriber.dmg (ต้องรันบนเครื่อง Mac ที่มี Python 3 จาก python.org หรือ Homebrew python-tk)
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt pyinstaller
python app.py --selftest
pyinstaller --noconfirm --clean --windowed --name VideoTranscriber --collect-all imageio_ffmpeg app.py

# dmg แบบลากแอปไปโฟลเดอร์ Applications
rm -rf dist/dmg && mkdir -p dist/dmg
cp -R dist/VideoTranscriber.app dist/dmg/
ln -s /Applications dist/dmg/Applications
hdiutil create -volname "Video Transcriber" -srcfolder dist/dmg -ov -format UDZO dist/VideoTranscriber.dmg
echo "done: dist/VideoTranscriber.dmg"
