# Screenshot assets

These publication images use synthetic medical study content only. They contain
no Anki profile name, real deck, real tag, card text, patient information, or
local filesystem path.

Render them from the repository root:

```sh
python3 release/render_screenshots.py --png
```

The script always writes the same SVG markup for the same bundled MedBrevia logo
and converts it with `rsvg-convert` when `--png` is supplied. It also writes
`SHA256SUMS.txt`.

The images are polished release mockups, not evidence that the distribution
build passed clean-install testing. Before publication, compare each image to
the frozen application and replace any materially inaccurate mockup with a
privacy-safe capture from a synthetic test profile.
