"""Central TelemetryServer detection receiver."""

def create_router(*args, **kwargs):
    """Load FastAPI code only when the server application starts."""
    from .router import create_router as build_router

    return build_router(*args, **kwargs)


__all__ = ("create_router",)
