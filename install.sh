#!/bin/bash
set -e

# Sunsynk Dashboard - Raspberry Pi Install Script
# Run as: bash install.sh [install_dir] [--yes]

INSTALL_DIR="${1:-/opt/sunsynk-dashboard}"
AUTO_YES=false
if [ "$2" = "--yes" ] || [ "$1" = "--yes" ]; then
    AUTO_YES=true
    [ "$1" = "--yes" ] && INSTALL_DIR="/opt/sunsynk-dashboard"
fi
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "============================================"
echo " Sunsynk Dashboard Installer"
echo "============================================"
echo ""

# ── Check platform ──────────────────────────────────────────────────────────

if ! grep -q "Raspberry Pi\|BCM" /proc/cpuinfo 2>/dev/null && ! grep -q "Raspberry" /proc/device-tree/model 2>/dev/null; then
    echo "WARNING: This doesn't appear to be a Raspberry Pi."
    echo "         Continuing anyway..."
    echo ""
fi

# ── Check Python ─────────────────────────────────────────────────────────────

echo "Checking Python..."
PYTHON=""
for cmd in python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        major=$("$cmd" -c "import sys; print(sys.version_info.major)")
        minor=$("$cmd" -c "import sys; print(sys.version_info.minor)")
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            PYTHON="$cmd"
            echo "  Using: $PYTHON ($ver)"
            break
        fi
    fi
done
if [ -z "$PYTHON" ]; then
    echo "ERROR: Python 3.10+ required. Install with: sudo apt install python3 python3-venv python3-pip"
    exit 1
fi

# ── Check system deps ────────────────────────────────────────────────────────

echo "Checking system dependencies..."
MISSING=""
for dep in curl git; do
    if ! command -v "$dep" &>/dev/null; then
        MISSING="$MISSING $dep"
    fi
done
if [ -n "$MISSING" ]; then
    echo "  Installing:$MISSING"
    sudo apt-get update -qq && sudo apt-get install -y -qq $MISSING
fi

# Ensure pip/venv available
if ! $PYTHON -m venv --help &>/dev/null 2>&1; then
    echo "  Installing python3-venv..."
    sudo apt-get install -y -qq python3-venv
fi

# ── Check serial adapter ─────────────────────────────────────────────────────

