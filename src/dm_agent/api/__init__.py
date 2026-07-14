"""Human-facing management API (M2b): campaign/character CRUD and world upload.

This is the admin layer around the buckets M1 built — distinct from the agent
*tools* (which the LLM calls) and from the websocket event stream (the play
surface). The stage's management sidebar drives these routes.
"""

from dm_agent.api.management import router

__all__ = ["router"]
