"""Run actual Play/Pause across satellite loops while Tk services other events."""

import argparse
import json
import os
from pathlib import Path
import time
import tkinter.messagebox

from ts2psp.app import Sims2PSPApp


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=22)
    parser.add_argument("--slow-frame", action="store_true")
    args = parser.parse_args()
    base = Path(
        os.environ.get(
            "TS2_PSP_DATA_DIR", Path(__file__).resolve().parent.parent / "data"
        )
    )
    errors = []
    tkinter.messagebox.showerror = lambda *values, **_: errors.append(str(values))
    app = Sims2PSPApp(base=base)
    app.update()
    tab = app.prop_tab
    app.tabs.select(tab)
    app.update()
    row = next(
        row
        for row in tab.anim_rows
        if row["ruta_interna"].endswith("/fx_rightarm_satellite")
    )
    tab.load(row)
    app.update()
    state = {
        "frames": 0,
        "loops": 0,
        "heartbeats": 0,
        "max_depth": 0,
        "max_event_gap": 0.0,
    }
    previous_position = tab.playhead
    depth = 0
    original = tab.render
    if args.slow_frame:
        original_pose = tab.posed_parts

        def slow_pose():
            time.sleep(0.025)
            return original_pose()

        tab.posed_parts = slow_pose

    def render():
        nonlocal depth, previous_position
        depth += 1
        state["max_depth"] = max(state["max_depth"], depth)
        try:
            original()
            state["frames"] += 1
            if tab.playhead < previous_position:
                state["loops"] += 1
            previous_position = tab.playhead
        finally:
            depth -= 1

    tab.render = render
    app.report_callback_exception = lambda *exception: errors.append(str(exception))
    previous_event = time.perf_counter()

    def heartbeat():
        nonlocal previous_event
        now = time.perf_counter()
        state["max_event_gap"] = max(state["max_event_gap"], now - previous_event)
        previous_event = now
        state["heartbeats"] += 1
        app.after(10, heartbeat)

    def finish():
        tab.play_button.invoke()
        if tab.playing:
            errors.append("Pause did not stop playback")
        if tab.status.cget("text").startswith("Preview:"):
            errors.append(tab.status.cget("text"))
        app.close()

    app.after(10, heartbeat)
    app.after(round(args.seconds * 1000), finish)
    tab.play_button.invoke()
    app.mainloop()
    print(json.dumps(state, indent=2))
    assert not errors, errors
    assert state["frames"] > 100 and state["heartbeats"] > 100, state
    assert state["max_depth"] == 1 and state["max_event_gap"] < 0.5, state
    assert state["loops"] >= 2, state


if __name__ == "__main__":
    main()
