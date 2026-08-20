Lians brand assets. The canonical scalable lotus is
`docs/assets/lians-lotus.svg`. It uses a transparent background, a sampled Lians
blue (`#1D3B7B`), and nine separately identified petal paths so motion work can
animate the mark without tracing a PNG.

The older PNG set remains for surfaces that require raster files.

- logo.png - trimmed mark with padding; used atop the SDK READMEs (PyPI/npm/pkg.go.dev)
- banner.png - 1600x400 mark + wordmark + tagline; used atop the main README
- avatar.png - 512x512 square; upload as the GitHub org avatar / social icons
- favicon.png - 64x64; embedded as a data URI in demo/index.html

Regenerate raster derivatives from the SVG if the mark changes. Do not redraw or
recolor the lotus independently on each surface.
