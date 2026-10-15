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
            welcome=True,
            import_button=Mock(),
            info=Mock(),
            data_dir=Path("new-cache/data"),
            import_messages=Queue(),
        )

        def inline_thread(target, **_):
            return SimpleNamespace(start=target)

        with (
            patch("ts2psp.app.filedialog.askopenfilename", return_value="game.iso"),
            patch("ts2psp.app.threading.Thread", side_effect=inline_thread),
            patch("ts2psp.app.import_iso", return_value={"props": 767}) as importer,
        ):
            Sims2PSPApp.select_iso(app)
        self.assertTrue(app.importing)
        app.import_button.configure.assert_called_once_with(state="disabled")
        self.assertEqual(importer.call_args.args[:2], ("game.iso", app.data_dir))
        self.assertEqual(app.import_messages.get_nowait(), ("done", {"props": 767}))

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
