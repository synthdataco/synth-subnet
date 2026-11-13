terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

locals {
  champion_parts = var.champion == "" ? ["", ""] : split("/", var.champion)

  startup_script = templatefile("${path.module}/cloud-init.sh", {
    miner_user        = var.ssh_user
    wallet_name       = var.wallet_name
    hotkey_name       = var.hotkey_name
    network           = var.network
    netuid            = var.netuid
    axon_port         = var.axon_port
    external_ip       = google_compute_address.miner.address
    binance_api_host  = var.binance_api_host
    champion_campaign = local.champion_parts[0]
    champion_leg      = local.champion_parts[1]
    autoquant_ref     = var.autoquant_ref
  })
}

resource "google_compute_address" "miner" {
  name   = "${var.name}-ip"
  region = var.region
}

resource "google_compute_firewall" "miner_axon" {
  name    = "${var.name}-axon"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = [tostring(var.axon_port)]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = [var.name]
}

resource "google_compute_firewall" "miner_ssh" {
  name    = "${var.name}-ssh"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = var.ssh_source_ranges
  target_tags   = [var.name]
}

resource "google_compute_instance" "miner" {
  name                      = var.name
  machine_type              = var.machine_type
  zone                      = var.zone
  tags                      = [var.name]
  allow_stopping_for_update = true

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2204-lts"
      size  = var.disk_size_gb
      type  = "pd-ssd"
    }
  }

  network_interface {
    network = "default"
    access_config {
      nat_ip = google_compute_address.miner.address
    }
  }

  # startup-script (not metadata_startup_script) updates in place.
  metadata = {
    enable-oslogin = "FALSE"
    ssh-keys       = "${var.ssh_user}:${var.ssh_public_key}"
    startup-script = local.startup_script
  }
}

output "miner_ip" {
  value       = google_compute_address.miner.address
  description = "Static public IP of the miner VM"
}

output "instance_name" {
  value       = google_compute_instance.miner.name
  description = "Name of the miner VM"
}

output "zone" {
  value       = google_compute_instance.miner.zone
  description = "Zone of the miner VM"
}

output "ssh_user" {
  value       = var.ssh_user
  description = "SSH user, which also runs the miner"
}

output "ssh_command" {
  value       = "ssh ${var.ssh_user}@${google_compute_address.miner.address}"
  description = "SSH command to connect to the miner"
}

output "wallet_dir" {
  value       = "/home/${var.ssh_user}/.bittensor/wallets/${var.wallet_name}"
  description = "Wallet directory on the VM (coldkeypub.txt and hotkeys/ go here)"
}

output "serve_mode" {
  value       = var.champion == "" ? "stock" : var.champion
  description = "stock, or the autoquant champion served"
}
