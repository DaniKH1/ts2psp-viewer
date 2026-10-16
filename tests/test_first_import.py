"""ISO selection must work before character tabs have been created."""

from pathlib import Path
from queue import Queue
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ts2psp.app import Sims2PSPApp


class FirstImportTests(unittest.TestCase):
    def test_cancel_iso_dialog_without_data_or_character_tab(self):
        app = SimpleNamespace(importing=False, tabs=None)
        with patch("ts2psp.app.filedialog.askopenfilename", return_value="") as dialog:
            Sims2PSPApp.select_iso(app)
        dialog.assert_called_once()

    def test_start_import_from_welcome_without_character_tab(self):
        app = SimpleNamespace(
            importing=False,
            tabs=None,
            start_import=Mock(),
        )
        with patch("ts2psp.app.filedialog.askopenfilename", return_value="game.iso"):
            Sims2PSPApp.select_iso(app)
        app.start_import.assert_called_once_with("game.iso")

    def test_extraction_runs_in_a_spawned_process(self):
        app = SimpleNamespace(
            importing=False,
            welcome=True,
            import_button=Mock(),
            import_status=Mock(),
            info=Mock(),
            data_dir=Path("new-cache/data"),
            import_panel=Mock(),
            import_cancel_button=Mock(),
            import_bar=Mock(),
            _log_import=Mock(),
            _import_failed=Mock(),
        )
        context = Mock()
        with patch(
            "ts2psp.app.multiprocessing.get_context", return_value=context
        ) as spawn:
            Sims2PSPApp.start_import(app, "game.iso")
        spawn.assert_called_once_with("spawn")
        self.assertTrue(app.importing)
        app.import_button.configure.assert_called_once_with(state="disabled")
        self.assertEqual(
            context.Process.call_args.kwargs["args"][:2],
            ("game.iso", str(app.data_dir)),
        )
        context.Process.return_value.start.assert_called_once()
        app._import_failed.assert_not_called()

    def test_dead_worker_is_reported_instead_of_waiting_forever(self):
        app = SimpleNamespace(
            importing=True,
            import_messages=Queue(),
            import_last_poll=0,
            import_max_gap=0,
            import_exit_seen=1,
            import_process=SimpleNamespace(exitcode=17),
            _import_failed=Mock(),
            after=Mock(),
            poll_import=Mock(),
        )
        with patch("ts2psp.app.time.monotonic", return_value=2):
            Sims2PSPApp.poll_import(app)
        self.assertIn("exit 17", app._import_failed.call_args.args[0])
        app.after.assert_called_once()

    def test_worker_errors_and_cancellation_reach_the_window(self):
        from ts2psp.io.import_job import run_import
        from ts2psp.io.iso import ImportCancelled

        for exception, expected in (
            (ImportCancelled(), "cancelled"),
            (ValueError("broken ISO"), "error"),
        ):
            messages = Queue()
            with patch("ts2psp.io.import_job.import_iso", side_effect=exception):
                run_import("game.iso", "cache", messages, Mock())
            kind, detail = messages.get_nowait()
            self.assertEqual(kind, expected)
            if kind == "error":
                self.assertIn("broken ISO", detail)

    def test_archive_validation_can_cancel_without_changing_existing_files(self):
        import tempfile
        import zipfile
        from ts2psp.io.iso import ImportCancelled, _validate_archive

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "archive.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("mesh", b"mesh data")
            original = path.read_bytes()
            with self.assertRaises(ImportCancelled):
                _validate_archive(path, Mock(), lambda: True)
            self.assertEqual(path.read_bytes(), original)

    def test_existing_export_still_blocks_iso_replacement(self):
        app = SimpleNamespace(
            importing=False,
            tabs=object(),
            character_tab=SimpleNamespace(animations=SimpleNamespace(exporting=True)),
        )
        with (
            patch("ts2psp.app.messagebox.showinfo") as info,
            patch("ts2psp.app.filedialog.askopenfilename") as dialog,
        ):
            Sims2PSPApp.select_iso(app)
        info.assert_called_once()
        dialog.assert_not_called()
