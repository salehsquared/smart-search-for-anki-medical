# AnkiWeb upload handoff — v1.0.15 public beta

Stop before this workflow until the frozen Git commit, GitHub release, archive,
and evidence record all refer to the same SHA-256.

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
- **Description:** `release/ANKIWEB_LISTING.md`, section
  `Ready-to-paste description`
- **Screenshots:** use the ordered list in `release/ANKIWEB_LISTING.md`
- **Release evidence:** `release/RELEASE_RECORD_1.0.15.md`
- **Checksum:** `dist/Smart_Search_Medical_1.0.15.ankiaddon.sha256`

## Upload boundary

1. Sign in to the owning AnkiWeb account.
2. Open https://ankiweb.net/shared/addons/ and choose **Upload**.
3. Enter the fields above, paste the prepared description, attach the frozen
   `.ankiaddon`, add the reviewed screenshots, and submit.
4. Record the assigned numeric add-on code and public URL in the release record.
5. Install by that numeric code in a fresh disposable profile and repeat the
   final smoke test before broadly announcing the listing.

The login, upload, submission, and assigned-code verification are intentionally
the only steps not completed by the pre-upload release preparation.
