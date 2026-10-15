"""Run the actual one-file EXE through a fresh ISO import and check its log."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exe", type=Path)
    parser.add_argument("--iso", type=Path, required=True)
    args = parser.parse_args()
    workspace = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(
        prefix="ts2-frozen-import-", dir=workspace
    ) as folder:
        base = Path(folder)
        environment = dict(os.environ)
        environment.pop("TS2_PSP_DATA_DIR", None)
        process = subprocess.Popen(
            [
                str(args.exe.resolve()),
                "--iso",
                str(args.iso.resolve()),
                "--data-dir",
                folder,
            ],
            env=environment,
        )
        started = time.monotonic()
        try:
            log = base / "last-import.log"
            ready = None
            while time.monotonic() - started < 180:
                if process.poll() is not None:
                    raise RuntimeError(
                        f"EXE exited before import: {process.returncode}"
                    )
                events = []
                if log.exists():
                    for line in log.read_text(encoding="utf-8").splitlines():
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass  # The parent may still be writing the last line.
                errors = [event for event in events if event["event"] == "error"]
                if errors:
                    raise RuntimeError(errors)
                ready = next((e for e in events if e["event"] == "ready"), None)
                if ready:
                    break
                time.sleep(0.1)
            if ready is None:
                raise RuntimeError(f"Import timed out; last events: {events[-5:]}")
            archives = list((base / "data").glob("*.zip"))
            assert len(archives) == 5, archives
            for path in archives:
                with zipfile.ZipFile(path) as archive:
                    assert archive.testzip() is None, path
            assert ready["props_loaded"] == 767, ready
            assert ready["max_ui_poll_gap"] < 1.5, ready
            print(
                json.dumps(
                    {"seconds": round(time.monotonic() - started, 2), **ready}, indent=2
                )
            )
        finally:
            if process.poll() is None:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                )
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
