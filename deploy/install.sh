#!/usr/bin/env bash
# Install as a systemd service on a Klipper/Moonraker host (Raspberry Pi OS, MainsailOS, ...).
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(id -un)"

rm -rf "$DIR/.venv"
if python3 -c "import ensurepip" 2>/dev/null; then
  python3 -m venv "$DIR/.venv"
else
  # No python3-venv and (on retired Debian releases) no working apt: bootstrap pip ourselves.
  echo "python3-venv not available; creating venv without pip and fetching pip directly..."
  python3 -m venv --without-pip "$DIR/.venv"
  PYV="$("$DIR/.venv/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  curl -fsSL "https://bootstrap.pypa.io/pip/$PYV/get-pip.py" -o "$DIR/.venv/get-pip.py" \
    || curl -fsSL https://bootstrap.pypa.io/get-pip.py -o "$DIR/.venv/get-pip.py"
  "$DIR/.venv/bin/python" "$DIR/.venv/get-pip.py" -q
  rm -f "$DIR/.venv/get-pip.py"
fi
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"
[ -f "$DIR/config.json" ] || cp "$DIR/config.example.json" "$DIR/config.json"

sed -e "s|__DIR__|$DIR|g" -e "s|__USER__|$USER_NAME|g" "$DIR/deploy/sample-identification.service" \
  | sudo tee /etc/systemd/system/sample-identification.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now sample-identification

HOST="$(hostname).local"
echo
echo "Running at http://$HOST:8420"
echo
echo "To add a 'Samples' link in Mainsail, save this as ~/printer_data/config/.theme/navi.json:"
cat <<EOF
[{"title": "Samples", "href": "http://$HOST:8420", "target": "_blank", "position": 90,
  "icon": "M12,2L1,21H23M12,6L19.5,19H4.5"}]
EOF
