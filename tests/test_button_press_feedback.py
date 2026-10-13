"""Which image does ttk pick when a button is both hovered and pressed?

The click feedback relies on 'pressed' winning over 'active'; if it does not,
the press is invisible. Renders a real button in each state and reads back the
pixel colours from the screen.
"""

import tkinter as tk
import time
from collections import Counter
from tkinter import ttk

import ts2psp.app as app


def dominant(image):
    colours = Counter(image.convert("RGB").getdata())
    return colours.most_common(3)


def main():
    root = tk.Tk()
    root.geometry("360x120+60+60")
    root.attributes("-topmost", True)
    app._install_sims_theme(root)
    root.deiconify()
    root.lift()
    root.update()

    idle = ttk.Button(root, text="X", style="Primary.TButton")
    idle.pack(pady=6)
    root.update_idletasks()
    root.update()

    import PIL.ImageGrab

    def shot(name):
        root.update_idletasks()
        root.update()
        time.sleep(0.35)
        root.update()
        box = (
            idle.winfo_rootx(),
            idle.winfo_rooty(),
            idle.winfo_rootx() + idle.winfo_width(),
            idle.winfo_rooty() + idle.winfo_height(),
        )
        image = PIL.ImageGrab.grab(bbox=box)
        image.save(name)
        return image

    states = {}
    states["idle"] = shot("debug_button_idle.png")
    idle.state(["active"])
    states["hover"] = shot("debug_button_hover.png")
    idle.state(["pressed", "active"])
    states["pressed+hover"] = shot("debug_button_pressed.png")

    for name, image in states.items():
        print(f"{name:<14} dominant {dominant(image)[0][0]}")

    idle.state(["!pressed", "!active"])
    root.destroy()


if __name__ == "__main__":
    main()
