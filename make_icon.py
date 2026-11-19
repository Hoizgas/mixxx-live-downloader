from __future__ import annotations

import sys
import struct
from pathlib import Path
from io import BytesIO

from PIL import Image, ImageDraw


out = Path(sys.argv[1])
iconset = out / "MixxxDownloader.iconset"
iconset.mkdir(parents=True, exist_ok=True)
scale = 2
size = 1024 * scale

# Draw large, then downsample to keep the mark crisp at dock and Finder sizes.
gradient = Image.new("RGBA", (size, size))
pixels = gradient.load()
top = (108, 80, 232, 255)
bottom = (36, 29, 100, 255)
for y in range(size):
    blend = y / (size - 1)
    color = tuple(round(top[i] * (1 - blend) + bottom[i] * blend) for i in range(4))
    for x in range(size):
        pixels[x, y] = color
mask = Image.new("L", (size, size), 0)
ImageDraw.Draw(mask).rounded_rectangle((20*scale, 20*scale, 1004*scale, 1004*scale), radius=218*scale, fill=255)
icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
icon.paste(gradient, (0, 0), mask)
draw = ImageDraw.Draw(icon, "RGBA")

# Dark turntable platter and subtle grooves.
draw.ellipse((153*scale, 158*scale, 871*scale, 876*scale), fill=(16, 20, 38, 255))
for radius in range(310, 207, -20):
    box = ((512-radius)*scale, (517-radius)*scale, (512+radius)*scale, (517+radius)*scale)
    draw.ellipse(box, outline=(150, 153, 187, 50), width=4*scale)

# Teal record label with the download arrow.
draw.ellipse((379*scale, 384*scale, 645*scale, 650*scale), fill=(54, 212, 192, 255))
ink = (17, 30, 56, 255)
draw.line((512*scale, 455*scale, 512*scale, 555*scale), fill=ink, width=31*scale)
draw.line((456*scale, 500*scale, 512*scale, 556*scale, 568*scale, 500*scale), fill=ink, width=31*scale, joint="curve")
draw.rounded_rectangle((433*scale, 586*scale, 591*scale, 608*scale), radius=11*scale, fill=ink)

# A tonearm and pivot establish the DJ turntable cue.
draw.line((736*scale, 790*scale, 817*scale, 709*scale, 738*scale, 625*scale), fill=(241, 233, 255, 245), width=18*scale, joint="curve")
draw.ellipse((704*scale, 758*scale, 768*scale, 822*scale), fill=(246, 192, 102, 255))

icon = icon.resize((1024, 1024), Image.Resampling.LANCZOS)
icon.save(out / "MixxxDownloader-1024.png")
sizes = [
    ("icon_16x16.png", 16), ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024),
]
for filename, pixels in sizes:
    icon.resize((pixels, pixels), Image.Resampling.LANCZOS).save(iconset / filename)

# Assemble an ICNS container directly. The platform iconutil on this host rejects
# otherwise valid PNG iconsets, so keep the conversion deterministic and local.
chunks = []
for chunk_type, pixels in [
    ("icp4", 16), ("icp5", 32), ("icp6", 64),
    ("ic07", 128), ("ic08", 256), ("ic09", 512), ("ic10", 1024),
]:
    data = BytesIO()
    icon.resize((pixels, pixels), Image.Resampling.LANCZOS).save(data, format="PNG")
    payload = data.getvalue()
    chunks.append(chunk_type.encode("ascii") + struct.pack(">I", len(payload) + 8) + payload)
body = b"".join(chunks)
(out / "MixxxDownloader.icns").write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)
