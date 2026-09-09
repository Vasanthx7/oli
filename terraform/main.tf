data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_key_pair" "oli" {
  key_name   = "oli-deploy"
  public_key = var.ssh_public_key
}

resource "aws_security_group" "oli" {
  name        = "oli-sg"
  description = "Oli: SSH (restricted) + HTTP/HTTPS (public)"

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.allowed_ssh_cidr]
  }

  ingress {
    description = "HTTP (ACME challenge + redirect to HTTPS)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "oli-sg" }
}

resource "aws_instance" "oli" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  key_name               = aws_key_pair.oli.key_name
  vpc_security_group_ids = [aws_security_group.oli.id]

  root_block_device {
    volume_type = "gp3"
    volume_size = 30 # matches the AWS free-tier EBS allowance
  }

  # Rendered once at first boot; re-run manually (see RUNBOOK) after any change
  # rather than relying on Terraform to re-apply it to a running instance.
  user_data = templatefile("${path.module}/templates/cloud-init.yaml.tftpl", {
    tailscale_authkey        = var.tailscale_authkey
    home_ollama_tailscale_ip = var.home_ollama_tailscale_ip
    groq_api_key             = var.groq_api_key
    postgres_password        = var.postgres_password
    duckdns_subdomain        = var.duckdns_subdomain
    duckdns_token            = var.duckdns_token
    ghcr_username            = var.ghcr_username
    ghcr_pat                 = var.ghcr_pat
    elastic_ip               = aws_eip.oli.public_ip
  })
  user_data_replace_on_change = true

  tags = { Name = "oli" }
}

resource "aws_eip" "oli" {
  domain = "vpc"
  tags   = { Name = "oli" }
}

resource "aws_eip_association" "oli" {
  instance_id   = aws_instance.oli.id
  allocation_id = aws_eip.oli.id
}
