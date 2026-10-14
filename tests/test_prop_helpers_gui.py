"""Reloading helper visibility must rebuild the scene and clear empty previews."""

import os
from pathlib import Path
import tkinter.messagebox

from ts2psp.app import Sims2PSPApp


def main():
    base = Path(
        os.environ.get(
            "TS2_PSP_DATA_DIR", Path(__file__).resolve().parent.parent / "data"
        )
    )
    errors = []
    tkinter.messagebox.showerror = lambda *args, **kwargs: errors.append(str(args))
    app = Sims2PSPApp(base=base)
    try:
        app.update()
        app.report_callback_exception = lambda *args: errors.append(str(args))
        tab = app.prop_tab
        app.tabs.select(tab)
        app.update()
        row = next(
            row for row in tab.rows if row["ruta_interna"].endswith("/fx_crib_cheap")
        )
        counts = []
        scenes = []
        for visible in (False, True, False):
            tab.show_helpers.set(visible)
            tab.load(row)
            tab.render()
            app.update()
            counts.append(len(tab.objeto.posiciones))
            scenes.append(tab.objeto)
            assert len(tab.objeto.dibujos) == len(tab.parts)
        assert counts[0] == counts[2] < counts[1], counts
        assert scenes[0] is not scenes[1] and scenes[1] is not scenes[2]
        empty = next(
            row
            for row in tab.rows
            if row["ruta_interna"].endswith("/fx_o-shower-opendoor")
        )
        tab.load(empty)
        tab.render()
        app.update()
        assert not tab.parts and tab.canvas._photo is None
        assert str(tab.play_button.cget("state")) == "disabled"
        tab.show_helpers.set(True)
        tab.load(empty)
        tab.render()
        app.update()
        assert tab.parts and tab.canvas._photo is not None
        assert not errors, errors
        print(
            f"Helper GUI: visible vertex counts {counts}; empty resource clears and restores OK"
        )
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
