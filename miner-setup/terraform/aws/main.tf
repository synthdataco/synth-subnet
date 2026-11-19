terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

locals {
  miner_user     = "ubuntu"
  champion_parts = var.champion == "" ? ["", ""] : split("/", var.champion)
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }
}

resource "aws_security_group" "miner" {
  name        = var.name
  description = "Security group for synth miner"

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.ssh_source_ranges
  }

  ingress {
    from_port   = var.axon_port
    to_port     = var.axon_port
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_eip" "miner" {
  domain = "vpc"

  tags = {
    Name = var.name
  }
}

resource "aws_instance" "miner" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  key_name               = var.key_pair_name
  vpc_security_group_ids = [aws_security_group.miner.id]

  root_block_device {
    volume_size = var.disk_size_gb
    volume_type = "gp3"
    iops        = 3000
  }

  user_data = templatefile("${path.module}/../gcp/cloud-init.sh", {
    miner_user        = local.miner_user
    wallet_name       = var.wallet_name
    hotkey_name       = var.hotkey_name
    network           = var.network
    netuid            = var.netuid
    axon_port         = var.axon_port
    external_ip       = aws_eip.miner.public_ip
    binance_api_host  = var.binance_api_host
    champion_campaign = local.champion_parts[0]
    champion_leg      = local.champion_parts[1]
    autoquant_ref     = var.autoquant_ref
  })

  tags = {
    Name = var.name
  }
}

resource "aws_eip_association" "miner" {
  instance_id   = aws_instance.miner.id
  allocation_id = aws_eip.miner.id
}

output "miner_ip" {
  value       = aws_eip.miner.public_ip
  description = "Elastic IP of the miner VM"
}

output "instance_id" {
  value       = aws_instance.miner.id
  description = "EC2 instance ID of the miner VM"
}

output "ssh_user" {
  value       = local.miner_user
  description = "SSH user, which also runs the miner"
}

output "ssh_command" {
  value       = "ssh ${local.miner_user}@${aws_eip.miner.public_ip}"
  description = "SSH command to connect to the miner"
}

output "wallet_dir" {
  value       = "/home/${local.miner_user}/.bittensor/wallets/${var.wallet_name}"
  description = "Wallet directory on the VM (coldkeypub.txt and hotkeys/ go here)"
}

output "serve_mode" {
  value       = var.champion == "" ? "stock" : var.champion
  description = "stock, or the autoquant champion served"
}
