#!/bin/sh
# Builds ~/Applications/Moto P2K.app (requires: brew install libusb)
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/Applications/Moto P2K.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$SRC/p2k.py" "$SRC/p2k_app.py" "$SRC/media.py" "$SRC/p2k_ui.html" "$APP/Contents/Resources/"
python3 -m venv "$APP/Contents/Resources/venv"
"$APP/Contents/Resources/venv/bin/pip" install -q --disable-pip-version-check -r "$SRC/requirements.txt"
cat > "$APP/Contents/MacOS/MotoP2K" <<'SH'
#!/bin/sh
R="$(cd "$(dirname "$0")/../Resources" && pwd)"
exec "$R/venv/bin/python" "$R/p2k_app.py"
SH
chmod +x "$APP/Contents/MacOS/MotoP2K"
cat > "$APP/Contents/Info.plist" <<'PL'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Moto P2K</string>
  <key>CFBundleDisplayName</key><string>Moto P2K</string>
  <key>CFBundleIdentifier</key><string>local.moto.p2k</string>
  <key>CFBundleExecutable</key><string>MotoP2K</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>LSUIElement</key><true/>
</dict></plist>
PL
echo "Done: $APP"
