# Smart Search for Anki — Medical v1.0.26

This release makes foreground Semantic searches reliable during review and
ensures every current Semantic request leaves `Searching…`.

## What changed

- An explicitly submitted Semantic search now runs while Anki is in review
  mode. Reviewing still pauses unsolicited indexing, reconciliation, and model
  preparation.
- Every current Semantic request now finishes with results, a clear retryable
  error, or a 45-second end-to-end timeout. The timeout also covers work held
  up before the isolated helper starts.
- A stopped or crashed helper now produces a retryable error, while model or
  index runtime failures show repair guidance. Neither path silently returns
  unrelated Exact results or a successful empty response.
- Newer searches continue to supersede older work, and a timed-out helper is
  discarded so the next request starts from a clean generation.
- Smart and Semantic matches render as soon as retrieval completes. Best-effort
  updates to live card flags, suspension, burial, and sibling IDs no longer
  keep the interface in `Searching…`.
- The isolated helper may remain warm for up to 90 seconds between adjacent
  searches. Closing the search window reaps it immediately; after switching
  modes, the existing idle timer unloads it.

## Compatibility

- Smart and Exact: Anki Desktop 24.11 through 26.08.
- Semantic Search: macOS 14 or later on Apple silicon.
- Smart and Exact remain available when Semantic is unsupported or still
  preparing.

Install or update from AnkiWeb with code **677438639**, or use Anki's
**Tools → Add-ons → Install from file…** command with the attached archive.
