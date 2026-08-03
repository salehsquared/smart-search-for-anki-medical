# AnkiWeb upload handoff — v1.0.23

## Candidate status

- Existing item `677438639` must be updated in place.
- Keep its single compatibility branch; do not create a duplicate listing or
  overlapping branch.

## Upload fields

- Archive: `dist/Smart_Search_Medical_1.0.23.ankiaddon`
- Human version: `1.0.23`
- Minimum Anki version: `241100` (24.11)
- Maximum Anki version: `-260800` (hard maximum at 26.08)
- SHA-256: `6908c69293cf9bac6b01a839310649ec38efeaeabb620aa1895199c3c6bc0f8d`
- Size: `58,092,823` bytes
- Files: `66`

Use `release/ANKIWEB_DESCRIPTION_1.0.23.md` as the exact description source.

## Required post-upload checks

1. Confirm AnkiWeb reports human version `1.0.23`, minimum `241100`, and hard
   maximum `-260800`.
2. Confirm boundary downloads at `241100` and `260800` are byte-identical to
   the frozen archive; reject `241099`, `260801`, and `260900`.
3. Install code `677438639` into disposable Anki 24.11 and 26.08 environments
   through Anki's official installer.
4. Run a live v1.0.22-to-v1.0.23 update in disposable Anki 26.05 while
   preserving customized configuration, `user_files`, and synthetic SQLite
   data.
5. Verify the rendered listing, images, support links, GitHub assets, and
   compatibility text.
6. Record final evidence in `release/RELEASE_RECORD_1.0.23.md`.
