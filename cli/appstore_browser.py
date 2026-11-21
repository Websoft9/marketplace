"""Websoft9 AppStore UI adapter. Never persist authentication state or traces."""

from __future__ import annotations

from urllib.parse import urlsplit

from .app_screenshots import (
    DEFAULT_LANGUAGE, LANGUAGES, Credentials, Language, ScreenshotError, VIEWPORT,
    resolve_language,
)

SEARCH_PLACEHOLDER = LANGUAGES["en"].search_placeholder
LOADING_PLACEHOLDER = LANGUAGES["en"].loading_placeholder
EMPTY_RESULTS_PLACEHOLDER = LANGUAGES["en"].empty_results
GITHUB_APP_BASE = "https://github.com/Websoft9/docker-library/tree/main/apps/"
RETRYABLE_FAILURES = frozenset({
    "page_load_timeout", "network_error", "search_loading_timeout",
    "search_results_timeout", "detail_loading_timeout",
})


class AppStoreBrowser:
    def __init__(self, credentials: Credentials, *, timeout: int = 3,
                 language: str | Language = DEFAULT_LANGUAGE):
        self.credentials = credentials
        self.language = resolve_language(language)
        self.timeout_ms = max(1, min(timeout, 3)) * 1000
        self.last_attempts = 0
        self.playwright = None
        self.browser = None

    def _localize_status(self, route, _request):
        """Render the requested UI language without changing the stored account locale."""
        try:
            response = route.fetch()
        except Exception:
            route.continue_()
            return
        try:
            payload = response.json()
        except Exception:
            route.fulfill(response=response)
            return
        if isinstance(payload, dict) and isinstance(payload.get("current_user"), dict):
            payload["current_user"]["locale"] = self.language.id
            route.fulfill(response=response, json=payload)
        else:
            route.fulfill(response=response)

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright, expect, TimeoutError as BrowserTimeout, Error as BrowserError
        except ImportError:
            raise ScreenshotError("Playwright is missing from this Python environment; run 'make install', then 'make cli ARGS=\"tools install-browser\"'.") from None
        self.expect = expect
        phase = "browser_setup_failed"
        try:
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch()
            self.context = self.browser.new_context(
                viewport=dict(VIEWPORT), device_scale_factor=1, locale="en-US", timezone_id="UTC",
                color_scheme="light", reduced_motion="reduce", accept_downloads=False,
            )
            self.context.set_default_timeout(self.timeout_ms)
            self.context.set_default_navigation_timeout(self.timeout_ms)
            # The product stores the UI language per operator and applies it after
            # login. Overlay the requested locale on the read-only status payload
            # so localized screenshots never mutate the shared account.
            self.context.route("**/auth/status", self._localize_status)
            phase = "login_page_load_timeout"
            page = self.context.new_page()
            # Retry a failed login-page GET once, never a credential submission.
            for attempt in range(2):
                try:
                    response = page.goto(self.credentials.url, wait_until="domcontentloaded")
                    if response is not None and response.status >= 500:
                        if attempt == 0:
                            continue
                        raise ScreenshotError("login_service_failed")
                    break
                except BrowserError:
                    if attempt == 1:
                        raise
            phase = "login_failed"
            page.locator('input[type="text"]').fill(self.credentials.username)
            page.locator('input[type="password"]').fill(self.credentials.password)
            # A visible login form alone cannot distinguish rejected credentials
            # from an authenticated session whose SPA redirect has stalled.
            with page.expect_response(lambda response: (
                response.request.method == "POST" and urlsplit(response.url).path == "/api/auth/login"
            )) as login_response:
                page.get_by_role("button", name="Sign in", exact=True).click()
            response = login_response.value
            if response.status in (401, 403):
                raise ScreenshotError("login_rejected")
            if response.status == 429:
                raise ScreenshotError("login_rate_limited")
            if not response.ok:
                raise ScreenshotError("login_service_failed")
            try:
                authenticated = response.json().get("authenticated") is True
            except Exception:
                # Full-document redirects may discard the response body before
                # Playwright can read it. In that case require the actual UI.
                authenticated = None
            if authenticated is False:
                raise ScreenshotError("login_not_authenticated")
            phase = "appstore_navigation_failed"
            # Route-based lookup keeps this independent of the current UI language.
            appstore_link = page.locator('a[href="/appstore"], a[href$="/appstore"]').first
            try:
                appstore_link.wait_for(state="visible")
            except BrowserTimeout:
                if authenticated is not True:
                    raise ScreenshotError("login_response_unavailable") from None
                # One refresh rehydrates the already authenticated session. Never
                # resubmit credentials or retry rejected/rate-limited logins.
                page.reload(wait_until="domcontentloaded")
                appstore_link.wait_for(state="visible")
            appstore_link.click()
            page.wait_for_url("**/appstore*")
            # A full load re-reads the localized status payload and applies it
            # before readiness checks and every subsequent capture.
            page.goto(page.url, wait_until="domcontentloaded")
            try:
                page.get_by_placeholder(self.language.search_placeholder, exact=True).wait_for()
            except BrowserTimeout:
                other = LANGUAGES["zh-CN"] if self.language.id == "en" else LANGUAGES["en"]
                if page.get_by_placeholder(other.search_placeholder, exact=True).is_visible():
                    raise ScreenshotError("language_not_applied") from None
                raise
            self.store_url = page.url
            page.close()
            return self
        except ScreenshotError:
            self.__exit__(None, None, None)
            raise
        except Exception:
            self.__exit__(None, None, None)
            raise ScreenshotError(phase) from None

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if self.browser is not None:
                self.browser.close()
        finally:
            if self.playwright is not None:
                self.playwright.stop()

    def capture(self, app: dict[str, str]) -> bytes:
        # At most two attempts, always within the same authenticated context.
        for attempt in range(2):
            self.last_attempts = attempt + 1
            try:
                return self._capture_once(app)
            except ScreenshotError as error:
                if attempt == 1 or str(error) not in RETRYABLE_FAILURES:
                    raise

    def _select_result(self, page, results, appid: str):
        count = results.count()
        if count == 0:
            raise ScreenshotError("search_results_timeout")
        unverified = False
        for index in range(count):
            results.nth(index).click()
            dialog = page.get_by_role("dialog")
            self.expect(dialog).to_have_count(1, timeout=self.timeout_ms)
            self.expect(dialog.get_by_role("heading")).to_be_visible(timeout=self.timeout_ms)
            if count == 1:
                return dialog
            # Cards do not expose an app ID. Read each candidate's detail link
            # without installing anything; compare the exact, case-sensitive ID.
            github = dialog.get_by_role("link", name="GitHub", exact=True)
            if github.count() == 1:
                if github.get_attribute("href") == GITHUB_APP_BASE + appid:
                    return dialog
            else:
                unverified = True
            dialog.get_by_role("button", name=self.language.close_label, exact=True).click()
            self.expect(dialog).to_have_count(0, timeout=self.timeout_ms)
        raise ScreenshotError("result_id_unavailable" if unverified else "exact_id_not_found")

    def _capture_once(self, app: dict[str, str]) -> bytes:
        # A fresh page per application prevents stale dialogs/results after failures.
        page = self.context.new_page()
        phase = "page_load_timeout"
        transient_network_error = []
        page.on("requestfailed", lambda request: transient_network_error.append(True))
        page.on("response", lambda response: transient_network_error.append(True) if response.status >= 500 else None)
        try:
            response = page.goto(self.store_url, wait_until="domcontentloaded")
            if response is not None and response.status in (401, 403):
                raise ScreenshotError("session_unauthorized")
            if response is not None and response.status >= 500:
                raise ScreenshotError("network_error")
            search = page.get_by_placeholder(self.language.search_placeholder, exact=True)
            search.fill(app["id"])
            search.press("Enter")
            phase = "search_results_timeout"
            # MUI's stable component class, not its generated css-* classes.
            results = (page.locator(".MuiCard-root")
                       .filter(has_not_text=self.language.loading_placeholder)
                       .filter(has_not_text=self.language.empty_results))
            empty = page.get_by_text(self.language.empty_results, exact=True)
            try:
                # Wait for either usable candidates or an explicit empty state;
                # don't conflate a still-loading store with a nonexistent app.
                self.expect(results.or_(empty)).not_to_have_count(0, timeout=self.timeout_ms)
                if empty.is_visible():
                    raise ScreenshotError("no_search_results")
            except AssertionError:
                if page.get_by_text(self.language.loading_placeholder, exact=True).is_visible():
                    raise ScreenshotError("search_loading_timeout") from None
                if empty.is_visible():
                    raise ScreenshotError("no_search_results") from None
                raise ScreenshotError("search_results_timeout") from None
            phase = "detail_loading_timeout"
            dialog = self._select_result(page, results, app["id"])
            install_button = dialog.get_by_role("button", name=self.language.install_label, exact=True)
            self.expect(install_button).to_be_visible(timeout=self.timeout_ms)
            self.expect(dialog.locator(".MuiSkeleton-root")).to_have_count(0, timeout=self.timeout_ms)
            page.wait_for_function("""() => {
                const dialog = document.querySelector('[role="dialog"]');
                if (!dialog || document.fonts.status !== 'loaded') return false;
                const images = [...dialog.querySelectorAll('img')].filter(image => {
                    const r = image.getBoundingClientRect();
                    return r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0
                        && r.top < innerHeight && r.left < innerWidth;
                });
                return images.length > 0 && images.every(image => image.complete && image.naturalWidth > 0);
            }""", timeout=self.timeout_ms)
            phase = "screenshot_failed"
            return page.screenshot(type="png", full_page=False, animations="disabled", caret="hide")
        except ScreenshotError as error:
            if transient_network_error and str(error) in RETRYABLE_FAILURES:
                raise ScreenshotError("network_error") from None
            raise
        except Exception:
            if transient_network_error:
                raise ScreenshotError("network_error") from None
            if phase == "detail_loading_timeout" and page.get_by_role("dialog").locator("img").evaluate_all(
                "images => images.some(image => image.complete && image.naturalWidth === 0)"
            ):
                raise ScreenshotError("detail_image_failed") from None
            raise ScreenshotError(phase) from None
        finally:
            page.close()
