variable "name" {
  description = "Name of the security group, and Name tag of the VM and its Elastic IP"
  type        = string
  default     = "synth-miner"
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "eu-west-1"
}

variable "instance_type" {
  description = "EC2 instance type (min 4 vCPU, 16GB RAM)"
  type        = string
  default     = "t3.xlarge"
}

variable "disk_size_gb" {
  description = "Root volume size in GB"
  type        = number
  default     = 30
}

variable "key_pair_name" {
  description = "Name of existing EC2 key pair for SSH access"
  type        = string
}

variable "ssh_source_ranges" {
  description = "CIDR ranges allowed to SSH into the VM"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "wallet_name" {
  description = "Bittensor wallet name"
  type        = string
  default     = "miner"
}

variable "hotkey_name" {
  description = "Bittensor hotkey name"
  type        = string
  default     = "default"
}

variable "network" {
  description = "Bittensor network (finney or test)"
  type        = string
  default     = "finney"
}

variable "netuid" {
  description = "Subnet UID"
  type        = number
  default     = 50
}

variable "axon_port" {
  description = "Miner axon port"
  type        = number
  default     = 8091
}

variable "binance_api_host" {
  description = "BINANCE_API_HOST for the miner, e.g. https://data-api.binance.vision in a US region (empty: api.binance.com)"
  type        = string
  default     = ""

  validation {
    condition     = var.binance_api_host == "" || can(regex("^https://[A-Za-z0-9.-]+$", var.binance_api_host))
    error_message = "binance_api_host must be empty or a bare https:// origin, e.g. https://data-api.binance.vision."
  }
}

variable "champion" {
  description = "autoquant champion to serve as <campaign>/<leg>, e.g. campaign-4/opus-5-5 (empty: the stock miner)"
  type        = string
  default     = ""

  validation {
    condition     = var.champion == "" || can(regex("^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$", var.champion))
    error_message = "champion must be empty or <campaign>/<leg>, e.g. campaign-4/opus-5-5."
  }
}

variable "autoquant_ref" {
  description = "autoquant git ref (branch or commit sha) to serve the champion from"
  type        = string
  default     = "main"
}
