"""ISO extraction worker: keep decoding/compression outside Tk's process."""

import traceback

from ts2psp.io.iso import ImportCancelled, import_iso


def run_import(iso_path, output_dir, messages, cancel):
    try:
        result = import_iso(
            iso_path,
            output_dir,
            lambda text: messages.put(("progress", text)),
            cancelled=cancel.is_set,
        )
    except ImportCancelled:
        messages.put(("cancelled", None))
    except Exception:
        messages.put(("error", traceback.format_exc()))
    else:
        messages.put(("done", result))
