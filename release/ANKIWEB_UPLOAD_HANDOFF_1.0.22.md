# AnkiWeb upload handoff — v1.0.22

## Candidate status

- Existing item `677438639` must be updated in place.
- Keep its single compatibility branch; do not create a duplicate listing or
  overlapping branch.

## Upload fields

- Archive: `dist/Smart_Search_Medical_1.0.22.ankiaddon`
- Human version: `1.0.22`
- Minimum Anki version: `241100` (24.11)
- Maximum Anki version: `-260800` (hard maximum at 26.08)
- SHA-256: `d80048e692e0776b03e1b2a43e5ba3d10bbc006d57a499ac226d32793088eec0`
- Size: `58,086,845` bytes
- Files: `66`

Use `release/ANKIWEB_DESCRIPTION_1.0.22.md` as the exact description source.

## Required post-upload checks

1. Confirm AnkiWeb reports human version `1.0.22`, minimum `241100`, and hard
   maximum `-260800`.
2. Confirm boundary downloads at `241100` and `260800` are byte-identical to
   the frozen archive; reject `241099`, `260801`, and `260900`.
3. Install code `677438639` into disposable Anki 24.11 and 26.08 environments
   through Anki's official installer.
4. Run a live v1.0.21-to-v1.0.22 update in disposable Anki 26.05 while
   preserving customized configuration, `user_files`, and synthetic SQLite
   data.
5. Verify the rendered listing, images, support links, GitHub assets, and
   compatibility text.
