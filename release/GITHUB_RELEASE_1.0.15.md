# Smart Search for Anki — Medical 1.0.15

This is the first public beta of Smart Search for Anki — Medical.

## Highlights

- Smart search with case-insensitive medical typo recovery and concise
  generic/brand medication aliases
- Exact search with Anki's native filters and card-specific sibling handling
- Optional local Semantic search
- Adaptive relevance cutoffs
- Resizable inline card preview and editor
- Multi-select Browser opening, flags, suspension, and tags
- Background indexing designed to keep Anki's interface responsive

## Compatibility

- Anki Desktop 26.05
- Integration-tested on macOS 26.3 on Apple silicon
- Semantic Search: macOS 14 or later on Apple silicon only
- Windows, Linux, and Intel Mac integration are not part of this beta's support
  claim
- AnkiMobile and AnkiDroid do not load desktop add-ons

## Installation

### AnkiWeb

1. In Anki Desktop, choose **Tools → Add-ons → Get Add-ons…**.
2. Enter code **`677438639`**.
3. Restart Anki, then press **Command/Ctrl-K**.

[View the public AnkiWeb listing](https://ankiweb.net/shared/info/677438639).

The downloadable `.ankiaddon` below is the same frozen v1.0.15 build for manual
installation. A fresh-profile install from AnkiWeb was verified against this
archive byte for byte (excluding Anki-managed metadata and local indexes).

If you previously installed a manually shared development build, remove or
disable it before installing the AnkiWeb version. Do not load the named
development copy and AnkiWeb's numeric copy simultaneously.

## Privacy

Search, typo recovery, indexing, and semantic inference run locally. The add-on
contains no analytics, telemetry, advertising, or crash reporting. Optional
Semantic setup downloads pinned model files only after the user explicitly
starts setup; no card text or query is sent.

See [PRIVACY.md](https://github.com/salehsquared/smart-search-for-anki-medical/blob/main/PRIVACY.md),
[THIRD_PARTY_NOTICES.md](https://github.com/salehsquared/smart-search-for-anki-medical/blob/main/THIRD_PARTY_NOTICES.md),
and [DATA_SOURCES.md](https://github.com/salehsquared/smart-search-for-anki-medical/blob/main/DATA_SOURCES.md).

## Support

Use the
[issue forms](https://github.com/salehsquared/smart-search-for-anki-medical/issues/new/choose)
for reproducible bugs or feature requests. For private feedback or a security
report, email `product@medbrevia.com`.

Use only synthetic or fully redacted examples. Never attach an Anki collection,
profile folder, local search database, vector index, patient information,
credentials, or identifiable card content.

The exact archive SHA-256 and test evidence are recorded in
`release/RELEASE_RECORD_1.0.15.md`.
