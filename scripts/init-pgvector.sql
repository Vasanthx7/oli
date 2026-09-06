-- Enable the pgvector extension so memory embeddings can use indexed vector
-- similarity search once the embedding column is migrated to a vector type.
CREATE EXTENSION IF NOT EXISTS vector;
