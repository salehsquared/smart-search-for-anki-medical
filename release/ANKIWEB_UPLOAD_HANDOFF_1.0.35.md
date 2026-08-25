# Completed AnkiWeb upload handoff — v1.0.35

## Result

- Existing public item `677438639` and its single compatibility branch were
  updated in place on 2026-08-25 UTC.
- No duplicate listing or overlapping compatibility branch was created.

## Upload fields

- Archive: `dist/Smart_Search_Medical_1.0.35.ankiaddon`
- Human version: `1.0.35`
- Minimum Anki version: `241100` (24.11)
- Maximum Anki version: `-260800` (hard maximum at 26.08)
- SHA-256: `befcbabb81b74b09f2b89fa129ac9f2c912c88fceec7a6234e2974a73c29df1b`
- Size: `52,924,796` bytes
- Files: `68`

Use `release/ANKIWEB_DESCRIPTION_1.0.35.md` as the exact description source.

## Post-upload checks

1. AnkiWeb reports one branch with minimum `241100`, hard maximum `-260800`,
   and `human_version=1.0.35`.
2. Boundary downloads at `241100` and `260800` are byte-identical to the frozen
   archive. Requests at `241099`, `260801`, and `260900` are rejected.
3. Code `677438639` completed clean installations through Anki's official
   installer in fresh disposable Anki 24.11 and 26.08 roots. Both returned
   `InstallOk`, installed all 68 members exactly, and created no duplicate or
   backup path.
4. Exact public v1.0.26-to-v1.0.35 upgrades passed at both support boundaries.
   Configuration, disabled/custom metadata, all synthetic `user_files`, and a
   SQLite probe remained byte-identical; SQLite `quick_check` returned `ok`.
5. The rendered listing shows the v1.0.35 description, Anki 24.11–26.08 range,
   tagged image, local-privacy statement, and the intended **Contact Author**
   target.
6. Fresh GitHub and AnkiWeb downloads are byte-identical and have SHA-256
   `befcbabb81b74b09f2b89fa129ac9f2c912c88fceec7a6234e2974a73c29df1b`.

Complete CI, artifact, GitHub, listing, routing, and disposable-install
evidence is in `release/RELEASE_RECORD_1.0.35.md`.
