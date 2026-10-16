"""Decode the base mip of PSP SURF textures, including embedded palettes."""

import io
import struct

import numpy as np
from PIL import Image


def unswizzle(raw, rows, rowbytes):
    """Undo PSP 16-byte by 8-row tiling for uncompressed pixels."""
    if rowbytes < 16 or rows < 8 or rowbytes % 16 or rows % 8:
        return raw
    arr = np.frombuffer(raw, dtype=np.uint8)
    return (
        arr.reshape(rows // 8, rowbytes // 16, 8, 16)
        .transpose(0, 2, 1, 3)
        .reshape(-1)
        .tobytes()
    )


def color565(value):
    return np.array(
        (
            ((value >> 11) & 31) * 255 // 31,
            ((value >> 5) & 63) * 255 // 63,
            (value & 31) * 255 // 31,
            255,
        ),
        dtype=np.uint8,
    )


def decode_bc(raw, width, height, mode):
    """Read PSP DXT blocks; their indices precede the color endpoints."""
    block_width, block_height = (width + 3) // 4, (height + 3) // 4
    block_size = 8 if mode == 0x1F else 16
    if len(raw) < block_width * block_height * block_size:
        raise ValueError("Incomplete DXT texture")
    # DXT blocks are linear. Applying pixel swizzling scrambles entire tiles.
    image = np.zeros((height, width, 4), dtype=np.uint8)
    for by in range(block_height):
        for bx in range(block_width):
            at = (by * block_width + bx) * block_size
            block = raw[at : at + block_size]
            a, b = struct.unpack_from("<HH", block, 4)
            c0, c1 = color565(a), color565(b)
            # BC2/BC3 always use four colors, independently of endpoint order.
            if mode != 0x1F or a > b:
                palette = [
                    c0,
                    c1,
                    ((2 * c0.astype(int) + c1.astype(int)) // 3).astype(np.uint8),
                    ((c0.astype(int) + 2 * c1.astype(int)) // 3).astype(np.uint8),
                ]
            else:
                palette = [
                    c0,
                    c1,
                    ((c0.astype(int) + c1.astype(int)) // 2).astype(np.uint8),
                    np.array([0, 0, 0, 0], dtype=np.uint8),
                ]
            color_bits = int.from_bytes(block[:4], "little")
            if mode == 0x20:
                alpha_bits = int.from_bytes(block[8:16], "little")
            elif mode == 0x21:
                a0, a1 = block[14:16]
                alpha_bits = int.from_bytes(block[8:14], "little")
                alpha_palette = [a0, a1]
                if a0 > a1:
                    alpha_palette += [((7 - j) * a0 + j * a1) // 7 for j in range(1, 7)]
                else:
                    alpha_palette += [
                        ((5 - j) * a0 + j * a1) // 5 for j in range(1, 5)
                    ] + [0, 255]
            for py in range(min(4, height - by * 4)):
                for px in range(min(4, width - bx * 4)):
                    n = py * 4 + px
                    pixel = palette[(color_bits >> (2 * n)) & 3].copy()
                    if mode == 0x20:
                        pixel[3] = ((alpha_bits >> (4 * n)) & 15) * 17
                    elif mode == 0x21:
                        pixel[3] = alpha_palette[(alpha_bits >> (3 * n)) & 7]
                    image[by * 4 + py, bx * 4 + px] = pixel
    return image


def _palette(data, head, end):
    # The CLUT descriptor follows a variable number of mip pointers.
    at = head + 0x10 + struct.unpack_from("<I", data, head + 0x10)[0]
    if not head <= at <= end - 8:
        raise ValueError("Palette descriptor outside SURF")
    pointer, config = struct.unpack_from("<II", data, at)
    count, format_code = config & 0xFFFF, config >> 16
    start = at + pointer
    if format_code != 5 or not 1 <= count <= 256 or not at <= start <= end - count * 4:
        raise ValueError("Unsupported or incomplete SURF palette")
    return np.frombuffer(data[start : start + count * 4], dtype=np.uint8).reshape(
        count, 4
    )


def _decode_surface_at(data, off, section_size, head):
    header = struct.unpack_from("<I", data, 12)[0]
    # Section sizes exclude the container header, including 160-byte map bundles.
    end = min(
        len(data), off + (header if header in (96, 128, 160) else 128) + section_size
    )
    if head + 0x18 > end:
        return None
    flags = struct.unpack_from("<I", data, head + 8)[0]
    mode, mip_count = flags & 0xFF, flags >> 24
    width, height = struct.unpack_from("<HH", data, head + 12)
    if (
        mode not in (0x18, 0x1D, 0x1E, 0x1F, 0x20, 0x21)
        or not 1 <= mip_count <= 12
        or not 1 <= width <= 2048
        or not 1 <= height <= 2048
        or width & (width - 1)
        or height & (height - 1)
    ):
        return None
    start = head + 0x14 + struct.unpack_from("<I", data, head + 0x14)[0]
    if mode in (0x1F, 0x20, 0x21):
        byte_count = (
            ((width + 3) // 4) * ((height + 3) // 4) * (8 if mode == 0x1F else 16)
        )
    else:
        byte_count = (
            width * height * (2 if mode == 0x18 else 1) // (2 if mode == 0x1E else 1)
        )
    if not head <= start <= end - byte_count:
        return None
    raw = data[start : start + byte_count]
    if mode in (0x1F, 0x20, 0x21):
        pixels = decode_bc(raw, width, height, mode)
    else:
        rowbytes = byte_count // height
        # El bit que marca el swizzle no siempre es el 8: los inodoros lo llevan
        # en el 9 y sus texturas salian en rayas horizontales. Comparando las
        # 1005 texturas de 8 y 4 bits del catalogo con y sin la pasada, solo
        # 7 necesitan un bit que el 8 no cubria, y los tres lo cubren.
        if flags & 0x700:
            raw = unswizzle(raw, height, rowbytes)
        if mode in (0x1D, 0x1E):
            packed = np.frombuffer(raw, dtype=np.uint8)
            if mode == 0x1E:
                indices = np.empty(packed.size * 2, dtype=np.uint8)
                indices[::2], indices[1::2] = packed & 15, packed >> 4
            else:
                indices = packed
            palette = _palette(data, head, end)
            if int(indices.max()) >= len(palette):
                raise ValueError("Texture references a missing palette entry")
            pixels = palette[indices.reshape(height, width)]
        else:
            vals = np.frombuffer(raw, dtype="<u2").reshape(height, width)
            pixels = np.stack(
                tuple(((vals >> shift) & 15) * 17 for shift in (0, 4, 8, 12)), axis=2
            ).astype(np.uint8)
    image = Image.fromarray(pixels).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def read_surface(data, off, section_size):
    """Return pixels and the source name from the same validated SURF header."""
    # Leaf resources use 96 bytes; bundles use 128, sometimes with a prefix.
    header = struct.unpack_from("<I", data, 12)[0]
    deltas = {96: (96, 128, 160), 160: (160, 128)}.get(header, (128, 160))
    for delta in deltas:
        result = _decode_surface_at(data, off, section_size, off + delta)
        if result is not None:
            return result, surface_source(data, off + delta)
    return None, ""


def decode_surface(data, off, section_size):
    return read_surface(data, off, section_size)[0]


def surface_source(data, head):
    """Read the source bitmap path through the SURF relative string pointer."""
    at = head + 4
    if not 0 <= at <= len(data) - 4:
        return ""
    target = at + struct.unpack_from("<i", data, at)[0]
    if not 0 <= target < len(data):
        return ""
    path = data[target : target + 1024].split(b"\0", 1)[0].decode("latin1")
    path = path.replace("\\", "/").casefold()
    if not path or not path.isprintable() or "\ufffd" in path:
        return ""
    return path.split("/data/", 1)[-1]
