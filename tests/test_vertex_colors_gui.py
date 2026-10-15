"""Measure real map/prop color switches without geometry or texture reloads."""

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np

from ts2psp.app import Sims2PSPApp


def main():
    base = Path(os.environ.get("TS2_PSP_DATA_DIR", "data"))
    errors = []
    with patch(
        "ts2psp.ui.objects.messagebox.showerror",
        side_effect=lambda *a: errors.append(str(a)),
    ):
        app = Sims2PSPApp(base=base)
        app.report_callback_exception = lambda *a: errors.append(str(a))
        try:
            tab = app.map_tab
            app.tabs.select(tab)
            app.update()
            row = next(
                r for r in tab.rows if r["ruta_interna"] == "levels/deadtree/graveyard"
            )
            tab._empezar_carga(row)
            start = time.perf_counter()
            while (
                tab.current is None or tab.current["entry"] != row["entry"]
            ) and time.perf_counter() - start < 60:
                app.update()
                time.sleep(0.02)
            app.update()
            assert tab.current and tab.current["entry"] == row["entry"]
            assert not errors, errors
            scene = tab.objeto
            assert scene is not None
            geometry, uv = scene.posiciones, scene.UV
            parts, data, cache = tab.parts, tab.data, tab._cache
            camera = (
                tab.camera.yaw,
                tab.camera.pitch,
                tab.camera.zoom,
                tab.camera.pan,
                tab.camera.focus_offset,
            )
            off = np.array(scene.render(480, 320, 0.5, -0.4, 1))
            gaps, durations = [], []
            previous = time.perf_counter()
            running = True

            def heartbeat():
                nonlocal previous
                now = time.perf_counter()
                gaps.append(now - previous)
                previous = now
                if running:
                    app.after(10, heartbeat)

            app.after(10, heartbeat)
            with (
                patch.object(
                    tab,
                    "reload",
                    side_effect=AssertionError("Color switch reloaded the map"),
                ),
                patch.object(
                    scene, "_subir_textura", wraps=scene._subir_textura
                ) as upload,
            ):
                for _ in range(21):
                    start = time.perf_counter()
                    tab.toggle_vertex_colors()
                    app.update()
                    time.sleep(0.025)
                    app.update()
                    durations.append(time.perf_counter() - start)
                    assert (
                        tab.objeto is scene
                        and tab.parts is parts
                        and tab.data is data
                        and tab._cache is cache
                    )
                    assert scene.posiciones is geometry and scene.UV is uv
                assert upload.call_count == 0
            on = np.array(scene.render(480, 320, 0.5, -0.4, 1))
            assert float(np.abs(on.astype(float) - off).mean()) > 0.1
            assert camera == (
                tab.camera.yaw,
                tab.camera.pitch,
                tab.camera.zoom,
                tab.camera.pan,
                tab.camera.focus_offset,
            )
            prop = app.prop_tab
            app.tabs.select(prop)
            prop.load(
                next(
                    r for r in prop.rows if r["ruta_interna"].endswith("/fx_lamp_kine")
                )
            )
            app.update()
            prop.render()
            prop_scene = prop.objeto
            with patch.object(
                prop_scene, "_subir_textura", wraps=prop_scene._subir_textura
            ) as upload:
                for i in range(20):
                    prop.show_vertex_colors.set(bool(i % 2))
                    prop.schedule()
                    app.update()
                    time.sleep(0.025)
                    app.update()
                    assert prop.objeto is prop_scene
                assert upload.call_count == 0
            running = False
            assert not errors, errors
            assert max(gaps) < 0.5, max(gaps)
            print(
                json.dumps(
                    {
                        "map_switches": 21,
                        "prop_switches": 20,
                        "texture_reuploads": 0,
                        "map_vertices": len(geometry),
                        "max_switch_frame_seconds": max(durations),
                        "max_event_gap_seconds": max(gaps),
                    },
                    indent=2,
                )
            )
        finally:
            app.close()


if __name__ == "__main__":
    main()
