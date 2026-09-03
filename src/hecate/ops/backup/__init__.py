"""Backup engine — PostgreSQL / MinIO / S3 / Qdrant / filesystem.

Twelve files: factory + per-backend drivers (pg_backup, fs_backup,
minio_backup, qdrant_backup, minio_storage, s3_storage) +
orchestrator + scheduler + restore + verification + storage.
"""

from __future__ import annotations
