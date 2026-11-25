"""Tests for the Manager Agent setup page, image-braiins-manager-agent/webui.py.

    python3 -m unittest discover -s tests
"""
import http.client
import importlib.util
import json
import os
import pathlib
import stat
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

REPO = pathlib.Path(__file__).resolve().parents[1]
DATA_DIR = tempfile.mkdtemp()
os.environ["BMA_CONFIG"] = os.path.join(DATA_DIR, "daemon.yaml")
spec = importlib.util.spec_from_file_location("agent_webui", REPO / "image-braiins-manager-agent/webui.py")
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)

AGENT_ID = "123e4567-e89b-42d3-a456-426614174000"
SECRET = "123e4567-e89b-42d3-a456-426614174001"
FORM = "agent_id=%s&secret_key=%s" % (AGENT_ID, SECRET)


class SetupPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), w.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        if os.path.exists(w.CONFIG):
            os.remove(w.CONFIG)

    def post(self, body=FORM, headers=None, raw_length=None):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        data = body.encode() if isinstance(body, str) else body
        conn.putrequest("POST", "/")
        conn.putheader("Content-Type", "application/x-www-form-urlencoded")
        conn.putheader("Content-Length", raw_length if raw_length is not None else str(len(data)))
        for k, v in (headers if headers is not None else {"X-BMA-Setup": "1"}).items():
            conn.putheader(k, v)
        conn.endheaders()
        conn.send(data)
        resp = conn.getresponse()
        result = resp.status, json.loads(resp.read())
        conn.close()
        return result

    def test_page_save_writes_0600_config(self):
        status, res = self.post(headers={"X-BMA-Setup": "1", "Sec-Fetch-Site": "same-origin"})
        self.assertEqual((status, res["ok"]), (200, True))
        with open(w.CONFIG) as f:
            self.assertEqual(f.read(), "agent_id: %s\nsecret_key: %s\n" % (AGENT_ID, SECRET))
        self.assertEqual(stat.S_IMODE(os.stat(w.CONFIG).st_mode), 0o600)
        self.assertEqual([n for n in os.listdir(DATA_DIR) if n != "daemon.yaml"], [])

    def test_saves_from_other_sites_are_refused(self):
        for headers in ({},                                                   # plain HTML form
                        {"X-BMA-Setup": "1", "Sec-Fetch-Site": "cross-site"},
                        {"X-BMA-Setup": "1", "Sec-Fetch-Site": "same-site"},  # another Umbrel app
                        {"X-BMA-Setup": "0"}):
            with self.subTest(headers=headers):
                status, res = self.post(headers=headers)
                self.assertEqual((status, res["ok"]), (403, False))
                self.assertFalse(os.path.exists(w.CONFIG))

    def test_bad_requests_get_an_answer(self):
        for body, length in ((FORM, "abc"), (FORM, "-1"), ("x" * 5000, None), (b"\xff\xfe", None)):
            with self.subTest(length=length, size=len(body)):
                status, res = self.post(body=body, raw_length=length)
                self.assertEqual((status, res["ok"]), (400, False))
                self.assertFalse(os.path.exists(w.CONFIG))

    def test_non_uuid_values_are_refused(self):
        status, res = self.post(body="agent_id=x%0Aevil:%201&secret_key=" + SECRET)
        self.assertEqual((status, res["ok"]), (200, False))
        self.assertFalse(os.path.exists(w.CONFIG))

    def test_handler_has_a_timeout(self):
        self.assertIsNotNone(w.Handler.timeout)


if __name__ == "__main__":
    unittest.main()
