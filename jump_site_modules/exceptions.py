"""Site modules exception hierarchy."""

from __future__ import annotations


class SiteError(Exception):
    """Base exception for site module errors."""


class LoginError(SiteError):
    """Login failed."""


class CaptchaError(SiteError):
    """Captcha challenge could not be solved."""


class JumpError(SiteError):
    """Jump execution failed."""


class NavigationError(SiteError):
    """Page navigation failed."""


class ParseError(SiteError):
    """Page content parsing failed."""
