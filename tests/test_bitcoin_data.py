"""Tests for image-braiins-bitcoin-data/webui.py (no node needed).

    python3 -m unittest discover -s tests
"""
import importlib.util
import json
import os
import pathlib
import socket
import stat
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
DATA_DIR = tempfile.mkdtemp()
os.environ["DATA_DIR"] = DATA_DIR
spec = importlib.util.spec_from_file_location("bitcoin_data", REPO / "image-braiins-bitcoin-data/webui.py")
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)

NOW = 1_764_000_000
EPOCH_SECONDS = w.EPOCH_BLOCKS * w.TARGET_INTERVAL
# 461 epochs of exactly 600 s blocks at difficulty 1e14; the last is current
BOUNDARIES = [(i * w.EPOCH_BLOCKS, NOW - EPOCH_SECONDS * (460 - i), 1e14) for i in range(461)]


def state(tip_offset_blocks, tip_offset_seconds):
    start_height, start_time, _ = BOUNDARIES[-1]
    return {"chain": "main", "boundaries": BOUNDARIES,
            "tip": (start_height + tip_offset_blocks, start_time + tip_offset_seconds),
            "updated": NOW, "error": None, "ibd": False, "verification": 1.0,
            "backfill": None, "fees": {}}


def summary(st):
    with mock.patch.object(w.time, "time", return_value=NOW):
        return w.build_summary(st, w.build_rows(st))


class EarlyEpoch(unittest.TestCase):
    """The first blocks of an epoch used to give absurd numbers: one block
    5 s after the retarget meant a 100x hashrate and a halving 'in 7 days'."""

    def assert_sane(self, s, tip_time):
        true_hashrate = 1e14 * w.TWO32 / w.TARGET_INTERVAL
        self.assertAlmostEqual(s["hashrate"] / true_hashrate, 1.0, places=6)
        self.assertGreater((s["eta"] - tip_time) / 86400, 13)       # ~14 days left
        self.assertGreater((s["halving_eta"] - tip_time) / 86400, 365)
        self.assertIsNone(s["projected_change"])

    def test_first_block_soon_after_the_retarget(self):
        for seconds in (5, -60, 1):  # a block time may even run backwards
            with self.subTest(seconds=seconds):
                st = state(1, seconds)
                self.assert_sane(summary(st), st["tip"][1])

    def test_own_average_is_used_from_min_sample_blocks(self):
        st = state(w.MIN_SAMPLE, w.MIN_SAMPLE * 300)  # blocks twice as fast
        s = summary(st)
        self.assertEqual(s["avg_interval"], 300)
        self.assertAlmostEqual(s["hashrate"] / (1e14 * w.TWO32 / 300), 1.0, places=6)
        self.assertIsNotNone(s["projected_change"])


class Projection(unittest.TestCase):
    def test_matches_consensus_2015_intervals(self):
        # an epoch at exactly 600 s per block: consensus measures 2015
        # intervals against 2016 targets, so difficulty rises by 2016/2015
        st = state(w.EPOCH_BLOCKS - 1, (w.EPOCH_BLOCKS - 1) * 600)
        self.assertAlmostEqual(summary(st)["projected_change"], 2016 / 2015 - 1, places=12)

    def test_clamped_to_four_times(self):
        st = state(100, 100 * 60)  # 10x too fast
        self.assertEqual(summary(st)["projected_change"], 3.0)


class Fees(unittest.TestCase):
    def test_pruned_node_still_gives_recent_fees(self):
        tip = 460 * w.EPOCH_BLOCKS + 1500
        oldest_kept = tip - 288  # default pruning keeps ~288 blocks

        def rpc(method, params):
            if params[0] < oldest_kept:
                raise w.RpcError("Block not available (pruned data)")
            return {"totalfee": 1000}

        fees = {}
        with mock.patch.object(w, "rpc", side_effect=rpc):
            w.sync_fees(fees, tip)
        self.assertEqual(sorted(fees), list(range(oldest_kept, tip + 1)))

    def test_fetch_is_capped_per_cycle_and_resumes(self):
        tip = 460 * w.EPOCH_BLOCKS + 1000
        fees = {}
        with mock.patch.object(w, "rpc", return_value={"totalfee": 1}):
            self.assertEqual(w.sync_fees(fees, tip), 400)
            w.sync_fees(fees, tip)
            w.sync_fees(fees, tip)
        self.assertEqual(len(fees), 1001)


class Cache(unittest.TestCase):
    def write(self, content):
        with open(w.CACHE, "w") as f:
            f.write(content if isinstance(content, str) else json.dumps(content))

    def test_malformed_cache_gives_an_empty_start(self):
        for bad in ("[]", "{}", "not json", {"boundaries": [1, 2]},
                    {"boundaries": [[0, 1, 1], [5, 2, 1]]},        # wrong height
                    {"boundaries": [[0, "x", 1]]}, {"boundaries": [[0, 1, 1]], "fees": {"a": 1}}):
            with self.subTest(bad=bad):
                self.write(bad)
                self.assertEqual(w.load_cache(), (None, [], {}))

    def test_round_trip(self):
        w.save_cache("main", [(0, 100, 1.0), (2016, 200, 2.0)], {4032: 5})
        self.assertEqual(w.load_cache(), ("main", [(0, 100, 1.0), (2016, 200, 2.0)], {4032: 5}))

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores permissions")
    def test_save_failure_is_not_raised(self):
        os.chmod(DATA_DIR, stat.S_IRUSR | stat.S_IXUSR)
        try:
            w.save_cache("main", [], {})  # must only log
        finally:
            os.chmod(DATA_DIR, stat.S_IRWXU)


class Server(unittest.TestCase):
    def test_handler_has_a_timeout(self):
        self.assertIsNotNone(w.Handler.timeout)
        self.assertLessEqual(w.Handler.timeout, 60)

    def test_idle_connection_is_dropped(self):
        with mock.patch.object(w.Handler, "timeout", 0.5):
            server = ThreadingHTTPServer(("127.0.0.1", 0), w.Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                conn = socket.create_connection(server.server_address)
                conn.sendall(b"GET / HTTP/1.1\r\n")  # never finishes the request
                conn.settimeout(5)
                start = time.monotonic()
                self.assertEqual(conn.recv(1), b"")  # server closed it
                self.assertLess(time.monotonic() - start, 4)
                conn.close()
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
