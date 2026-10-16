# ISO import

## Responsive extraction

Selecting an ISO starts a separate extraction process. The main Tk process
handles the window, animated progress bar, stage messages and cancellation.
Decoding/compression no longer competes with Tk for Python execution time.

Stages cover index reading, extraction counts, catalogue writing, archive CRC
validation and installation. Validation reads checked chunks and supports
cancellation. New archives remain in a staging directory until validated;
cancelling before installation keeps the existing cache.

Worker exceptions are delivered to the window. If the worker exits without
sending a result, the parent reports its exit code instead of waiting forever.
The import polling timer is retained and cancelled when the app closes.

## Troubleshooting

The progress panel appears beneath the viewport or welcome screen. **Cancel
import** stops the job and enables another attempt. Errors include the path of
the local `last-import.log`, normally:

```text
%LOCALAPPDATA%\TS2 PSP Viewer\last-import.log
```

A configured resource folder changes that location. The log includes local
ISO/cache paths, stages, counts, errors and the largest measured UI polling gap.
It is never uploaded automatically.

To open the app and start the same import pipeline with a supplied path:

```powershell
& '.\TS2 PSP Viewer.exe' --iso 'E:\Games\The Sims 2.iso' --data-dir 'C:\TS2ViewerCache'
```

The cache directory receives `data/` and `last-import.log`. Normal file selection
remains available through the welcome button or File menu.

## Checks

```powershell
python tools/check.py
$env:PYTHONPATH = 'src'
python tests/test_first_import_gui.py --iso 'E:\Games\The Sims 2.iso'
python tools/check_frozen_import.py 'dist/TS2 PSP Viewer.exe' --iso 'E:\Games\The Sims 2.iso'
```

The GUI check starts in an empty directory, cancels extraction, retries through
the selection callback and checks the resulting tabs. The frozen check launches
the actual EXE with `--iso` into another empty directory, waits for the window
to finish loading the resources and checks all five ZIP CRCs. It does not
automate the native file picker.

On the validation PC, the one-file EXE imported the supplied ISO and validated
the resulting archives in 13.62 seconds: 85 characters, 507 meshes, 2,152
textures, 767 props, 1,336 animations and 21 maps. The largest UI polling gap was
0.417 seconds, including creation of the tabs. Cancel/retry from source also
passed, and the full suite passed 117 tests. These timings depend on the PC,
drive and operating system; the old reported hang was not reproduced locally.
