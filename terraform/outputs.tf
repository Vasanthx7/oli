output "elastic_ip" {
  description = "Static public IP of the instance."
  value       = aws_eip.oli.public_ip
}

output "url" {
  description = "Public HTTPS URL, once DuckDNS + Caddy's cert are up."
  value       = "https://${var.duckdns_subdomain}.duckdns.org"
}

output "ssh_command" {
  description = "SSH into the instance."
  value       = "ssh ubuntu@${aws_eip.oli.public_ip}"
}
