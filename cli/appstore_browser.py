"""Websoft9 AppStore UI adapter. Never persist authentication state or traces."""

from __future__ import annotations

from urllib.parse import urlsplit

from .app_screenshots import Credentials, ScreenshotError, VIEWPORT

SEARCH_PLACEHOLDER = "Enter an app name to search, such as WordPress, MySQL, GitLab ..."
LOADING_PLACEHOLDER = "Loading App Store data..."
EMPTY_RESULTS_PLACEHOLDER = "No apps match the current filters"
GITHUB_APP_BASE = "https://github.com/Websoft9/docker-library/tree/main/apps/"


class AppStoreBrowser:
    def __init__(self, credentials: Credentials, *, timeout: int = 30):
        self.credentials = credentials
        self.timeout_ms = timeout * 1000
        self.playwright = None
        self.browser = None

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright, expect, TimeoutError as BrowserTimeout
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
            phase = "login_failed"
            page = self.context.new_page()
            page.goto(self.credentials.url, wait_until="domcontentloaded")
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
            appstore_link = page.get_by_role("link", name="App Store", exact=True)
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
            page.get_by_placeholder(SEARCH_PLACEHOLDER, exact=True).wait_for()
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
        # A fresh page per application prevents stale dialogs/results after failures.
        page = self.context.new_page()
        phase = "appstore_navigation_failed"
        try:
            page.goto(self.store_url, wait_until="domcontentloaded")
            search = page.get_by_placeholder(SEARCH_PLACEHOLDER, exact=True)
            search.fill(app["id"])
            search.press("Enter")
            phase = "search_results_timeout"
            # MUI's stable component class, not its generated css-* classes.
            trademark = app.get("trademark", "")
            results = (page.locator(".MuiCard-root")
                       .filter(has_not_text=LOADING_PLACEHOLDER)
                       .filter(has_not_text=EMPTY_RESULTS_PLACEHOLDER))
            card = results
            if trademark:
                card = card.filter(has=page.get_by_text(trademark, exact=True))
            empty = page.get_by_text(EMPTY_RESULTS_PLACEHOLDER, exact=True)
            try:
                # Wait for either usable candidates or an explicit empty state;
                # don't conflate a still-loading store with a nonexistent app.
                self.expect(card.or_(empty)).not_to_have_count(0, timeout=self.timeout_ms)
                if empty.is_visible():
                    raise ScreenshotError("no_search_results")
                self.expect(card).to_have_count(1, timeout=self.timeout_ms)
            except AssertionError:
                if page.get_by_text(LOADING_PLACEHOLDER, exact=True).is_visible():
                    raise ScreenshotError("search_loading_timeout") from None
                if empty.is_visible():
                    raise ScreenshotError("no_search_results") from None
                count = card.count()
                if count > 1:
                    raise ScreenshotError("multiple_matching_results") from None
                if trademark and count == 0 and results.count() > 0:
                    raise ScreenshotError("trademark_not_matched") from None
                raise ScreenshotError("search_results_timeout") from None
            if trademark:
                card.get_by_text(trademark, exact=True).click()
            else:
                # With no catalog display name, accept only one search result;
                # never guess a title or select the first of several candidates.
                card.click()
            phase = "detail_identity_failed"
            dialog = page.get_by_role("dialog")
            self.expect(dialog).to_have_count(1, timeout=self.timeout_ms)
            heading = dialog.get_by_role("heading")
            self.expect(heading.get_by_text(trademark, exact=True) if trademark else heading).to_be_visible()
            # Verify the application ID independently of its display trademark.
            self.expect(dialog.get_by_role("link", name="GitHub", exact=True)).to_have_attribute(
                "href", GITHUB_APP_BASE + app["id"], timeout=self.timeout_ms,
            )
            phase = "detail_not_ready"
            self.expect(dialog.get_by_role("button", name="Install", exact=True)).to_be_visible()
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
        except ScreenshotError:
            raise
        except Exception:
            raise ScreenshotError(phase) from None
        finally:
            page.close()
