"""Backward-compatible ASGI entry point for local development.

Use ``uvicorn opspilot.api.server:app --reload`` for new deployments.
This module keeps the former ``uvicorn server:app --reload`` command working
after the OpsPilot package reorganization.
"""

from opspilot.api.server import app

