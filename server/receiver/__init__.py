"""Central TelemetryServer detection receiver."""

from .models import DetectionResult


def create_router(*args, **kwargs):
    """Load FastAPI code only when the server application starts."""
    from .router import create_router as build_router

    return build_router(*args, **kwargs)


__all__ = ("DetectionResult", "create_router")
