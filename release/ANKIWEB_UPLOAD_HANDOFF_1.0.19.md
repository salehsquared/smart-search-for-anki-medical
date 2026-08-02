# AnkiWeb upload handoff — v1.0.19

## Existing listing

- Add-on code: `677438639`
- URL: https://ankiweb.net/shared/info/677438639
- Update this item; do not create a second listing or overlapping branch.

## Upload fields

- Archive: `dist/Smart_Search_Medical_1.0.19.ankiaddon`
- Human version: `1.0.19`
- Minimum Anki version: `241100` (24.11)
- Maximum Anki version: `-260800` (hard maximum at 26.08)
- SHA-256: `dd36b476b4dc3513e408f114b580c0e0db92adead7b3025496edcf8cc4852de8`
- Size: `52,244,211` bytes
- Files: `67`

Use `release/ANKIWEB_DESCRIPTION_1.0.19.md` as the description source.

## Required post-upload checks

1. Confirm AnkiWeb reports human version `1.0.19`, minimum `241100`, and hard
   maximum `-260800`.
2. Confirm downloads for point versions `241100` and `260800` are byte-identical
   to the frozen archive; versions outside the range must be rejected.
3. Install code `677438639` in disposable Anki 24.11 and 26.08 profiles.
4. Upgrade a preserved public v1.0.15 installation through the new native
   updater on Anki 26.05 or 26.08 and confirm `user_files` survive.
5. Confirm startup, Smart/Exact search, About update UI, and the expected
   Semantic setup state.
6. Record the final server metadata and installation evidence in
   `release/RELEASE_RECORD_1.0.19.md`.
