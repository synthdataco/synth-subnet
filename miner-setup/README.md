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

Best when you want a single command to create a cloud VM that installs the miner and keeps it running across reboots.

The VM gets a static public IP, installs everything once, and runs the miner as the systemd unit `synth-miner` under the SSH user. It uses neither PM2 nor Docker, so the PM2 steps under [After Setup](#after-setup-all-options) do not apply. It serves the stock miner, or an [autoquant](https://github.com/synthdataco/autoquant) champion when `champion` is set. Each VM draws its own random seed (`SYNTH_BENCHMARK_SEED` and `PYTHONHASHSEED` in `/etc/synth-miner/miner.env`), so two VMs serving the same champion do not answer with identical paths.

### GCP

```bash
cd terraform/gcp
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your project ID and SSH key
terraform init
terraform apply
```

- The Google provider reads Application Default Credentials, not `gcloud auth login`. Run `gcloud auth application-default login`, or `export GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token)"` before each `terraform` command.
- The project needs the Compute Engine API enabled and a `default` network. Terraform creates the axon and SSH firewall rules.
- The VM turns OS Login off so that `ssh_public_key` works. An organization policy that enforces OS Login overrides it.

### AWS

```bash
cd terraform/aws
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your key pair name
terraform init
terraform apply
```

The SSH user is `ubuntu`, and the public IP is an Elastic IP.

### Variables

Both providers take these, besides the cloud settings in `terraform.tfvars.example`:

| Variable            | Default         | Meaning                                                                                  |
| ------------------- | --------------- | ---------------------------------------------------------------------------------------- |
| `name`              | `synth-miner`   | Name of the VM, its IP and its firewall rules                                            |
| `wallet_name`       | `miner`         | Wallet directory on the VM                                                               |
| `hotkey_name`       | `default`       | Hotkey file name                                                                         |
| `network`, `netuid` | `finney`, `50`  | `test` and `247` for testnet                                                             |
| `axon_port`         | `8091`          | Axon port, opened in the cloud firewall and in `ufw`                                     |
| `champion`          | `""`            | Empty serves the stock miner; `<campaign>/<leg>` (e.g. `campaign-4/opus-5-5`) a champion |
| `autoquant_ref`     | `main`          | autoquant branch or commit the champion is taken from                                    |
| `binance_api_host`  | `""`            | Set `https://data-api.binance.vision` in a US region, where `api.binance.com` is blocked |
| `ssh_source_ranges` | `["0.0.0.0/0"]` | CIDR ranges allowed to SSH in                                                            |

Outputs: `miner_ip`, `ssh_user`, `ssh_command`, `wallet_dir`, `serve_mode`, plus `instance_name` and `zone` (GCP) or `instance_id` (AWS).

### Wait for provisioning

The VM is created in seconds; the startup script then runs for several minutes. It prints `synth-miner: provisioned` when it is done, or `synth-miner: FAILED at <step>`, which it also writes to `/var/lib/synth-miner/failed`.

**GCP**, from the Terraform directory:

```bash
gcloud compute instances get-serial-port-output "$(terraform output -raw instance_name)" \
  --zone "$(terraform output -raw zone)" | grep -E 'synth-miner: (provisioned|FAILED)'
```

**AWS**:

```bash
ssh ubuntu@<miner_ip> "grep -E 'synth-miner: (provisioned|FAILED)' /var/log/cloud-init-output.log"
```

Each step can run again. After fixing the cause of a failure, rerun the script with `sudo google_metadata_script_runner startup` (GCP) or `sudo bash /var/lib/cloud/instance/user-data.txt` (AWS). Once provisioned, it does nothing on later boots.

### Add the hotkey and start the miner

Register the hotkey first (see [After Setup](#after-setup-all-options)). Copy only `coldkeypub.txt` and the hotkey file to the VM, never `coldkey`:

```bash
U=$(terraform output -raw ssh_user)
IP=$(terraform output -raw miner_ip)
W=$(terraform output -raw wallet_dir)
scp ~/.bittensor/wallets/miner/coldkeypub.txt "$U@$IP:$W/"
scp ~/.bittensor/wallets/miner/hotkeys/default "$U@$IP:$W/hotkeys/"
ssh "$U@$IP" "sudo systemctl start synth-miner"
ssh "$U@$IP" "sudo journalctl -u synth-miner -n 50 --no-pager"
```

The unit does not start until the hotkey file exists. After that it starts at every boot and restarts 15 seconds after any exit.

Optional: champions that read the event calendar need a free [FRED API key](https://fred.stlouisfed.org/docs/api/api_key.html) for US macro releases. Save it on the VM as `~/.config/synth-miner/fred_api_key` (mode 600), then `sudo systemctl restart synth-miner`.

### Change or tear down

The VM installs once. Changing a variable the startup script reads (model, wallet, network, port, Binance host) does not change the installed miner: on GCP it updates the instance metadata, and on AWS it stops and starts the instance. Changing `name`, `zone`/`region` or `disk_size_gb` recreates the VM, so run `terraform plan` first. To apply a change, recreate the VM with `terraform apply -replace=google_compute_instance.miner` (AWS: `-replace=aws_instance.miner`), then copy the hotkey again and run `sudo systemctl start synth-miner`. The IP stays the same.

Terraform keeps its state in `terraform.tfstate` next to `main.tf`. Keep it to manage or destroy the VM later.

To tear down: `terraform destroy` (this also releases the IP)

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
