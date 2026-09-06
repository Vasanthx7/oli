# 7. Deploy free-tier first, evolve orchestration later

Date: 2026-09-06
Status: Accepted

## Context

The goal includes learning DevOps end-to-end, but paying for and operating
Kubernetes from day one adds cost and complexity before the app needs it.

## Decision

Build production *practices* from the start — tests, CI/CD, containers, and
infrastructure-as-code — but **deploy onto free/cheap hosting first** (e.g. Oracle
Cloud Always-Free ARM VM or Fly.io free tier) behind Caddy with automatic HTTPS,
provisioned via Terraform. Graduate to k3s (Kubernetes) or AWS as a deliberate
later step once the app is fully containerized.

## Consequences

- Never blocked on cost or orchestration complexity; a live URL early.
- Everything is codified (Docker, Terraform), so moving to heavier orchestration
  later is additive, not a rewrite.
- The initial deploy demonstrates less orchestration depth; addressed by the
  planned k3s/AWS level-up.