echo "Looking for RS485 USB adapter..."
SERIAL_PORT=""
for dev in /dev/ttyUSB* /dev/ttyAMA* /dev/ttyACM* /dev/serial/by-id/*; do
    if [ -e "$dev" ]; then
        SERIAL_PORT="$dev"
        echo "  Found: $SERIAL_PORT"
        break
    fi
done
if [ -z "$SERIAL_PORT" ]; then
    echo "  No USB serial adapter detected."
    echo "  Dashboard starts in SIMULATE mode. When you connect RS485,"
    echo "  go to Settings → Go Live to auto-detect and switch to live mode."
    SERIAL_PORT="/dev/ttyUSB0"
fi

# ── Copy files ───────────────────────────────────────────────────────────────

echo "Installing to $INSTALL_DIR..."
sudo mkdir -p "$INSTALL_DIR"
sudo cp -r "$SCRIPT_DIR"/* "$INSTALL_DIR/" 2>/dev/null || true
sudo chown -R "$(whoami):$(whoami)" "$INSTALL_DIR"

# Check that key files exist
for f in main.py dashboard.py database.py reader.py requirements.txt config.yaml; do
    if [ ! -f "$INSTALL_DIR/$f" ]; then
        echo "ERROR: $f not found in $SCRIPT_DIR. Run this script from the sunsynk-dashboard directory."
        exit 1
    fi
done

# ── Init git for updates ─────────────────────────────────────────────────────

if command -v git &>/dev/null; then
    cd "$INSTALL_DIR"
    if [ ! -d .git ]; then
        git init -q
        git config user.email "dashboard@localhost"
        git config user.name "Dashboard"
        git add -A
        git commit -q -m "Initial install"
        git remote add origin "https://github.com/caziques/musical-succotash" 2>/dev/null || true
    fi
fi

# ── Create venv ──────────────────────────────────────────────────────────────

echo "Creating virtual environment..."
cd "$INSTALL_DIR"
$PYTHON -m venv venv

echo "Installing Python dependencies..."
# Try online with short timeout, fall back to local wheels immediately
if venv/bin/pip install -r requirements.txt --timeout 5 -q 2>/dev/null; then
    echo "  Done (online)."
elif venv/bin/pip install --no-index --find-links="$INSTALL_DIR/wheels" -r requirements.txt -q 2>/dev/null; then
    echo "  Done (offline wheels)."
else
    echo "ERROR: Could not install dependencies."
    exit 1
fi

# ── Configure ────────────────────────────────────────────────────────────────

echo "Updating config.yaml with detected serial port..."
if [ -f "$INSTALL_DIR/config.yaml" ]; then
    sed -i "s|port:.*|port: \"$SERIAL_PORT\"|" "$INSTALL_DIR/config.yaml"
fi

# ── Create data dir ──────────────────────────────────────────────────────────

mkdir -p "$INSTALL_DIR/data"
touch "$INSTALL_DIR/data/inverter.db"

# ── Create logs dir ──────────────────────────────────────────────────────────

mkdir -p /var/log/sunsynk 2>/dev/null || mkdir -p "$INSTALL_DIR/logs"

# ── Install systemd service ──────────────────────────────────────────────────

echo ""
if $AUTO_YES; then
    INSTALL_SVC="Y"
    echo "Auto-installing systemd service."
else
    read -p "Install as systemd service to auto-start on boot? [Y/n] " -r INSTALL_SVC
    INSTALL_SVC="${INSTALL_SVC:-Y}"
fi

if [[ "$INSTALL_SVC" =~ ^[Yy] ]]; then
    SERVICE_FILE="/etc/systemd/system/sunsynk-dashboard.service"
    sudo tee "$SERVICE_FILE" > /dev/null << SYSTEMD
[Unit]
Description=Sunsynk Inverter Dashboard
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/main.py -c $INSTALL_DIR/config.yaml
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SYSTEMD

    sudo systemctl daemon-reload
    sudo systemctl enable sunsynk-dashboard

    echo ""
    if $AUTO_YES; then
        START_NOW="Y"
        echo "Auto-starting dashboard."
    else
        read -p "Start the dashboard now? [Y/n] " -r START_NOW
        START_NOW="${START_NOW:-Y}"
    fi
    if [[ "$START_NOW" =~ ^[Yy] ]]; then
        sudo systemctl start sunsynk-dashboard
        sleep 2
        if systemctl is-active --quiet sunsynk-dashboard; then
            echo "  Service started successfully."
        else
            echo "  WARNING: Service failed to start. Check: sudo journalctl -u sunsynk-dashboard -n 20"
        fi
    fi
    echo ""
    echo "Service commands:"
    echo "  sudo systemctl status sunsynk-dashboard"
    echo "  sudo systemctl restart sunsynk-dashboard"
    echo "  sudo journalctl -u sunsynk-dashboard -f"
fi

# ── Done ─────────────────────────────────────────────────────────────────────

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -z "$IP" ] && IP="<your-pi-ip>"

echo ""
echo "============================================"
echo " Installation Complete"
echo "============================================"
echo ""
echo "Dashboard:  http://$IP:8080"
echo "Install dir: $INSTALL_DIR"
echo "Serial port: $SERIAL_PORT"
echo "Config:      $INSTALL_DIR/config.yaml"
echo ""
echo "First run creates a random admin password."
echo "Find it with: journalctl -u sunsynk-dashboard | grep 'Default admin'"
echo ""
echo "To test without inverter: $INSTALL_DIR/venv/bin/python $INSTALL_DIR/main.py --simulate"
echo "============================================"
