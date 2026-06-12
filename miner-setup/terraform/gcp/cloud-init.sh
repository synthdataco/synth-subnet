#!/bin/bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

echo "=== Installing system dependencies ==="
apt-get update
apt-get upgrade -y
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update
apt-get install -y \
    python3.11 python3.11-venv python3.11-distutils \
    nodejs npm \
    pkg-config curl build-essential make \
    git ufw

echo "=== Installing Rust ==="
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
export PATH="/root/.cargo/bin:$PATH"

echo "=== Installing PM2 ==="
npm install -g pm2

echo "=== Configuring firewall ==="
ufw allow OpenSSH
ufw allow ${axon_port}
ufw --force enable

echo "=== Configuring swap (12GB) ==="
fallocate -l 12G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

echo "=== Cloning synth-subnet ==="
cd /opt
git clone https://github.com/mode-network/synth-subnet.git
cd synth-subnet

echo "=== Setting up Python environment ==="
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="/root/.local/bin:$PATH"
uv sync --frozen --no-dev

echo "=== Creating PM2 config ==="
cat > /opt/synth-subnet/miner.config.js << 'PMEOF'
module.exports = {
  apps: [{
    name: "miner",
    interpreter: "/opt/synth-subnet/.venv/bin/python3",
    script: "./neurons/miner.py",
    args: "--netuid ${netuid} --subtensor.network ${network} --logging.debug --logging.trace --wallet.name ${wallet_name} --wallet.hotkey default --axon.port ${axon_port} --blacklist.force_validator_permit true --blacklist.validator_min_stake 1000",
    env: {
      PYTHONPATH: ".",
    },
  }],
};
PMEOF

echo ""
echo "============================================"
echo "  Miner VM setup complete!"
echo "============================================"
echo ""
echo "Next steps:"
echo "  1. SSH into the VM"
echo "  2. Create wallet:  cd /opt/synth-subnet && source .venv/bin/activate && btcli wallet create --wallet.name ${wallet_name} --wallet.hotkey default"
echo "  3. Fund wallet with >= 0.25 TAO"
echo "  4. Register:       btcli subnet register --wallet.name ${wallet_name} --wallet.hotkey default --netuid ${netuid} --network ${network}"
echo "  5. Start miner:    pm2 start miner.config.js"
echo ""
