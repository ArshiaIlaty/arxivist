"""Web UI for arxivist (FastAPI). Import create_app to build the ASGI app."""

from .server import create_app

__all__ = ["create_app"]
