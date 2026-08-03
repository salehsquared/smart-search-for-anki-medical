# Support

Smart Search for Anki — Medical is maintained by Saleh Mostafa with MedBrevia.

For a reproducible bug, use the repository's **Bug report** form. For private
feedback, email `product@medbrevia.com`. Please do not send an Anki collection,
profile folder, search database, patient information, or identifiable card
content.

## Before reporting a problem

1. Restart Anki once.
2. Record the Smart Search version, Anki version, macOS/Windows/Linux version,
   and processor architecture.
3. Identify whether the problem occurs in Smart, Exact, Semantic, or every mode.
4. If Smart/Exact data needs attention, use **Refresh Smart & Exact** once and
   wait for it to finish.
5. If Semantic is preparing, try the same query in Smart or Exact. Those modes
   are designed to remain usable during Semantic preparation.
6. Check whether the issue also occurs with other add-ons temporarily disabled.
   Never disable an add-on in the middle of sync or another collection-changing
   operation.

## A useful report includes

- Exact, numbered reproduction steps
- Expected result and actual result
- Whether it happens every time
- Search mode and a safely redacted query
- Native Anki filters used, if any
- Approximate collection size
- Whether Semantic setup or preparation was active
- Relevant error text copied as text

Use synthetic examples whenever possible. If a screenshot is necessary, remove
names, profile names, deck names, tags, patient details, and personal card
content first.

## Current support boundary

- Smart Search is an Anki Desktop add-on and cannot run inside AnkiMobile or
  AnkiDroid.
- v1.0.21 supports Anki Desktop 24.11 through 26.08, including the 25.02,
  25.07, 25.09, 26.05, and 26.08 release families.
- The supported-version matrix is integration-tested on macOS with Apple
  silicon.
- Semantic Search supports **macOS 14 or later on Apple-silicon Macs only**.
- Windows, Linux, and Intel Mac integration testing is not part of the v1.0.21
  support claim.
- Smart and Exact do not require Semantic Search and remain available while its
  separate index is preparing.
- Only Anki versions listed on the published AnkiWeb page are supported.

## Collection safety

The add-on's indexes are disposable and profile-scoped. Flags, suspension, and
tags use Anki's supported undoable operations. If reporting a data-integrity
concern, stop using the affected profile, make a normal Anki backup, and report
the exact action sequence. Do not upload the collection with the report.

## Security reports

Do not open a public issue for a suspected vulnerability or accidental exposure
of private card data. Email `product@medbrevia.com` with the subject
`Smart Search security report`, a minimal description, and a safe way to reach
you. Do not attach a collection or credentials.

## Response expectations

Support is best-effort. Reproducible collection-safety, startup-crash, or
security issues take priority over feature requests and ranking preferences.
