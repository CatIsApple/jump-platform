"""jump-site-modules: Class-based site handler framework.

Usage::

    from jump_site_modules import create_site, list_sites

    site = create_site("헬로밤", driver, "hlbam27.com", "myid", "mypw")
    result = site.login()
    jump = site.jump()
"""

from __future__ import annotations

from typing import Any, Callable

from .base import BaseSite
from .custom.bamminjok import BamminjokSite
from .custom.kakaotteok import KakaotteokSite
from .custom.opmart import OpmartSite
from .gnuboard.bamje import BamjeSite
from .gnuboard.busanbibigi import BusanbibigiSite
from .gnuboard.hellobam import HellobamSite
from .gnuboard.indal import IndalSite
from .gnuboard.lybam import LybamSite
from .gnuboard.opguide import OpguideSite
from .gnuboard.oplove import OploveSite
from .gnuboard.opmania import OpmaniaSite
from .gnuboard.opnara import OpnaraSite
from .gnuboard.opart import OpartSite
from .gnuboard.opview import OpviewSite
from .xe.albam import AlbamSite
from .xe.sexbam import SexbamSite

SITE_REGISTRY: dict[str, type[BaseSite]] = {
    "헬로밤": HellobamSite,
    "오피가이드": OpguideSite,
    "오피뷰": OpviewSite,
    "오피매니아": OpmaniaSite,
    "리밤": LybamSite,
    "외로운밤": LybamSite,
    "오피아트": OpartSite,
    "밤의제국": BamjeSite,
    "오피나라": OpnaraSite,
    "오피러브": OploveSite,
    "카카오떡": KakaotteokSite,
    "밤의민족": BamminjokSite,
    "오피마트": OpmartSite,
    "섹밤": SexbamSite,
    "인천달리기": IndalSite,
    "아이러브밤": AlbamSite,
    "부산비비기": BusanbibigiSite,
}


def create_site(
    key: str,
    driver: Any,
    domain: str,
    username: str,
    password: str,
    *,
    emit: Callable[[str, str], None] | None = None,
    captcha_api_key: str = "",
) -> BaseSite:
    """Create a site instance by Korean name key.

    Raises ``KeyError`` if *key* is not in the registry.
    """
    cls = SITE_REGISTRY.get(key)
    if cls is None:
        raise KeyError(
            f"Unknown site key: {key!r}. "
            f"Available: {', '.join(sorted(SITE_REGISTRY))}"
        )
    return cls(
        driver,
        domain,
        username,
        password,
        emit=emit,
        captcha_api_key=captcha_api_key,
    )


def list_sites() -> list[str]:
    """Return sorted list of registered site keys."""
    return sorted(SITE_REGISTRY)


def get_site_class(key: str) -> type[BaseSite] | None:
    """Return the site class for *key*, or ``None``."""
    return SITE_REGISTRY.get(key)
