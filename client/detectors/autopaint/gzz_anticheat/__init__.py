"""GZZ client-side anti-cheat sensors and detectors."""

from .detector import AutoPaintDetector
from .models import CommonEvent

__all__ = ["AutoPaintDetector", "CommonEvent"]
__version__ = "0.4.1"
