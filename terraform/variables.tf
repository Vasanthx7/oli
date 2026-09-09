variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "instance_type" {
  description = "EC2 instance type. t2.micro is universally free-tier eligible (750 hrs/mo for 12 months, 1GB RAM) — a swap file is added in cloud-init for headroom. Bump to t3.small later if Postgres + app + Chromium get tight."
  type        = string
  default     = "t2.micro"
}

variable "ssh_public_key" {
  description = "Your SSH public key (contents of e.g. ~/.ssh/id_ed25519.pub), installed for the 'ubuntu' user."
  type        = string
}

variable "allowed_ssh_cidr" {
  description = "CIDR allowed to SSH in (port 22). Restrict to your own IP (e.g. \"203.0.113.5/32\") rather than leaving it open to the world."
  type        = string
}

variable "duckdns_subdomain" {
  description = "DuckDNS subdomain, without the domain suffix (e.g. \"oli\" for oli.duckdns.org)."
  type        = string
}

variable "duckdns_token" {
  description = "DuckDNS account token, used to point the subdomain at this instance's Elastic IP."
  type        = string
  sensitive   = true
}

variable "tailscale_authkey" {
  description = "Tailscale auth key (generate a reusable, non-expiring one in the Tailscale admin console) so the instance joins your tailnet non-interactively."
  type        = string
  sensitive   = true
}

variable "home_ollama_tailscale_ip" {
  description = "Tailscale IP (100.x.y.z) of the home PC running Ollama, from `tailscale ip -4` on that machine."
  type        = string
}

variable "groq_api_key" {
  description = "Groq API key, used for the browse tool (and as chat fallback if CHAT_* is ever unset)."
  type        = string
  sensitive   = true
}

variable "postgres_password" {
  description = "Password for the oli Postgres user in production (do not reuse the docker-compose.yml dev default)."
  type        = string
  sensitive   = true
}

variable "ghcr_username" {
  description = "GitHub username used to pull the (private) oli image from GHCR."
  type        = string
  default     = "vasanthx7"
}

variable "ghcr_pat" {
  description = "GitHub Personal Access Token with `read:packages` scope, used by the instance to `docker login ghcr.io` and pull the private oli image."
  type        = string
  sensitive   = true
}
