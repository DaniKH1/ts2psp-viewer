"""Scrollable Canvas list that keeps a textured panel behind its rows."""

import tkinter as tk
from tkinter import font as tkfont
from PIL import Image, ImageTk


class TexturedListbox(tk.Canvas):
    def __init__(
        self,
        parent,
        texture_path,
        *,
        width=180,
        height=440,
        font=None,
        foreground="#101d4c",
        selection="#6daee9",
        row_height=23,
        selectmode="browse",
        **kwargs,
    ):
        kwargs.pop("exportselection", None)
        kwargs.pop("justify", None)
        super().__init__(
            parent,
            width=width,
            height=height,
            background="#aeb8e5",
            highlightthickness=0,
            borderwidth=0,
            takefocus=True,
            **kwargs,
        )
        self._source = Image.open(texture_path).convert("RGBA")
        self._photo = None
        self._items = []
        # 'browse' (una sola fila) es el historico y lo que usa la lista de
        # personajes. 'extended' anade Ctrl para alternar y Shift para marcar
        # un rango, como el tk.Listbox que sustituye en animaciones.
        self._selectmode = selectmode
        self._selected = None  # ancla / cursor de la ultima fila tocada
        self._selected_set = set()  # filas marcadas; vacia si no hay ninguna
        self._first = 0
        self._row_height = row_height
        self._font = font
        self._measure = None
        self._foreground = foreground
        self._selection = selection
        self._scroll_callback = None
        self.bind("<Configure>", self._redraw)
        self.bind("<Button-1>", self._click)
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Button-4>", lambda event: self.yview("scroll", -1, "units"))
        self.bind("<Button-5>", lambda event: self.yview("scroll", 1, "units"))
        self.bind("<Up>", lambda event: self._move(-1))
        self.bind("<Down>", lambda event: self._move(1))
        self.bind("<Shift-Up>", lambda event: self._move(-1, extend=True))
        self.bind("<Shift-Down>", lambda event: self._move(1, extend=True))
        self.bind("<Home>", lambda event: self._choose(0))
        self.bind("<End>", lambda event: self._choose(len(self._items) - 1))
        self.bind("<Prior>", lambda event: self._move(-self._visible_rows()))
        self.bind("<Next>", lambda event: self._move(self._visible_rows()))

    def configure(self, cnf=None, **kwargs):
        if cnf:
            kwargs.update(cnf)
        if "yscrollcommand" in kwargs:
            self._scroll_callback = kwargs.pop("yscrollcommand")
            self._report_scroll()
        return super().configure(**kwargs)

    config = configure

    def insert(self, index, *items):
        at = len(self._items) if index == "end" else int(index)
        self._items[at:at] = [str(item) for item in items]
        self._redraw()

    def delete(self, first, last=None):
        if not self._items:
            return
        a = self._index(first)
        b = self._index(last) if last is not None else a
        del self._items[a : b + 1]
        self._selected = None
        self._selected_set = set()
        self._first = min(self._first, self._max_first())
        self._redraw()

    def get(self, first, last=None):
        if last is None:
            return self._items[self._index(first)]
        return tuple(self._items[self._index(first) : self._index(last) + 1])

    def size(self):
        return len(self._items)

    def _state(self):
        """(ancla, filas marcadas) para detectar cambios reales."""
        return (self._selected, tuple(sorted(self._selected_set)))

    def curselection(self):
        return tuple(sorted(self._selected_set))

    def selection_set(self, first, last=None):
        a = self._index(first)
        b = self._index(last) if last is not None else a
        if a > b:
            a, b = b, a
        before = self._state()
        self._selected = a
        self._selected_set = set(range(a, b + 1))
        self.see(a)
        if before != self._state():
            self.event_generate("<<ListboxSelect>>")

    def selection_clear(self, first=None, last=None):
        before = self._state()
        self._selected = None
        self._selected_set = set()
        self._redraw()
        if before != self._state():
            self.event_generate("<<ListboxSelect>>")

    def see(self, index):
        at = self._index(index)
        if at < self._first:
            self._first = at
        elif at >= self._first + self._visible_rows():
            self._first = at - self._visible_rows() + 1
        self._first = max(0, min(self._first, self._max_first()))
        self._redraw()

    def yview(self, *args):
        if not args:
            total = max(1, len(self._items))
            return self._first / total, min(
                1, (self._first + self._visible_rows()) / total
            )
        if args[0] == "moveto":
            self._first = round(float(args[1]) * len(self._items))
        elif args[0] == "scroll":
            step = self._visible_rows() if args[2] == "pages" else 1
            self._first += int(args[1]) * step
        self._first = max(0, min(self._first, self._max_first()))
        self._redraw()

    def _index(self, index):
        return len(self._items) - 1 if index == "end" else int(index)

    def _visible_rows(self):
        return max(1, (max(1, self.winfo_height()) - 12) // self._row_height)

    def _max_first(self):
        return max(0, len(self._items) - self._visible_rows())

    def _report_scroll(self):
        if self._scroll_callback:
            self._scroll_callback(*self.yview())

    def _choose(self, index, extend=False, toggle=False):
        if not 0 <= index < len(self._items):
            return "break"
        before = self._state()
        if self._selectmode == "extended":
            if toggle:
                if index in self._selected_set:
                    self._selected_set.discard(index)
                else:
                    self._selected_set.add(index)
                self._selected = index
            elif extend and self._selected is not None:
                low, high = sorted((self._selected, index))
                self._selected_set = set(range(low, high + 1))
            else:
                self._selected = index
                self._selected_set = {index}
        elif self._selectmode == "multiple":
            if toggle:
                if index in self._selected_set:
                    self._selected_set.discard(index)
                else:
                    self._selected_set.add(index)
                self._selected = index
            else:
                self._selected = index
                self._selected_set = {index}
        else:
            self._selected = index
            self._selected_set = {index}
        self.see(self._selected)
        if before != self._state():
            self.event_generate("<<ListboxSelect>>")
        return "break"

    def _move(self, delta, extend=False):
        index = self._selected if self._selected is not None else 0
        return self._choose(
            max(0, min(len(self._items) - 1, index + delta)), extend=extend
        )

    def _click(self, event):
        self.focus_set()
        if 6 <= event.y < self.winfo_height() - 6:
            index = self._first + (event.y - 6) // self._row_height
            # Teclado en X11/Windows: 0x0001 Shift, 0x0004 Control.
            ctrl = bool(event.state & 0x0004)
            shift = bool(event.state & 0x0001)
            return self._choose(index, extend=shift, toggle=ctrl)
        return "break"

    def _wheel(self, event):
        if event.delta:
            self.yview("scroll", -int(event.delta / 120), "units")
        return "break"

    def _measure_font(self):
        """Fuente de Tk para medir el texto; se crea una sola vez."""
        if self._measure is None:
            self._measure = tkfont.Font(root=self, font=self._font)
        return self._measure

    def _ellipsize(self, text, max_px):
        """Recorta el texto para que quepa en una sola fila.

        Sin esto, create_text con width= partia los nombres largos en dos
        lineas que se solapaban con la fila siguiente, porque la altura de fila
        es fija. Un tk.Listbox normal tambien los recorta, solo que sin puntos.
        """
        if max_px <= 8:
            return ""
        font = self._measure_font()
        if font.measure(text) <= max_px:
            return text
        low, high = 0, len(text)
        while low < high:
            mid = (low + high + 1) // 2
            if font.measure(text[:mid] + "...") <= max_px:
                low = mid
            else:
                high = mid - 1
        return (text[:low] + "...") if low > 0 else "..."

    def _panel_image(self, width, height):
        source = self._source
        corner = 11
        width = max(width, 2 * corner + 1)
        height = max(height, 2 * corner + 1)
        panel = Image.new("RGBA", (width, height))
        xs = (0, corner, source.width - corner, source.width)
        ys = (0, corner, source.height - corner, source.height)
        dx = (0, corner, width - corner, width)
        dy = (0, corner, height - corner, height)
        for row in range(3):
            for col in range(3):
                tile = source.crop((xs[col], ys[row], xs[col + 1], ys[row + 1]))
                target = (dx[col + 1] - dx[col], dy[row + 1] - dy[row])
                if tile.size != target:
                    tile = tile.resize(target, Image.Resampling.BILINEAR)
                panel.paste(tile, (dx[col], dy[row]))
        return panel

    def _redraw(self, _event=None):
        width = max(2, self.winfo_width())
        height = max(2, self.winfo_height())
        super().delete("all")
        self._photo = ImageTk.PhotoImage(self._panel_image(width, height), master=self)
        self.create_image(0, 0, image=self._photo, anchor="nw")
        count = min(
            len(self._items) - self._first,
            (height - 12 + self._row_height - 1) // self._row_height,
        )
        for row in range(max(0, count)):
            index = self._first + row
            top = 6 + row * self._row_height
            if index in self._selected_set:
                self.create_rectangle(
                    5,
                    top,
                    width - 6,
                    top + self._row_height - 1,
                    fill=self._selection,
                    outline="",
                )
            self.create_text(
                width / 2,
                top + self._row_height / 2,
                text=self._ellipsize(self._items[index], max(10, width - 18)),
                anchor="center",
                font=self._font,
                fill=self._foreground,
            )
        self._report_scroll()
