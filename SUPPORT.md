# Support

Smart Search for Anki — Medical is maintained by Saleh Mostafa with MedBrevia.

Use the repository's bug-report form for a reproducible public issue. For
private feedback, email `product@medbrevia.com`. Please do not send an Anki
collection, profile folder, search database, patient information, or
identifiable card content.

## Before reporting a problem

1. Restart Anki once.
2. Record the Smart Search version, Anki version, operating-system version, and
   processor architecture.
3. Identify whether the problem occurs in Smart, Exact, Semantic, or every
   mode.
4. If Smart/Exact data needs attention, use **Refresh Smart & Exact** once and
   wait for it to finish.
5. If Semantic is preparing, try the same query in Smart or Exact. Those modes
   remain usable during Semantic preparation.
6. Check whether the issue also occurs with other add-ons temporarily disabled.
   Never disable an add-on during sync or another collection-changing
   operation.

A useful report includes exact numbered steps, expected and actual behavior,
reproducibility, search mode, a safely redacted query, any native Anki filters,
approximate collection size, and whether Semantic preparation was active.

## Current support boundary

- Smart Search is an Anki Desktop add-on; it does not run inside AnkiMobile or
  AnkiDroid.
- v1.0.25 supports Anki Desktop 24.11 through 26.08, including the 25.02,
  25.07, 25.09, 26.05, and 26.08 release families.
- The supported-version matrix is integration-tested on macOS with Apple
  silicon.
- Semantic Search supports macOS 14 or later on Apple-silicon Macs only.
- Windows, Linux, and Intel Mac integration testing is not part of the v1.0.25
  support claim.
- Smart and Exact remain available while the separate Semantic index prepares.

See [Known limitations](release/KNOWN_LIMITATIONS.md),
[Privacy](PRIVACY.md), and [Security](SECURITY.md).
