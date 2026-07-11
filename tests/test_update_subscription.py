import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

import update_subscription as app


class SubscriptionTests(unittest.TestCase):
    def test_decodes_plain_and_base64_subscriptions(self):
        plain = "vmess://one\nvless://two\nhttp://ignored\n"
        encoded = base64.b64encode(plain.encode())
        self.assertEqual(app.decode_subscription(plain.encode()), ["vmess://one", "vless://two"])
        self.assertEqual(app.decode_subscription(encoded), ["vmess://one", "vless://two"])

    def test_rejects_subscription_without_supported_links(self):
        with self.assertRaises(ValueError):
            app.decode_subscription(b"https://example.com")

    def test_thresholds_are_inclusive(self):
        self.assertTrue(app.qualifies(400, 2 * 1024 * 1024))
        self.assertFalse(app.qualifies(401, 10 * 1024 * 1024))
        self.assertFalse(app.qualifies(10, 2 * 1024 * 1024 - 1))

    def test_prepare_preserves_proxy_link_order(self):
        clash = {
            "proxies": [{"name": "node-a", "type": "vmess"}, {"name": "node-b", "type": "vless"}],
            "proxy-groups": [{"name": "select", "type": "select", "proxies": ["node-a", "node-b"]}],
        }
        links = base64.b64encode(b"vmess://one\nvless://two\n")
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(app, "fetch_upstream", side_effect=[yaml.safe_dump(clash).encode(), links]):
                count = app.prepare(Path(directory))
            self.assertEqual(count, 2)
            mapping = __import__("json").loads(Path(directory, "nodes.json").read_text())
            self.assertEqual(mapping[1], {"name": "node-b", "link": "vless://two"})

    def test_publish_does_not_replace_last_good_subscription_when_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            docs = Path(directory)
            previous = "last-good\n"
            (docs / "sub.txt").write_text(previous)
            with self.assertRaises(RuntimeError):
                app.publish_results([{"name": "n", "link": "vmess://x"}], [{"name": "n", "passed": False}], docs)
            self.assertEqual((docs / "sub.txt").read_text(), previous)


if __name__ == "__main__":
    unittest.main()
