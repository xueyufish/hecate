"""Standalone execution host for the Hecate runtime kernel.

The runner cold-starts from a profile directory (artifact manifest, host
config, identity trust material) and serves the step3 execution-backend
HTTP binding in a read-only technical-preview posture: read tools only,
server-verified identity, local append-only evidence, no control plane
and no default outbound upload.

Preview limitations, declared via the capabilities endpoint rather than
hidden: serial execution, in-memory checkpoints (no durable recovery),
no background retries, no long-task resumption, no write or approval
tools. These land in later steps (step6/7/10/11).
"""

__version__ = "0.1.0"
