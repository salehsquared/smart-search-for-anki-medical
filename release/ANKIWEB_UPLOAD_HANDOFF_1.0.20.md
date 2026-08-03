# AnkiWeb upload handoff — v1.0.20

## Publication status

- This candidate is validated locally but has **not** been uploaded.
- The currently public release is v1.0.19.
- Update existing item `677438639`; do not create a second listing or an
  overlapping compatibility branch.

## Upload fields

- Archive: `dist/Smart_Search_Medical_1.0.20.ankiaddon`
- Human version: `1.0.20`
- Minimum Anki version: `241100` (24.11)
- Maximum Anki version: `-260800` (hard maximum at 26.08)
- SHA-256: `dca1f90d52b3a4d64c86108547a7178108a6a55ddcb1d435a976fa9f8415cf8f`
- Size: `52,275,482` bytes
- Files: `68`

Use `release/ANKIWEB_DESCRIPTION_1.0.20.md` as the description source.

## Required post-upload checks

1. Confirm AnkiWeb reports human version `1.0.20`, minimum `241100`, and hard
   maximum `-260800`.
2. Confirm downloads for point versions `241100` and `260800` are byte-identical
   to the frozen archive; versions immediately outside the range must fail.
3. Install code `677438639` into disposable Anki 24.11 and 26.08 profiles.
4. Upgrade a preserved v1.0.19 installation through Anki's native updater and
   confirm configuration and all `user_files` data survive.
5. Confirm startup, Smart/Exact search, About update UI, review-mode isolation,
   and the expected Semantic setup state.
6. Record the final server metadata, hashes, and installation evidence in
   `release/RELEASE_RECORD_1.0.20.md` before declaring the release public.
