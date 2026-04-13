#!/bin/bash
# Setup script untuk TitanChess Poster Bot di GCP VM
set -e

echo "🔧 [1/7] Updating system..."
sudo apt-get update && sudo apt-get upgrade -y

echo "🔧 [2/7] Installing Python 3.12 + pip..."
sudo apt-get install -y python3 python3-pip python3-venv git

echo "🔧 [3/7] Creating swap file (2GB)..."
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
echo "✅ Swap aktif: $(free -h | grep Swap)"

echo "🔧 [4/7] Installing Chromium dependencies..."
sudo apt-get install -y \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 \
    libasound2t64 libxshmfence1 fonts-noto-color-emoji

echo "🔧 [5/7] Setting up project..."
mkdir -p /opt/titanchess-poster
cd /opt/titanchess-poster

python3 -m venv venv
source venv/bin/activate

pip install --upgrade pip
pip install python-telegram-bot python-dotenv requests tiktok-uploader playwright
playwright install chromium

echo "🔧 [6/7] Creating directories..."
mkdir -p videos poster

echo "🔧 [7/7] Creating systemd service..."
sudo tee /etc/systemd/system/titanchess-poster.service > /dev/null <<EOF
[Unit]
Description=TitanChess Poster Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/titanchess-poster
ExecStart=/opt/titanchess-poster/venv/bin/python bot.py
Restart=always
RestartSec=10
Environment=DISPLAY=:99

[Install]
WantedBy=multi-user.target
EOF

echo ""
echo "✅ Setup selesai!"
echo ""
echo "Langkah selanjutnya:"
echo "1. Upload file project ke /opt/titanchess-poster/"
echo "2. sudo systemctl enable titanchess-poster"
echo "3. sudo systemctl start titanchess-poster"
