# Completed AnkiWeb upload handoff — v1.0.26

## Publication outcome

- Existing public item `677438639` and its single compatibility branch were
  updated in place on 2026-08-10.
- No duplicate listing or overlapping compatibility branch was created.
- Server timestamp: `1786378023` (`2026-08-10T16:07:03Z`).

## Upload fields

- Archive: `dist/Smart_Search_Medical_1.0.26.ankiaddon`
- Human version: `1.0.26`
- Minimum Anki version: `241100` (24.11)
- Maximum Anki version: `-260800` (hard maximum at 26.08)
- SHA-256: `07b96d826d88280355babf70ad8311dbac3a2fb3a84c95fbf0b2642586fb027c`
- Size: `58,116,764` bytes
- Files: `67`

Use `release/ANKIWEB_DESCRIPTION_1.0.26.md` as the exact description source.

## Completed post-upload checks

1. AnkiWeb reports one branch with minimum `241100` and hard maximum
   `-260800`; every served archive's manifest reports
   `human_version=1.0.26`.
2. All seven supported point versions served `58,116,764` bytes with the
   frozen SHA-256 and 67 unique members. Boundary downloads were independently
   byte-compared; `241099`, `260801`, and `260900` returned HTTP 404.
3. Code `677438639` completed disposable clean installs on Anki 24.11 and
   26.08 through `aqt.addons.download_and_install_addon()`.
4. Public v1.0.25-to-v1.0.26 upgrades passed on all seven supported Anki
   versions while preserving customized configuration, top-level metadata,
   complete `user_files`, sentinels, and a synthetic SQLite index byte-for-byte.
5. The rendered listing shows the v1.0.26 description and compatibility text;
   the tagged synthetic image loads at `1040×732`, **Contact Author** targets
   the GitHub issue chooser, and the privacy statement renders. The intended
   privacy endpoint returns HTTP 200 but is not linked from the listing;
   AnkiWeb leaves bare project/mobile/email strings as text rather than links.

The complete evidence is in `release/RELEASE_RECORD_1.0.26.md`.
