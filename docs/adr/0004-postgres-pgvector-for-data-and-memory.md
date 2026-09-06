# 4. Postgres + pgvector for data and memory

Date: 2026-09-06
Status: Accepted

## Context

The prototype used SQLite with brute-force cosine similarity in NumPy for memory
recall. That is fine for a single user but does not demonstrate production data
practices, and multi-tenancy (ADR 0005) needs concurrent access and real isolation.

## Decision

Use **PostgreSQL** as the production database with the **pgvector** extension for
memory embeddings, accessed via async SQLAlchemy with **Alembic** migrations.
SQLite remains available for fast local development and tests.

## Consequences

- Industry-standard datastore; teaches migrations, connection pooling, and a real
  vector store (pgvector replaces the NumPy brute-force recall).
- Runs as a container in local `docker-compose` and as a managed/hosted instance
  in production.
- More moving parts to operate; mitigated by containerization and IaC.
