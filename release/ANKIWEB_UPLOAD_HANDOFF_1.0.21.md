# AnkiWeb upload handoff — v1.0.21

## Publication target

- Update existing item `677438639` and its single compatibility branch in
  place. Do not create a second listing or overlapping branch.

## Upload fields

- Archive: `dist/Smart_Search_Medical_1.0.21.ankiaddon`
- Human version: `1.0.21`
- Minimum Anki version: `241100` (24.11)
- Maximum Anki version: `-260800` (hard maximum at 26.08)
- SHA-256: `9f3640fffaa964cd2fd1b530d6626ba7ce2fe32c1f07c4b572a731b2dc95375a`
- Size: `58,084,711` bytes
- Files: `66`

Use `release/ANKIWEB_DESCRIPTION_1.0.21.md` as the exact description source.

## Required post-upload checks

1. Confirm AnkiWeb reports human version `1.0.21`, minimum `241100`, and hard
   maximum `-260800`.
2. Confirm boundary downloads at `241100` and `260800` are byte-identical to
   the frozen archive; reject `241099`, `260801`, and `260900`.
3. Complete disposable clean installs from code `677438639` through the oldest
   and newest supported Anki versions.
4. Complete a live v1.0.20-to-v1.0.21 update through Anki 26.05 and verify that
   customized configuration, `user_files`, and synthetic indexes survive.
5. Verify the rendered listing, tagged images, support links, GitHub assets,
   update metadata, and Semantic compatibility text.
6. Record the final evidence in `release/RELEASE_RECORD_1.0.21.md`.
