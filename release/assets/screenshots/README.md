# Screenshot assets

These publication images use synthetic medical study content only. They contain
no real profile, deck, tag, card text, patient information, or local filesystem
path.

- `01-smart-search.png` and `05-inline-editor.png` are clean-profile captures
  of the frozen v1.0.15 add-on.
- `02-semantic-setup.png` and `03-bulk-actions.png` are deterministic release
  compositions reviewed against the original public interface.
- `04-about-privacy.png` is a deterministic release composition reviewed
  against the v1.0.19 About interface.
- The matching SVG files are editable source compositions, not test evidence.

Render them from the repository root:

```sh
python3 release/render_screenshots.py --png
```

The script always writes the same SVG markup for the same bundled MedBrevia logo
and converts it with `rsvg-convert` when `--png` is supplied. Running it with
`--png` regenerates the composed PNGs and must not be used to replace the two
clean-profile captures. `SHA256SUMS.txt` covers every shipped SVG and PNG.

The compositions are illustrative, not evidence that the distribution build
passed clean-install testing. The release record separately documents the
isolated integration tests.
