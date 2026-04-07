from __future__ import annotations

import logging
import threading
import traceback
from typing import Any

logger = logging.getLogger(__name__)


class BrowserManager:
    """단일 WebDriver를 생성/재사용하는 매니저.

    - 원본 jump.exe(디컴파일)는 seleniumbase.Driver(uc=True) 기반으로 Cloudflare/로봇 페이지를
      최대한 회피/통과하도록 구성되어 있다.
    - 이 프로젝트는 외부 의존성을 최소화하기 위해 Selenium을 기본으로 두되,
      seleniumbase가 설치되어 있으면 원본과 최대한 동일하게 seleniumbase(uc) 드라이버를 사용한다.
    """

    def __init__(self, *, headless: bool = False) -> None:
        self.headless = bool(headless)
        self._lock = threading.RLock()
        self._driver: Any | None = None

    def get_driver(self) -> Any:
        with self._lock:
            if self._driver is not None:
                return self._driver

            driver = None

            # 1) seleniumbase(uc) 우선: 원본과 최대한 동일
            try:
                from seleniumbase import Driver  # type: ignore
                from selenium.webdriver.chrome.options import Options  # type: ignore

                chrome_options = Options()
                chrome_options.add_argument("--start-maximized")
                chrome_options.set_capability("goog:loggingPrefs", {"browser": "INFO"})

                driver = Driver(
                    uc=True,
                    browser="chrome",
                    proxy_bypass_list="*",
                    # 원본은 headless=False (차단 회피 목적). 사용자가 headless를 켰다면 그대로 전달한다.
                    headless=self.headless,
                    cap_string=chrome_options.to_capabilities(),
                )
                try:
                    driver.execute_script(
                        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                    )
                except Exception:
                    pass

                try:
                    driver.set_window_size(1920, 1080)
                except Exception:
                    pass

                # 원본은 드라이버 준비 후 구글로 워밍업 + 캡차 GUI 클릭을 호출한다.
                try:
                    driver.uc_open_with_reconnect("https://google.com", reconnect_time=10)
                    driver.uc_gui_click_captcha()
                except Exception:
                    pass
            except Exception as sb_exc:
                logger.debug("seleniumbase 사용 불가: %s", sb_exc)
                driver = None

            # 2) Selenium fallback: 설치만으로 동작해야 하므로 기본 드라이버 사용
            if driver is None:
                try:
                    from selenium import webdriver  # type: ignore
                    from selenium.webdriver.chrome.options import Options  # type: ignore
                except Exception as exc:  # noqa: BLE001
                    logger.error("selenium import 실패:\n%s", traceback.format_exc())
                    raise RuntimeError(
                        "브라우저 드라이버를 만들 수 없습니다. selenium 또는 seleniumbase 설치를 확인하세요."
                        f"\n원인: {exc}"
                    ) from exc

                options = Options()
                options.add_argument("--start-maximized")
                # 자동화 탐지 완화(원본 대비 약함)
                options.add_argument("--disable-blink-features=AutomationControlled")
                options.add_experimental_option("excludeSwitches", ["enable-automation"])
                options.add_experimental_option("useAutomationExtension", False)
                if self.headless:
                    options.add_argument("--headless=new")

                try:
                    driver = webdriver.Chrome(options=options)
                except Exception as drv_exc:
                    logger.error("Chrome 드라이버 생성 실패:\n%s", traceback.format_exc())
                    raise RuntimeError(
                        f"Chrome 브라우저를 시작할 수 없습니다: {drv_exc}"
                    ) from drv_exc

                try:
                    driver.set_window_size(1920, 1080)
                except Exception:
                    pass

                # navigator.webdriver 숨김(새 문서에 주입)
                try:
                    driver.execute_cdp_cmd(
                        "Page.addScriptToEvaluateOnNewDocument",
                        {
                            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                        },
                    )
                except Exception:
                    pass

            self._driver = driver
            return driver

    def is_alive(self) -> bool:
        """드라이버가 유효한지 확인 (창이 닫혔거나 크래시 시 False)."""
        with self._lock:
            if self._driver is None:
                return False
            try:
                _ = self._driver.current_url
                return True
            except Exception:
                return False

    def reset(self) -> None:
        """기존 드라이버를 정리하고 다음 get_driver() 호출 시 새로 생성되도록 초기화."""
        with self._lock:
            if self._driver is not None:
                try:
                    self._driver.quit()
                except Exception:
                    pass
                self._driver = None

    def quit(self) -> None:
        with self._lock:
            if self._driver is None:
                return
            try:
                self._driver.quit()
            except Exception:
                pass
            finally:
                self._driver = None
