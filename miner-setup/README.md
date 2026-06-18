# Synth Subnet Miner Setup

Three deployment options for running a synth-subnet miner. Choose the one that fits your setup.

## Hardware Requirements

- **CPU**: 4 cores, 3.5 GHz (min 2 cores, 2.5 GHz)
- **RAM**: 16 GB DDR4
- **Swap**: 12 GB
- **Disk**: 25 GB SSD
- **Network**: Port 8091 open (TCP), 2 Gbps download
- **GPU**: Not required
- **OS**: Ubuntu 22.04

## Prerequisites (all options)

- A Bittensor wallet funded with >= 0.25 TAO
- Wallet registered on subnet 50 (mainnet) or 247 (testnet)

---

## Option 1: Terraform (provision VM + install everything)

Best when you want a single command to create a cloud VM with everything pre-installed.

### GCP

```bash
cd terraform/gcp
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your project ID and SSH key
terraform init
terraform apply
```

### AWS

```bash
cd terraform/aws
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your key pair name
terraform init
terraform apply
```

The VM will be created in seconds, but the startup script runs in the background and takes ~5 minutes to install all dependencies.

**GCP** — monitor with:

```bash
ssh <user>@<miner_ip> "sudo journalctl -u google-startup-scripts.service -f"
```

**AWS** — monitor with:

```bash
ssh ubuntu@<miner_ip> "sudo tail -f /var/log/cloud-init-output.log"
```

Wait until you see `Miner VM setup complete!`, then SSH in and follow the printed instructions to create a wallet, register, and start the miner.

To tear down: `terraform destroy`

---

## Option 2: Docker (run on any machine with Docker)

Best when you already have a machine and want the simplest setup.

```bash
cd docker
cp .env.example .env
# Edit .env with your config

# Create wallet first (one-time)
docker compose run --rm miner btcli wallet create --wallet.name miner --wallet.hotkey default

# Fund and register the wallet, then start
docker compose up -d
docker compose logs -f
```

Wallet data persists in the `wallet-data` Docker volume.

---

## Option 3: Ansible (install on existing VM)

Best when you already have a VM and want automated software installation.

```bash
cd ansible
cp inventory.example inventory
# Edit inventory with your VM IP and config

ansible-playbook -i inventory setup-miner.yml
```

Then SSH into the VM and follow the printed instructions.

---

## After Setup (all options)

1. **Create wallet**: `btcli wallet create --wallet.name miner --wallet.hotkey default`
2. **Fund wallet** with >= 0.25 TAO
3. **Register**: `btcli subnet register --wallet.name miner --wallet.hotkey default --netuid 50`
4. **Start miner**: `pm2 start miner.config.js` (or `docker compose up -d` for Docker)
5. **Verify**: `pm2 logs miner` / check https://synthdata.co/miners/performance

First scores appear ~25 hours after first prediction.
