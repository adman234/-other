#!/bin/sh
# Installs elo-touch-fix and puts a "Calibrate Touchscreen" icon in the
# applications menu and on the desktop.
set -e
URL="https://raw.githubusercontent.com/adman234/-other/elo-touch-fix/elo-touch-fix/fix.py"
TMP="$(mktemp -d)"

# Works both from a checkout and piped from wget ("wget -qO- .../install.sh | sh").
if [ -f "$(dirname "$0")/fix.py" ]; then
    cp "$(dirname "$0")/fix.py" "$TMP/fix.py"
else
    echo "Downloading fix.py..."
    wget -qO "$TMP/fix.py" "$URL" || curl -fsSL -o "$TMP/fix.py" "$URL"
fi

echo "Installing dependencies (python3-tk, xinput, libnotify-bin)..."
sudo apt-get install -y wget python3-tk xinput libnotify-bin pkexec

sudo install -m 755 "$TMP/fix.py" /usr/local/bin/elo-touch-fix

LAUNCHER="[Desktop Entry]
Type=Application
Name=Calibrate Touchscreen
Comment=Calibrate the Elo touchscreen (fixes flipped/offset touches)
Exec=/usr/local/bin/elo-touch-fix
Icon=input-tablet
Terminal=false
Categories=Settings;HardwareSettings;"

mkdir -p "$HOME/.local/share/applications"
printf '%s\n' "$LAUNCHER" > "$HOME/.local/share/applications/elo-touch-fix.desktop"

DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [ -d "$DESKTOP_DIR" ]; then
    printf '%s\n' "$LAUNCHER" > "$DESKTOP_DIR/elo-touch-fix.desktop"
    chmod +x "$DESKTOP_DIR/elo-touch-fix.desktop"
    gio set "$DESKTOP_DIR/elo-touch-fix.desktop" metadata::trusted true 2>/dev/null || true
fi

rm -rf "$TMP"
echo
echo "Installed. Double-click 'Calibrate Touchscreen' (desktop or app menu),"
echo "or run: elo-touch-fix"
