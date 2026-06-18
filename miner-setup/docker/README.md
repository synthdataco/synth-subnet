# Synth Subnet Miner — Docker Setup

## Prerequisites

- Docker and Docker Compose installed
- At least 0.25 TAO for wallet registration

## Quick Start

```bash
# 1. Configure environment
cp .env.example .env
# Edit .env if needed (defaults are fine for mainnet)

# 2. Build the image
docker compose build

# 3. Create a wallet
docker compose run --rm miner btcli wallet create \
  --wallet.name miner --wallet.hotkey default

# 4. Fund the wallet with >= 0.25 TAO

# 5. Register on the subnet
docker compose run --rm miner btcli subnet register \
  --wallet.name miner --wallet.hotkey default --netuid 50

# 6. Start the miner
docker compose up -d

# 7. Check logs
docker compose logs -f
```

## Configuration

Edit `.env` to customize:

| Variable | Default | Description |
|----------|---------|-------------|
| `WALLET_NAME` | miner | Bittensor wallet name |
| `WALLET_HOTKEY` | default | Wallet hotkey name |
| `NETWORK` | finney | `finney` for mainnet, `test` for testnet |
| `NETUID` | 50 | `50` for mainnet, `247` for testnet |
| `AXON_PORT` | 8091 | Port for validator connections (must be open) |
| `VALIDATOR_MIN_STAKE` | 1000 | Minimum validator stake to accept requests |

## Testnet

To run on testnet, update your `.env`:

```
NETWORK=test
NETUID=247
```

Then register with:

```bash
docker compose run --rm miner btcli subnet register \
  --wallet.name miner --wallet.hotkey default \
  --network test --netuid 247
```

## Managing the Miner

```bash
# Stop
docker compose down

# Restart
docker compose restart

# View logs
docker compose logs -f

# Rebuild after upstream updates
docker compose build --no-cache
docker compose up -d
```

## Important Notes

- Port 8091 must be open and accessible from the internet for validators to reach your miner
- Wallet data persists in the `wallet-data` Docker volume — it survives container restarts
- First CRPS scores appear ~25 hours after your first prediction submission
- Monitor performance at https://synthdata.co/miners/performance
