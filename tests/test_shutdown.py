import threading
import unittest
from unittest.mock import MagicMock, patch

import app as sccs_app


class ShutdownApiTests(unittest.TestCase):
    def test_issue_host_shutdown_invokes_sudo(self):
        with patch("app.subprocess.Popen") as popen:
            popen.return_value = MagicMock()
            self.assertTrue(sccs_app._issue_host_shutdown())
            popen.assert_called_once()
            args = popen.call_args[0][0]
            self.assertEqual(args, ["sudo", "-n", "shutdown", "-h", "now"])

    def test_cleanup_best_effort_times_out_without_blocking(self):
        barrier = threading.Event()

        def slow_cleanup():
            barrier.wait(timeout=2)

        with patch("app.cleanup", side_effect=slow_cleanup):
            with patch("app.logger") as logger:
                sccs_app._cleanup_best_effort(timeout_s=0.05)
                logger.warning.assert_called_once()
                self.assertIn("did not finish", logger.warning.call_args[0][0])

    def test_api_shutdown_powers_off_screens_before_host(self):
        """Host must not power off until screen SSH attempts finish."""
        order = []
        done = threading.Event()
        screen_actuator = MagicMock()

        def record_screens():
            order.append("screens")

        def record_host():
            order.append("host")
            return True

        def record_cleanup(*_args, **_kwargs):
            order.append("cleanup")
            done.set()

        screen_actuator.shutdown_all.side_effect = record_screens

        with patch.object(sccs_app, "runtime") as runtime:
            runtime.screen_actuator = screen_actuator
            with patch.object(sccs_app, "_issue_host_shutdown", side_effect=record_host):
                with patch.object(sccs_app, "_cleanup_best_effort", side_effect=record_cleanup):
                    with patch("time.sleep"):
                        client = sccs_app.app.test_client()
                        res = client.post("/api/system/shutdown")

        self.assertEqual(res.status_code, 200)
        self.assertTrue(done.wait(timeout=2), "shutdown sequence did not finish")
        self.assertEqual(order, ["screens", "host", "cleanup"])
        screen_actuator.shutdown_all.assert_called_once_with()

    def test_api_shutdown_still_powers_off_host_without_screens(self):
        done = threading.Event()
        order = []

        def record_host():
            order.append("host")
            return True

        def record_cleanup(*_args, **_kwargs):
            order.append("cleanup")
            done.set()

        with patch.object(sccs_app, "runtime") as runtime:
            runtime.screen_actuator = None
            with patch.object(sccs_app, "_issue_host_shutdown", side_effect=record_host):
                with patch.object(sccs_app, "_cleanup_best_effort", side_effect=record_cleanup):
                    with patch("time.sleep"):
                        client = sccs_app.app.test_client()
                        res = client.post("/api/system/shutdown")

        self.assertEqual(res.status_code, 200)
        self.assertTrue(done.wait(timeout=2), "shutdown sequence did not finish")
        self.assertEqual(order, ["host", "cleanup"])


class ScreenShutdownTests(unittest.TestCase):
    def test_shutdown_all_waits_for_ssh_threads(self):
        from actuators.screens import ScreenActuator

        started = threading.Event()
        release = threading.Event()
        saw_shutdown_cmd = threading.Event()

        def fake_run(cmd, *_args, **_kwargs):
            # Only gate the shutdown SSH so concurrent app probes don't deadlock.
            if isinstance(cmd, str) and "shutdown -h now" in cmd:
                saw_shutdown_cmd.set()
                started.set()
                self.assertTrue(release.wait(timeout=2))
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="0\n", stderr="")

        screens = {
            "kitchen": {
                "friendly": "Kitchen",
                "username": "joel",
                "host": "10.10.10.10",
            }
        }
        actuator = ScreenActuator.__new__(ScreenActuator)
        actuator._screens = screens
        actuator._observed = {"kitchen": 100}
        actuator._on_command_failed = None

        with patch("actuators.screens.subprocess.run", side_effect=fake_run):
            worker = threading.Thread(target=actuator.shutdown_all, kwargs={"join_timeout": 2})
            worker.start()
            self.assertTrue(started.wait(timeout=1), "SSH never started")
            self.assertTrue(saw_shutdown_cmd.is_set())
            # Before remote SSH finishes, shutdown_all must still be blocked.
            self.assertTrue(worker.is_alive())
            release.set()
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
