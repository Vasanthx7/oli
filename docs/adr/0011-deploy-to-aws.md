# 11. Deploy to AWS: single EC2 instance, home Ollama over Tailscale

Date: 2026-09-09
Status: Accepted (implementation written, not yet applied/verified — no AWS account access in this session)

## Context

ADR 0007 deferred AWS in favor of a free-tier host, planning to "graduate to
k3s/AWS as a deliberate later step." The operator decided to go straight to AWS
(has free-tier credit available) rather than staging through Oracle/Fly first.

This raised three sub-decisions:

1. **Compute shape.** EC2+docker-compose vs. ECS Fargate+RDS vs. EKS. Oli is a
   single-user app; ECS/EKS add VPC/ALB/ECR/task-definition or full cluster
   machinery that buys nothing at this scale and can't easily host the local
   Ollama option (no long-lived host to run a model on).
2. **Where does the local half of the hybrid split (ADR 0010) run?** The
   operator's Ollama + GPU lives on their home PC, not in AWS. Cloud GPU
   instances are expensive, and CPU-only inference of a 7B model on a
   free-tier box would be slow and would blow past 1GB of RAM alongside
   Postgres and Chromium.
3. **Domain/TLS**, needed for Caddy's automatic HTTPS.

## Decision

- **Single EC2 instance running the existing docker-compose stack** (`app`,
  `db` = pgvector/pg16, plus a new `caddy` service), on a free-tier-eligible
  `t2.micro`. A 2GB swap file is added at boot as memory headroom.
- **Chat/memory keeps running on the home PC's Ollama, reached over
  Tailscale** — the EC2 box and the home PC join the same tailnet;
  `CHAT_BASE_URL` points at the home PC's stable Tailscale IP. No port
  forwarding, no public exposure of the home network. `browse` stays on Groq,
  unchanged from ADR 0010. This is a **geographic** extension of the existing
  hybrid split, not a new architecture: if the home PC/Tailscale link is down,
  chat fails — the same accepted trade-off ADR 0010 already made, now also
  covering network reachability, not just process uptime.
- **DuckDNS** for the hostname (`<name>.duckdns.org`), pointed at the
  instance's Elastic IP. Caddy obtains a real Let's Encrypt certificate via
  HTTP-01 — no DNS-provider-specific ACME plugin needed, no paid domain.
- **Terraform** provisions the key pair, security group (22/80/443 only),
  instance, and Elastic IP; cloud-init bootstraps Docker, Tailscale, the swap
  file, and the prod compose stack. The instance pulls a prebuilt image from
  GHCR (published by CI) rather than building locally — the tiny box never
  compiles Playwright/Chromium.
- **GHCR image stays private** (`docker login` via a scoped PAT during
  bootstrap), not made public: the final Docker image's runtime stage copies
  the full app directory, so a public image would leak source even though the
  git repo is private.

## Consequences

- A live HTTPS URL, fully codified (Terraform + cloud-init), survives an
  instance replacement.
- Cost: EC2 + storage are free for 12 months; the Elastic IP still carries
  AWS's ~$3.60/month public-IPv4 charge (not covered by any free tier).
- **RAM is tight** (1GB + 2GB swap) for Postgres + app + occasional Chromium.
  Swap absorbs spikes at a latency cost; if `browse` under load becomes
  unreliable, `instance_type` is a single Terraform variable to bump (e.g. to
  `t3.small`) — an explicitly accepted, easily-reversed risk, not a redesign.
- Home-Ollama-over-Tailscale means **two independent things must be up** for
  full functionality (the AWS box, and the home PC + its Tailscale link) —
  more moving parts than co-locating everything in one place, traded for zero
  cloud LLM-hosting cost.
- Deploying a new release is a manual step (SSH in, `docker compose pull &&
  up -d`) — auto-deploy-on-push was explicitly deferred to keep this phase's
  scope contained; see the RUNBOOK.
- Postgres backups were also explicitly deferred (fast follow, not bundled
  into this phase).
