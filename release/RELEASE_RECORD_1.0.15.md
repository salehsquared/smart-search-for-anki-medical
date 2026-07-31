# Release record — Smart Search for Anki — Medical 1.0.15

## Frozen artifact

- Channel: public beta
- Git ref for the frozen code and artifact: `v1.0.15`
- Build command: `python scripts/build_addon.py`
- Build host: macOS 26.3 (25D125), Apple silicon (`arm64`)
- Artifact: `dist/Smart_Search_Medical_1.0.15.ankiaddon`
- Bytes: 27,701,670
- Files: 53
- SHA-256:
  `6b876c22e0b4053f92bdff68888e21442237ed5220b262b2dc662d65fcb8ac66`
- Anki minimum: 26.05
- Anki maximum: 26.05
- Semantic support: macOS 14 or later on Apple silicon
- Screenshot checksums:
  `release/assets/screenshots/SHA256SUMS.txt`

The package was built twice independently from the same tree. The two archives
and sidecars were byte-for-byte identical. ZIP CRC validation passed for all 53
entries, and the archive has no enclosing directory.

## Automated validation

- Complete unit and offscreen Qt suite: **263 passed**, 0 failed
- Packaged Python compilation: passed
- Manifest, public-file allowlist, archive paths, package ceiling, and CRCs:
  passed
- Bundled wheel, terminology, and notice checksums: passed
- Privacy scan for local paths, profile data, collection databases, secrets,
  credentials, caches, logs, generated indexes, and telemetry: passed
- `git diff --check`: passed

## Isolated Anki integration

Host: Anki Desktop 26.05 on macOS 26.3, Apple silicon. Every integration test
used a disposable base under `/private/tmp` and synthetic notes.

### Clean install and search

- Installed the exact `.ankiaddon` through **Tools → Add-ons → Install from
  file**, restarted Anki, and confirmed one Tools action and one compact Smart
  toolbar entry.
- Smart typo recovery resolved `buproprion` to `bupropion`, then expanded the
  concise aliases `aplenzin`, `forfivo`, and `wellbutrin`.
- Exact search was case-insensitive and accepted native `deck:` and `tag:`
  filters.
- The incomplete query `bupropon is:` searched the completed term without a
  failure dialog.
- Arrow navigation opened the inline preview; Front/Answer, close/reopen, and
  inline editing all worked. The synthetic edit was saved and rendered
  immediately.
- Selection reported the exact **5 notes · 5 cards** scope.
- Keyboard result activation invoked Anki's Browser; search preservation,
  lifecycle, and double-click behavior are also covered by the offscreen
  integration suite.

### Semantic setup

- Missing Semantic assets did not install automatically.
- Explicit setup downloaded and SHA-256-validated the pinned model and local
  runtime, then indexed all five synthetic notes.
- Installed footprint in the disposable profile:
  - model: 34 MB
  - runtime: 100 MB
  - five-note vector index: 824 KB (capacity-backed file)
- Smart and Exact searches remained responsive during setup.
- A live Semantic query for `Lowers seizure threshold` returned the intended
  synthetic Wellbutrin note. The footer reported five indexed notes and a
  30 ms query.
- Text and Semantic SQLite `PRAGMA quick_check` results: `ok`.

### Upgrade and rollback

- Installed v1.0.10, selected Exact mode, set the result limit to 37, disabled
  preview, built indexes, and added a disposable persistence sentinel.
- Upgraded to v1.0.15 through Anki's real **Install from file** workflow and
  restarted.
- Exact mode, result limit 37, disabled preview, indexes, and sentinel all
  survived.
- Installed v1.0.14 over v1.0.15 through the same workflow and restarted.
- The same settings, indexes, and sentinel survived rollback.
- The ordered logical digest of notes, cards, and revlog was identical before
  upgrade, after upgrade, and after rollback:
  `f92a37a238e4bdc6361879000cc5d1e16811920d9489e199fb25248ed834464a`.
- Every text and Semantic index passed `PRAGMA quick_check` at each phase.

### Numeric-folder simulation

- Renamed the disposable installed folder to `9999999999`, simulating an
  AnkiWeb-assigned numeric code.
- Anki restarted with exactly one Smart entry; Smart/Exact initialized with all
  five notes.
- Collection digest and both index integrity checks remained unchanged.

### Real AnkiWeb numeric-code install

- The add-on was installed from AnkiWeb by code `677438639` in a fresh
  disposable Anki base; Anki created the installed folder `677438639`.
- The installed immutable files were diff-identical to
  `dist/Smart_Search_Medical_1.0.15.ankiaddon`. Anki-managed `meta.json` and
  `user_files/` were excluded from the comparison.
- The installed add-on registered exactly one toolbar entry, and **Command-K**
  opened Smart Search.
- Smart search corrected `buproprion` to `bupropion` and expanded its concise
  aliases. Exact search accepted uppercase `BUPROPION`.
- The inline preview rendered both the card front and answer.
- Semantic Search remained opt-in, and no model was downloaded during this
  installation or smoke test.
- Both the text and Semantic SQLite databases returned `ok` from
  `PRAGMA quick_check`.

## Qt accessibility control finding

Computer-driven accessibility hierarchy inspection crashed Anki's native
Browser with `SIGSEGV / EXC_BAD_ACCESS` in Anki's bundled Qt 6.11 Cocoa
accessibility plugin. The same crash and exact native stack reproduced after
the Smart Search add-on was completely removed from the disposable profile.
Ordinary keyboard activation remained healthy before the hierarchy query.

This is therefore recorded as an Anki/Qt macOS accessibility-automation fault,
not a Smart Search execution fault. The add-on's Browser, close, preview, and
stale-widget lifecycles remain covered by the passing offscreen suite.

## Normal-profile boundary

The normal Anki collections and installed Smart Search code/configuration were
never used for integration testing.

Before/after SHA-256 hashes were identical for:

- both normal `collection.anki2` files and their WAL/SHM companions
- the installed add-on's `manifest.json`, `config.json`, and `meta.json`

The automation framework accidentally opened and then closed the normal Anki
application once while recovering from a disposable-process crash. No sync or
collection action was performed, and an add-on-update prompt was cancelled.
Anki rewrote only `prefs21.db` during that open/close cycle (same file size and
inode, different hash). This deviation is disclosed rather than represented as
a byte-for-byte normal-profile pass.

## AnkiWeb publication

- Status: public
- Publication date: 2026-07-31
- AnkiWeb code: `677438639`
- Public URL: https://ankiweb.net/shared/info/677438639
- Supported Anki version: exactly 26.05
- Server metadata: `min_point_version=260500`,
  `max_point_version=-260500`, `human_version=1.0.15`

Completed for this release:

- frozen deterministic archive and checksum
- public README, privacy, support, security, limitations, attribution, and
  third-party notices
- GitHub release notes and AnkiWeb listing copy
- issue forms and support URL
- publication screenshots and checksums
- clean-install, upgrade, rollback, Semantic, and numeric-folder QA
- AnkiWeb publication and assignment of code `677438639`
- post-publication install by the real numeric code
- immutable-file comparison against the frozen artifact
- installed Smart, Exact, preview, toolbar, shortcut, opt-in Semantic, and
  SQLite integrity smoke checks

Reviewer: Codex release QA
Validation date: 2026-07-31
