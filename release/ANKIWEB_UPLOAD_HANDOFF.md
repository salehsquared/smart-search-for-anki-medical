# AnkiWeb publication record — v1.0.15 public beta

## Live listing

- **Status:** public
- **Published:** 2026-07-31
- **AnkiWeb code:** `677438639`
- **Public URL:** https://ankiweb.net/shared/info/677438639
- **Installed folder:** `677438639`
- **Supported Anki version:** exactly 26.05
- **AnkiWeb metadata:** `min_point_version=260500`,
  `max_point_version=-260500`, `human_version=1.0.15`

The public item uses the same frozen v1.0.15 archive documented in
`release/RELEASE_RECORD_1.0.15.md`. The archive was not rebuilt after
publication.

## Listing fields

- **Title:** Smart Search for Anki — Medical
- **Channel:** Public beta
- **Minimum Anki version:** 26.05
- **Maximum Anki version:** 26.05
- **Support URL:** https://github.com/salehsquared/smart-search-for-anki-medical/issues/new/choose
- **Project URL:** https://github.com/salehsquared/smart-search-for-anki-medical
- **Privacy URL:** https://github.com/salehsquared/smart-search-for-anki-medical/blob/main/PRIVACY.md
- **License:** MIT for the original add-on; bundled third-party components keep
  their licenses and notices
- **Suggested tags, if offered:** medical, search, browser, productivity

## Files to use

- **Add-on:** `dist/Smart_Search_Medical_1.0.15.ankiaddon`
- **Description:** `release/ANKIWEB_DESCRIPTION_1.0.15.md`
- **Screenshots:** use the ordered list in `release/ANKIWEB_LISTING.md`
- **Release evidence:** `release/RELEASE_RECORD_1.0.15.md`
- **Checksum:** `dist/Smart_Search_Medical_1.0.15.ankiaddon.sha256`

## Completed publication verification

1. The AnkiWeb item was published and assigned code `677438639`.
2. The public server metadata reports version 1.0.15 and restricts installation
   to exactly Anki 26.05.
3. Installation by the numeric code succeeded in a fresh disposable Anki base,
   where Anki created the folder `677438639`.
4. The installed immutable files were diff-identical to the frozen distribution
   artifact. Anki-managed `meta.json` and `user_files/` were excluded from that
   comparison.
5. The installed add-on registered one toolbar entry, and **Command-K** opened
   Smart Search.
6. Smart search corrected `buproprion` to `bupropion` and applied its concise
   aliases; Exact search accepted uppercase `BUPROPION`.
7. The inline preview rendered both the card front and answer.
8. Semantic Search remained opt-in and did not download a model during the
   install or smoke test.
9. Both the text and Semantic SQLite databases returned `ok` from
   `PRAGMA quick_check`.

No AnkiWeb account, upload, or initial assigned-code step remains for v1.0.15.
Future releases should update the existing item `677438639`, then repeat the
numeric-code installation and immutable-file comparison against the new frozen
artifact.
