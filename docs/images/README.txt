Lians brand assets. The canonical scalable mark is `docs/assets/lians-mark.svg`:
a four-part L (periwinkle #8b9ee8, sage #79bfa9, apricot #f0ac76, rose #e693b2)
on a transparent background. The wordmark sets "Lians" in Inter Tight Bold with
one colour per letter: L #5b6fd0, i #3f9883, a #d9864a, n #c8638a, s #7a5aa8.
Ink is #1f2a44 on warm off-white #fbfaf7. The same mark and palette are used on
check.lians.ai.

Raster derivatives, all rendered from that SVG and palette:

- logo.png - 325x120 mark + wordmark, transparent; used atop the SDK READMEs (PyPI/npm/pkg.go.dev)
- banner.png - 1600x400 mark + wordmark + tagline; used atop the main README
- avatar.png - 512x512 mark on #fbfaf7; upload as the GitHub org avatar / social icons
- favicon.png - 128x128 mark, transparent

Regenerate the PNGs from the SVG if the mark changes. Do not redraw or recolour
the mark independently on each surface.
