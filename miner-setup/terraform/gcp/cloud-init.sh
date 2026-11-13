#!/bin/bash
# Rendered by Terraform templatefile: write bash variables as $VAR, no braces.
# Installs once, then leaves the miner to the synth-miner systemd unit.
set -euo pipefail

MINER_USER='${miner_user}'
WALLET_NAME='${wallet_name}'
HOTKEY_NAME='${hotkey_name}'
NETWORK='${network}'
NETUID='${netuid}'
AXON_PORT='${axon_port}'
EXTERNAL_IP='${external_ip}'
BINANCE_API_HOST='${binance_api_host}'
CHAMPION_CAMPAIGN='${champion_campaign}'
CHAMPION_LEG='${champion_leg}'
AUTOQUANT_REF='${autoquant_ref}'

STATE_DIR=/var/lib/synth-miner

# GCP runs the startup script on every boot.
if [ -f "$STATE_DIR/provisioned" ]; then
    echo "synth-miner: already provisioned"
    exit 0
fi

STEP=start
on_exit() {
    local rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "synth-miner: FAILED at $STEP"
        mkdir -p "$STATE_DIR"
        echo "$STEP" > "$STATE_DIR/failed"
    fi
}
trap on_exit EXIT

step() {
    STEP=$1
    echo "synth-miner: step $STEP"
}

as_miner() {
    runuser -u "$MINER_USER" -- "$@"
}

export DEBIAN_FRONTEND=noninteractive
export HOME=/root
cd /
mkdir -p "$STATE_DIR"
rm -f "$STATE_DIR/failed"

if [ -n "$CHAMPION_CAMPAIGN" ]; then
    MODE=champion
else
    MODE=stock
fi

step packages
apt-get -o DPkg::Lock::Timeout=600 update
apt-get -o DPkg::Lock::Timeout=600 install -y \
    git git-lfs curl ufw ca-certificates

step swap
if ! grep -q '^/swapfile[[:space:]]' /proc/swaps \
    && ! grep -q '^/swapfile[[:space:]]' /etc/fstab; then
    fallocate -l 12G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

step firewall
ufw allow OpenSSH
ufw allow "$AXON_PORT/tcp"
ufw --force enable

step user
# On GCP the guest agent may create the user from the ssh-keys metadata.
if ! id -u "$MINER_USER" > /dev/null 2>&1; then
    useradd -m -s /bin/bash "$MINER_USER" || id -u "$MINER_USER" > /dev/null
fi
# sudo ignores sudoers.d files whose name contains a dot.
SUDOERS=/etc/sudoers.d/90-synth-miner
echo "$MINER_USER ALL=(ALL) NOPASSWD:ALL" > "$SUDOERS.tmp"
chmod 0440 "$SUDOERS.tmp"
visudo -cf "$SUDOERS.tmp"
mv "$SUDOERS.tmp" "$SUDOERS"
MINER_GROUP=$(id -gn "$MINER_USER")
MINER_HOME=/home/$MINER_USER
install -d -m 0700 -o "$MINER_USER" -g "$MINER_GROUP" \
    "$MINER_HOME/.bittensor" \
    "$MINER_HOME/.bittensor/wallets" \
    "$MINER_HOME/.bittensor/wallets/$WALLET_NAME" \
    "$MINER_HOME/.bittensor/wallets/$WALLET_NAME/hotkeys" \
    "$MINER_HOME/.config" \
    "$MINER_HOME/.config/synth-miner"

step uv
if [ ! -x /usr/local/bin/uv ]; then
    curl -LsSf https://astral.sh/uv/install.sh \
        | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
fi

if [ "$MODE" = stock ]; then
    step synth-subnet
    install -d -o "$MINER_USER" -g "$MINER_GROUP" /opt/synth-subnet
    if [ ! -d /opt/synth-subnet/.git ]; then
        as_miner git clone https://github.com/synthdataco/synth-subnet \
            /opt/synth-subnet
    fi
    cd /opt/synth-subnet
    as_miner uv sync --frozen --no-dev
