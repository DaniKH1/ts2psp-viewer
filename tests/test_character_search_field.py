from ts2psp.paths import resource_path

"""Check the CharacterSearch.TEntry texture for tiling seams.

Tk mosaics (tiles, does not scale) the centre slice of a bordered image
element, so a seam appears whenever the slice Tk tiles is not uniform.
This reproduces that slice arithmetic exactly, with no display required.
"""
from pathlib import Path

from PIL import Image

UI_ASSET_PATH = resource_path("assets/ui")
SOURCE = UI_ASSET_PATH / "character_search.png"
EXPECTED_SIZE = (147, 28)
# Must match the border in style.element_create('Sims.CharacterSearch.field').
BORDER = (5, 5, 5, 5)


def check(source_path=SOURCE, border=BORDER, expected_size=EXPECTED_SIZE):
    image = Image.open(source_path).convert("RGBA")
    assert image.size == expected_size, image.size
    width, height = image.size
    left, top, right, bottom = border
    pixels = image.load()

    # Tk tiles source[top:height-bottom, left:width-right].
    span = range(top, height - bottom)
    columns = range(left, width - right)
    colours = {pixels[x, y] for y in span for x in columns}
    assert (
        len(colours) == 1
    ), f"centre slice is not uniform ({len(colours)} colours): {sorted(colours)[:4]}"

    # The tiled slice must also be seamless against itself, i.e. its first and
    # last columns match, otherwise the mosaic shows a line at every repeat.
    first = {pixels[columns[0], y] for y in span}
    last = {pixels[columns[-1], y] for y in span}
    assert first == last == colours, "slice edges differ, mosaic will band"

    return colours.pop()[:3]


def main():
    colour = check()
    print(f"centre slice uniform: {colour}")
    print(
        f"border={BORDER} -> tiled {147 - BORDER[0] - BORDER[2]}x"
        f"{28 - BORDER[1] - BORDER[3]} px of flat {colour}"
    )

    # Show what the previous border did, to document the regression.
    for border in ((3, 3, 3, 3), (4, 4, 4, 4), (5, 5, 5, 5)):
        try:
            check(border=border)
            print(f"border={border}: OK")
        except AssertionError as exc:
            print(f"border={border}: SEAM -> {exc}")


if __name__ == "__main__":
    main()
