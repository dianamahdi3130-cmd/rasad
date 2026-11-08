"""Media and map maintenance run without owning the satellite scanner's state."""
import importlib
import sys
import types
import unittest
from unittest.mock import Mock, patch

cli = importlib.import_module("rasad.main")


class WorkerCliTests(unittest.TestCase):
    def media_module(self, process):
        module = types.ModuleType("rasad.mediaqueue")
        module.process_one = process
        return patch.dict(sys.modules, {"rasad.mediaqueue": module})

    def test_once_processes_one_job_without_saving_scanner_state_or_sending(self):
        process = Mock(return_value=True)
        with self.media_module(process), patch.object(cli, "State") as state, \
                patch.object(cli, "tg_send") as send, patch.object(cli.time, "sleep") as sleep:
            cli.media_worker({"test": "config"}, once=True, max_minutes=0)
        process.assert_called_once_with({"test": "config"})
        state.assert_not_called()
        send.assert_not_called()
        sleep.assert_not_called()

    def test_idle_worker_has_a_bounded_lifetime(self):
        process = Mock(return_value=False)
        with self.media_module(process), \
                patch.object(cli.time, "monotonic", side_effect=[0, 0, 61]), \
                patch.object(cli.time, "sleep") as sleep:
            cli.media_worker({}, interval=2, max_minutes=1)
        process.assert_called_once()
        sleep.assert_called_once_with(2)

    def test_worker_failure_does_not_end_the_worker(self):
        process = Mock(side_effect=[RuntimeError("temporary failure"), False])
        with self.media_module(process), \
                patch.object(cli.time, "monotonic", side_effect=[0, 0, 1, 61]), \
                patch.object(cli.time, "sleep") as sleep, self.assertLogs(cli.log, "ERROR"):
            cli.media_worker({}, max_minutes=1)
        self.assertEqual(process.call_count, 2)
        self.assertEqual(sleep.call_count, 2)

    def test_media_command_dispatch_does_not_run_a_satellite_cycle(self):
        cfg = {"test": "config"}
        with patch.object(cli, "load_config", return_value=cfg), \
                patch.object(cli, "media_worker") as worker, \
                patch.object(cli, "run_cycle") as cycle:
            cli.main(["--state", "state", "--site", "site", "media-worker", "--once"])
        worker.assert_called_once_with(cfg, 2, 340, True)
        cycle.assert_not_called()

    def test_map_command_only_builds_when_needed_and_does_not_save_state(self):
        cfg = {"test": "config"}
        for needed in (False, True):
            with self.subTest(needed=needed), \
                    patch.object(cli, "load_config", return_value=cfg), \
                    patch.object(cli, "geo_needed", return_value=needed), \
                    patch.object(cli, "make_session", return_value="session") as session, \
                    patch.object(cli, "build_geodata") as build, \
                    patch.object(cli, "State") as state, \
                    patch.object(cli, "run_cycle") as cycle:
                cli.main(["maintain-geo", "--budget", "12"])
            if needed:
                build.assert_called_once_with(cfg, "session", budget_s=12)
            else:
                build.assert_not_called()
                session.assert_not_called()
            state.assert_not_called()
            cycle.assert_not_called()


if __name__ == "__main__":
    unittest.main()
