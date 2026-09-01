"""
Genera los iconos PNG de la PWA sin dependencias (Pillow no esta).
Fondo oscuro + tres barras (evocan las probabilidades 1/X/2).
    python _iconos.py
"""
import struct
import zlib
import os

FONDO = (14, 18, 27)        # #0e121b
BARRA1 = (56, 189, 248)     # celeste
BARRA2 = (148, 163, 184)    # gris
BARRA3 = (52, 211, 153)     # verde


def _png(path, size):
    px = bytearray()
    m = size // 8
    # tres barras centradas, alturas 45%, 65%, 55%
    barras = [
        (int(size * 0.20), int(size * 0.55), BARRA1),
        (int(size * 0.42), int(size * 0.32), BARRA2),
        (int(size * 0.64), int(size * 0.44), BARRA3),
    ]
    bw = int(size * 0.16)
    for y in range(size):
        px.append(0)  # filtro de fila
        for x in range(size):
            c = FONDO
            for bx, bh, bc in barras:
                if bx <= x < bx + bw and (size - m - bh) <= y < (size - m):
                    c = bc
            px += bytes(c)
    raw = bytes(px)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
    idat = zlib.compress(raw, 9)
    with open(path, "wb") as f:
        f.write(sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b""))
    print("escrito", path, size, "x", size)


if __name__ == "__main__":
    os.makedirs("docs", exist_ok=True)
    _png("docs/icon-192.png", 192)
    _png("docs/icon-512.png", 512)
    _png("docs/icon-180.png", 180)   # apple-touch-icon
