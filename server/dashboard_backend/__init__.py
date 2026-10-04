"""Read-only Dashboard API; register its router in C's application."""

from .router import create_dashboard_router
from .service import DashboardService

__all__ = ["DashboardService", "create_dashboard_router"]
