# Smart Search for Anki — Medical v1.0.18

This release expands tested compatibility from one Anki version to **Anki
Desktop 24.11 through 26.08**.

## What changed

- Added compatibility for Anki's Python 3.9 and Python 3.13 generations.
- Added feature-probed Anki integrations so optional host APIs do not take down
  core search.
- Added a separately pinned Python 3.9 Semantic runtime; Semantic remains
  available on supported Apple-silicon Macs running older Anki releases.
- Added a seven-version automated Anki/Qt compatibility matrix.
- Added complete version-specific runtime licensing and attribution.

## Supported versions

Tested on Anki 24.11, 25.02.7, 25.07.5, 25.09.4, 25.09.5, 26.05, and 26.08.
Semantic Search requires macOS 14 or later on Apple silicon. Smart and Exact
remain available on other desktop platforms, but Windows, Linux, and Intel Mac
integration testing is not part of this beta support claim.

## Artifact

- `Smart_Search_Medical_1.0.18.ankiaddon`
- SHA-256: `364b20361d9706249b12f2d257c8f11514bbc518007844034118ccacdfeaba77`

Install from AnkiWeb with code **677438639** after the v1.0.18 listing update,
or use Anki's **Tools → Add-ons → Install from file…** command with the attached
archive.
