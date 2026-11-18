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
const titles={n8n:'n8n',mysql:'MySQL',ffmpeg:'FFmpeg',wrong:'Wrong',duplicate:'Duplicate',broken:'Broken'};
function search(id) {
  document.querySelector('#details').innerHTML='';
  const title=titles[id];
  if(!title) {
    document.querySelector('#results').innerHTML='<div class="MuiCard-root">No apps match the current filters</div>';
    return;
  }
  const card=title ? `<div class="MuiCard-root"><p onclick="detail('${id}')">${title}</p></div>` : '';
  if(id==='ffmpeg') {
    document.querySelector('#results').innerHTML='<div class="MuiCard-root">Loading App Store data...</div>';
    setTimeout(()=>{ document.querySelector('#results').innerHTML=card; },150);
    return;
  }
  document.querySelector('#results').innerHTML=card+(id==='duplicate' ? card : '');
}
function detail(id) {
  const target=id==='wrong' ? 'n8n' : id;
  const image=id==='broken' ? '/missing-image' :
    'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
  document.querySelector('#details').innerHTML=`<div role="dialog">
    <h2><p>${titles[id]}</p></h2>
    <a aria-label="GitHub" href="https://github.com/Websoft9/docker-library/tree/main/apps/${target}">GitHub</a>
    <img width="100" height="100" src="${image}">
    <button>Install</button></div>`;
}
</script></body></html>""".replace("SEARCH_PLACEHOLDER", SEARCH_PLACEHOLDER)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
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

    def test_missing_trademark_uses_unique_result_and_checks_detail_appid(self):
        with AppStoreBrowser(self.credentials, timeout=1) as browser:
            validate_png(browser.capture({"id": "ffmpeg", "trademark": ""}))
            for appid, error in (("missing", "missing_or_ambiguous_result"),
                                 ("duplicate", "missing_or_ambiguous_result"),
                                 ("wrong", "detail_identity_failed")):
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

    def test_missing_ambiguous_wrong_identity_and_broken_image_fail_closed(self):
        with AppStoreBrowser(self.credentials, timeout=1) as browser:
            for app, error in (({"id": "missing", "trademark": "Missing"}, "missing_or_ambiguous_result"),
                               ({"id": "duplicate", "trademark": "Duplicate"}, "missing_or_ambiguous_result"),
                               ({"id": "wrong", "trademark": "Wrong"}, "detail_identity_failed"),
                               ({"id": "broken", "trademark": "Broken"}, "detail_not_ready")):
                with self.subTest(app=app), self.assertRaises(ScreenshotError) as raised:
                    browser.capture(app)
                self.assertEqual(str(raised.exception), error)
                self.assertEqual(browser.context.pages, [])
            # Failure must not poison the next application's page.
            validate_png(browser.capture({"id": "mysql", "trademark": "MySQL"}))


if __name__ == "__main__":
    unittest.main()
