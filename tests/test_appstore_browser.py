"""Optional Chromium tests using synthetic pages, never a live server or credentials.

Run with MK_TEST_SCREENSHOT_BROWSER=1 after installing .[screenshots] and Chromium.
"""

from __future__ import annotations

import os
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

from cli.app_screenshots import Credentials, ScreenshotError, validate_png
from cli.appstore_browser import AppStoreBrowser, SEARCH_PLACEHOLDER


LOGIN = """<!doctype html><html lang="en"><body data-mode="MODE">
<input type="text"><input type="password">
<button onclick="signIn()">Sign in</button>
<script>
async function signIn() {
  const mode=document.body.dataset.mode;
  const response=await fetch('/api/auth/login?mode='+mode,{method:'POST'});
  const result=await response.json();
  if(result.authenticated && mode!=='stalled') location.href='/home';
}
</script>
</body></html>"""
HOME = '<!doctype html><html><body><a href="/appstore">App Store</a></body></html>'
STORE = """<!doctype html><html lang="en"><body>
<input placeholder="SEARCH_PLACEHOLDER" oninput="search(this.value)">
<div id="results"></div><div id="details"></div>
<script>
const titles={n8n:'n8n',mysql:'MySQL',ffmpeg:'FFmpeg',wrong:'Wrong',broken:'Broken',
  wordpress:'Same label',wordpresspro:'Same label',wordpressmultisite:'Same label',
  'similar-pro':'Similar','similarpro':'Similar'};
function search(id) {
  document.querySelector('#details').innerHTML='';
  if(id==='wordpress' || id==='similar') {
    const ids=id==='wordpress' ? ['wordpresspro','wordpress','wordpressmultisite'] : ['similar-pro','similarpro'];
    document.querySelector('#results').innerHTML=ids.map(key=>
      `<div class="MuiCard-root" onclick="detail('${key}')"><p>${titles[key]}</p></div>`).join('');
    return;
  }
  if(id==='loading') {
    document.querySelector('#results').innerHTML='<div class="MuiCard-root">Loading App Store data...</div>';
    return;
  }
  if(id==='blank') {
    document.querySelector('#results').innerHTML='';
    return;
  }
  const title=titles[id];
  if(!title) {
    document.querySelector('#results').innerHTML='<div class="MuiCard-root">No apps match the current filters</div>';
    return;
  }
  const card=title ? `<div class="MuiCard-root" onclick="detail('${id}')"><p>${title}</p></div>` : '';
  if(id==='ffmpeg') {
    document.querySelector('#results').innerHTML='<div class="MuiCard-root">Loading App Store data...</div>';
    setTimeout(()=>{ document.querySelector('#results').innerHTML=card; },150);
    return;
  }
  document.querySelector('#results').innerHTML=card;
}
function detail(id) {
  const target=id==='wrong' ? 'n8n' : id;
  const image=id==='broken' ? '/missing-image' :
    'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
  document.querySelector('#details').innerHTML=`<div role="dialog">
    <h2><p>${titles[id]}</p></h2>
    <a aria-label="GitHub" href="https://github.com/Websoft9/docker-library/tree/main/apps/${target}">GitHub</a>
    <img width="100" height="100" src="${image}">
    <button onclick="document.querySelector('#details').innerHTML=''">Close</button>
    <button>Install</button></div>`;
}
</script></body></html>""".replace("SEARCH_PLACEHOLDER", SEARCH_PLACEHOLDER)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" and self.server.transient_login_page_failures:
            self.server.transient_login_page_failures -= 1
            self.send_response(503)
            self.end_headers()
            return
        if self.path == "/appstore" and self.server.transient_store_failures:
            self.server.transient_store_failures -= 1
            self.send_response(503)
            self.end_headers()
            return
        login_modes = {"/": "normal", "/stalled": "stalled", "/rejected": "rejected",
                       "/rate-limited": "rate-limited", "/unauthenticated": "unauthenticated",
                       "/unavailable": "unavailable"}
        mode = login_modes.get(self.path)
        content = {"/home": HOME, "/appstore": STORE}.get(self.path)
        if mode:
            content = (HOME if mode == "stalled" and "fixture_session=1" in self.headers.get("Cookie", "")
                       else LOGIN.replace("MODE", mode))
        self.send_response(200 if content else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write((content or "not found").encode())

    def do_POST(self):
        parsed = urlsplit(self.path)
        if parsed.path != "/api/auth/login":
            self.send_error(404)
            return
        self.server.login_posts += 1
        mode = parse_qs(parsed.query).get("mode", ["normal"])[0]
        status = {"rejected": 401, "rate-limited": 429, "unavailable": 503}.get(mode, 200)
        authenticated = status == 200 and mode != "unauthenticated"
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if authenticated:
            self.send_header("Set-Cookie", "fixture_session=1; Path=/")
        self.end_headers()
        self.wfile.write(json.dumps({"authenticated": authenticated}).encode())

    def log_message(self, *args):
        pass


@unittest.skipUnless(os.environ.get("MK_TEST_SCREENSHOT_BROWSER") == "1", "optional Chromium fixture tests")
class BrowserFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.login_posts = 0
        cls.server.transient_store_failures = 0
        cls.server.transient_login_page_failures = 0
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.credentials = Credentials(f"http://127.0.0.1:{cls.server.server_port}/", "fixture-user", "fixture-password")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_login_search_open_and_capture_real_png(self):
        with AppStoreBrowser(self.credentials, timeout=1) as browser:
            for app in ({"id": "n8n", "trademark": "n8n"}, {"id": "mysql", "trademark": "MySQL"}):
                validate_png(browser.capture(app))
            self.assertEqual(browser.context.pages, [])

    def test_authenticated_stalled_redirect_recovers_without_second_login(self):
        credentials = Credentials(self.credentials.url + "stalled", "fixture-user", "fixture-password")
        before = self.server.login_posts
        with AppStoreBrowser(credentials, timeout=1) as browser:
            validate_png(browser.capture({"id": "mysql", "trademark": "MySQL"}))
        self.assertEqual(self.server.login_posts - before, 1)

    def test_one_transient_server_failure_retries_without_logging_in_again(self):
        before = self.server.login_posts
        with AppStoreBrowser(self.credentials, timeout=1) as browser:
            self.server.transient_store_failures = 1
            validate_png(browser.capture({"id": "mysql", "trademark": "Ignored"}))
            self.assertEqual(browser.last_attempts, 2)
            self.assertEqual(browser.context.pages, [])
        self.assertEqual(self.server.login_posts - before, 1)

    def test_login_page_get_can_retry_once_without_repeating_credentials(self):
        before = self.server.login_posts
        self.server.transient_login_page_failures = 1
        with AppStoreBrowser(self.credentials, timeout=1) as browser:
            validate_png(browser.capture({"id": "mysql", "trademark": "Ignored"}))
        self.assertEqual(self.server.login_posts - before, 1)

    def test_unique_result_is_used_without_name_matching(self):
        with AppStoreBrowser(self.credentials, timeout=1) as browser:
            validate_png(browser.capture({"id": "ffmpeg", "trademark": ""}))
            validate_png(browser.capture({"id": "mysql", "trademark": "Incorrect name"}))
            validate_png(browser.capture({"id": "wrong", "trademark": "Unrelated name"}))
            for appid, error in (("missing", "no_search_results"),
                                 ("similar", "exact_id_not_found")):
                with self.subTest(appid=appid), self.assertRaises(ScreenshotError) as raised:
                    browser.capture({"id": appid, "trademark": ""})
                self.assertEqual(str(raised.exception), error)
                self.assertEqual(browser.context.pages, [])
            validate_png(browser.capture({"id": "mysql", "trademark": "MySQL"}))

    def test_authentication_errors_are_distinguished_and_never_retried(self):
        for path, error in (("rejected", "login_rejected"), ("rate-limited", "login_rate_limited"),
                            ("unauthenticated", "login_not_authenticated"), ("unavailable", "login_service_failed")):
            credentials = Credentials(self.credentials.url + path, "fixture-user", "fixture-password")
            before = self.server.login_posts
            with self.subTest(path=path), self.assertRaises(ScreenshotError) as raised:
                with AppStoreBrowser(credentials, timeout=1):
                    self.fail("Unauthenticated browser must not be used")
            self.assertEqual(str(raised.exception), error)
            self.assertEqual(self.server.login_posts - before, 1)

    def test_missing_exact_id_and_broken_image_fail_closed(self):
        with AppStoreBrowser(self.credentials, timeout=1) as browser:
            for app, error in (({"id": "missing", "trademark": "Missing"}, "no_search_results"),
                               ({"id": "similar", "trademark": "Similar"}, "exact_id_not_found"),
                               ({"id": "broken", "trademark": "Broken"}, "detail_image_failed")):
                with self.subTest(app=app), self.assertRaises(ScreenshotError) as raised:
                    browser.capture(app)
                self.assertEqual(str(raised.exception), error)
                self.assertEqual(browser.context.pages, [])
            # Failure must not poison the next application's page.
            validate_png(browser.capture({"id": "mysql", "trademark": "MySQL"}))

    def test_search_distinguishes_loading_and_missing_state(self):
        with AppStoreBrowser(self.credentials, timeout=1) as browser:
            for app, error in (({"id": "loading", "trademark": ""}, "search_loading_timeout"),
                               ({"id": "blank", "trademark": ""}, "search_results_timeout")):
                with self.subTest(app=app), self.assertRaises(ScreenshotError) as raised:
                    browser.capture(app)
                self.assertEqual(str(raised.exception), error)
                self.assertEqual(browser.context.pages, [])
                self.assertEqual(browser.last_attempts, 2)

    def test_multiple_results_select_exact_id_not_same_title_or_prefix(self):
        with AppStoreBrowser(self.credentials, timeout=1) as browser:
            page = browser.context.new_page()
            try:
                page.goto(browser.store_url, wait_until="domcontentloaded")
                page.get_by_placeholder(SEARCH_PLACEHOLDER, exact=True).fill("wordpress")
                results = page.locator(".MuiCard-root")
                browser.expect(results).to_have_count(3, timeout=1000)
                dialog = browser._select_result(page, results, "wordpress")
                self.assertEqual(dialog.get_by_role("link", name="GitHub", exact=True).get_attribute("href"),
                                 "https://github.com/Websoft9/docker-library/tree/main/apps/wordpress")
            finally:
                page.close()
            validate_png(browser.capture({"id": "wordpress", "trademark": "Wrong catalog name"}))


if __name__ == "__main__":
    unittest.main()
