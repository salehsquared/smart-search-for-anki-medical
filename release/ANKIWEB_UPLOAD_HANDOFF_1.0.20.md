# AnkiWeb upload handoff — v1.0.20

## Publication status

- v1.0.20 was published on 2026-08-03 UTC (2026-08-02 America/Phoenix).
- Existing item `677438639` and its single compatibility branch were updated
  in place; no second listing or overlapping branch was created.

## Upload fields

- Archive: `dist/Smart_Search_Medical_1.0.20.ankiaddon`
- Human version: `1.0.20`
- Minimum Anki version: `241100` (24.11)
- Maximum Anki version: `-260800` (hard maximum at 26.08)
- SHA-256: `dca1f90d52b3a4d64c86108547a7178108a6a55ddcb1d435a976fa9f8415cf8f`
- Size: `52,275,482` bytes
- Files: `68`

Use `release/ANKIWEB_DESCRIPTION_1.0.20.md` as the description source.

## Completed post-upload checks

1. Confirmed AnkiWeb reports human version `1.0.20`, minimum `241100`, and hard
   maximum `-260800`.
2. Boundary downloads at `241100` and `260800` were byte-identical to the
   frozen archive; `241099` and `260900` were rejected.
3. Code `677438639` completed disposable clean installs through Anki 24.11 and
   26.08's official installer implementations.
4. A live v1.0.19-to-v1.0.20 update through Anki 26.05 preserved customized
   configuration, a sentinel, and a synthetic local index.
5. The public listing, images, support link, GitHub assets, update metadata,
   and expected Semantic compatibility text were verified.
6. Final evidence is recorded in `release/RELEASE_RECORD_1.0.20.md`.
