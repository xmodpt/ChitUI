"""
ChitUI Plugin System

Provides extensibility through self-contained plugins.
"""

from .base import ChitUIPlugin
from .manager import PluginManager

__all__ = ['ChitUIPlugin', 'PluginManager']
