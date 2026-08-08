"""Authoritative runtime version for the Lians server package."""

__version__ = "0.5.0"

# Updated in lock-step with the single packaged Alembic graph head. Runtime
# readiness compares the live database to this immutable image contract.
EXPECTED_ALEMBIC_HEAD = "0064_agent_improvement_plane"
