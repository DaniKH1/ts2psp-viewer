"""Import a supplied ISO from a real, empty welcome screen."""

import argparse
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from ts2psp.app import Sims2PSPApp


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iso", required=True, type=Path)
    args = parser.parse_args()
    workspace = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(
        prefix="ts2-first-import-", dir=workspace
    ) as folder:
        assert workspace in Path(folder).resolve().parents
        errors = []
        with patch(
            "ts2psp.app.messagebox.showerror",
            side_effect=lambda *a: errors.append(str(a)),
        ):
            app = Sims2PSPApp(base=folder)
            app.report_callback_exception = lambda *a: errors.append(str(a))
            try:
                assert app.tabs is None and app.welcome is not None
                with patch(
                    "ts2psp.app.filedialog.askopenfilename", return_value=str(args.iso)
                ):
                    app.select_iso()
                started = time.perf_counter()
                while (
                    app.tabs is None
                    and not errors
                    and time.perf_counter() - started < 120
                ):
                    app.update()
                    time.sleep(0.02)
                assert not errors, errors
                assert app.tabs is not None, app.info.get()
                assert len(app.prop_tab.rows) == 767
                assert app.prop_tab.assets.manifest["version"] == 1
                print(
                    f"Fresh ISO import passed: 767 props; {time.perf_counter() - started:.2f} s"
                )
            finally:
                app.close()


if __name__ == "__main__":
    main()
