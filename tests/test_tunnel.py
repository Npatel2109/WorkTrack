import io
import logging
import subprocess
import unittest
from unittest.mock import Mock, patch

from geosite.tunnel import QuickTunnel


class QuickTunnelTests(unittest.TestCase):
    def setUp(self):
        self.tunnel = QuickTunnel("cloudflared", "http://127.0.0.1:5000", logging.getLogger("test"))

    def tearDown(self):
        self.tunnel.stop()

    def test_missing_executable_is_explicit(self):
        with patch("geosite.tunnel.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "not installed"):
                self.tunnel.start()
        self.assertFalse(self.tunnel.snapshot()["ready"])

    def test_start_fixed_command_and_prevent_duplicate_processes(self):
        process = Mock()
        process.poll.return_value = None
        with patch("geosite.tunnel.shutil.which", return_value="cloudflared.exe"), \
                patch("geosite.tunnel.subprocess.Popen", return_value=process) as popen, \
                patch("geosite.tunnel.threading.Thread"):
            self.tunnel.start()
            self.tunnel.start()
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0], [
            "cloudflared.exe", "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:5000",
        ])
        self.assertFalse(self.tunnel.snapshot()["ready"])
        self.tunnel.url = "https://example.trycloudflare.com"
        self.tunnel.connected = True
        self.assertTrue(self.tunnel.snapshot()["ready"])
        self.tunnel.stop()
        process.terminate.assert_called_once()
        self.assertEqual(self.tunnel.snapshot()["url"], "")

    def test_output_parsing_and_exit_do_not_report_ready(self):
        process = Mock()
        process.stdout = io.StringIO(
            "https://example.trycloudflare.com\nRegistered tunnel connection connIndex=0\n"
        )
        process.wait.return_value = 1
        process.poll.return_value = 1
        self.tunnel.process = process
        with self.assertLogs("test", level="ERROR"):
            self.tunnel._read(process)
        self.assertEqual(self.tunnel.url, "https://example.trycloudflare.com")
        self.assertFalse(self.tunnel.snapshot()["ready"])
        self.assertIn("code 1", self.tunnel.snapshot()["error"])

    def test_stop_kills_only_owned_process_when_termination_times_out(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("cloudflared", 5), 0]
        self.tunnel.process = process
        self.tunnel.stop()
        process.kill.assert_called_once()
        self.assertFalse(self.tunnel.snapshot()["running"])
