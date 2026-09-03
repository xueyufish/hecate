"""Audit log persistence and query.

Five files: policy (rules), writer (write path), store (storage
backend abstraction), archiver (cold storage), service (query).
"""

from __future__ import annotations
