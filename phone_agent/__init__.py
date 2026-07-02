"""
Phone Agent - An AI-powered phone automation framework.

This package provides tools for automating Android and iOS phone interactions
using AI models for visual understanding and decision making.
"""

from importlib import import_module

__version__ = "0.1.0"
__all__ = ["PhoneAgent", "IOSPhoneAgent"]


def __getattr__(name):
    """Load agent classes lazily so utility subpackages stay lightweight."""
    if name == "PhoneAgent":
        return import_module("phone_agent.agent").PhoneAgent
    if name == "IOSPhoneAgent":
        return import_module("phone_agent.agent_ios").IOSPhoneAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
