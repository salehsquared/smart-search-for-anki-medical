# AnkiWeb upload handoff — v1.0.26

## Target

- Update existing public item `677438639` and its single compatibility branch
  in place.
- Do not create a duplicate listing or overlapping compatibility branch.

## Upload fields

- Archive: `dist/Smart_Search_Medical_1.0.26.ankiaddon`
- Human version: `1.0.26`
- Minimum Anki version: `241100` (24.11)
- Maximum Anki version: `-260800` (hard maximum at 26.08)
- SHA-256: `07b96d826d88280355babf70ad8311dbac3a2fb3a84c95fbf0b2642586fb027c`
- Size: `58,116,764` bytes
- Files: `67`

Use `release/ANKIWEB_DESCRIPTION_1.0.26.md` as the exact description source.

## Required post-upload checks

1. Confirm AnkiWeb reports human version `1.0.26`, minimum `241100`, and hard
   maximum `-260800`.
2. Confirm boundary downloads at `241100` and `260800` are byte-identical to
   the frozen archive; reject `241099`, `260801`, and `260900`.
3. Confirm code `677438639` completes disposable clean installs on Anki 24.11
   and 26.08 through Anki's official installer.
4. Confirm a live v1.0.25-to-v1.0.26 update preserves customized configuration,
   `user_files`, and a synthetic SQLite index.
5. Verify the rendered listing, tagged image, support/privacy links, and
   compatibility text.