else
    step autoquant
    install -d -o "$MINER_USER" -g "$MINER_GROUP" /opt/autoquant
    cd /opt/autoquant
    if [ ! -d .git ]; then
        as_miner env GIT_LFS_SKIP_SMUDGE=1 git clone --filter=blob:none \
            https://github.com/synthdataco/autoquant /opt/autoquant
    fi
    as_miner env GIT_LFS_SKIP_SMUDGE=1 \
        git -c advice.detachedHead=false checkout "$AUTOQUANT_REF"

    step champion-files
    LEG_DIR=campaign_results/$CHAMPION_CAMPAIGN/$CHAMPION_LEG
    test -d "$LEG_DIR"
    as_miner git lfs install --local
    as_miner git lfs pull --include "$LEG_DIR/**"
    LFS_SPEC='version https://git-lfs.github.com/spec/'
    POINTERS=$(find "$LEG_DIR" -type f -size -1024c -print0 \
        | while IFS= read -r -d '' f; do
            if [ "$(head -c 40 "$f" | tr -d '\000')" = "$LFS_SPEC" ]; then
                echo "$f"
            fi
        done)
    if [ -n "$POINTERS" ]; then
        echo "synth-miner: Git LFS pointers left in $LEG_DIR:"
        echo "$POINTERS"
        exit 1
    fi

    step champion-env
    # The unpacked entrypoint runs python3.12.
    as_miner uv sync --frozen --no-dev --python 3.12

    step unpack
    as_miner rm -rf champions/champion
    as_miner uv run --no-sync python -m synth_lib.serving.unpack_champion \
        --campaign "$CHAMPION_CAMPAIGN" --leg "$CHAMPION_LEG" --name champion
fi

step config
install -d -m 0755 /etc/synth-miner
# Written once: the seed stays fixed for the life of the VM.
if [ ! -f /etc/synth-miner/miner.env ]; then
    SEED=$(od -An -N4 -tu4 /dev/urandom | tr -d ' ')
    {
        echo "MODE=$MODE"
        echo "NETWORK=$NETWORK"
        echo "NETUID=$NETUID"
        echo "WALLET_NAME=$WALLET_NAME"
        echo "COLDKEY_NAME=$WALLET_NAME"
        echo "HOTKEY_NAME=$HOTKEY_NAME"
        echo "AXON_PORT=$AXON_PORT"
        echo "PORT=$AXON_PORT"
        echo "BT_AXON_EXTERNAL_IP=$EXTERNAL_IP"
        if [ -n "$BINANCE_API_HOST" ]; then
            echo "BINANCE_API_HOST=$BINANCE_API_HOST"
        fi
        echo "SYNTH_BENCHMARK_SEED=$SEED"
        echo "PYTHONHASHSEED=$SEED"
    } > /etc/synth-miner/miner.env.tmp
    chmod 0644 /etc/synth-miner/miner.env.tmp
    mv /etc/synth-miner/miner.env.tmp /etc/synth-miner/miner.env
fi

step launcher
install -d -m 0755 /opt/synth-miner
cat > /opt/synth-miner/run.sh << 'EOF'
#!/bin/bash
# Started by synth-miner.service with /etc/synth-miner/miner.env.
set -euo pipefail

FRED_FILE=$HOME/.config/synth-miner/fred_api_key
if [ -f "$FRED_FILE" ]; then
    FRED_API_KEY=$(cat "$FRED_FILE")
    export FRED_API_KEY
fi

case "$MODE" in
    stock)
        cd /opt/synth-subnet
        export PYTHONPATH=.
        exec .venv/bin/python neurons/miner.py \
            --netuid "$NETUID" \
            --subtensor.network "$NETWORK" \
            --wallet.name "$WALLET_NAME" \
            --wallet.hotkey "$HOTKEY_NAME" \
            --axon.port "$AXON_PORT" \
            --logging.debug
        ;;
    champion)
        cd /opt/autoquant
        export UV_NO_SYNC=1
        exec champions/champion/entrypoint.sh
        ;;
esac
echo "unknown MODE: $MODE" >&2
exit 1
EOF
chmod 0755 /opt/synth-miner/run.sh

step service
# Started once the hotkey is on the VM: systemctl start synth-miner.
cat > /etc/systemd/system/synth-miner.service << EOF
[Unit]
Description=Synth subnet miner
Wants=network-online.target
After=network-online.target
ConditionPathExists=$MINER_HOME/.bittensor/wallets/$WALLET_NAME/hotkeys/$HOTKEY_NAME

[Service]
User=$MINER_USER
EnvironmentFile=/etc/synth-miner/miner.env
ExecStart=/opt/synth-miner/run.sh
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable synth-miner

STEP=done
touch "$STATE_DIR/provisioned"
echo "synth-miner: provisioned"
