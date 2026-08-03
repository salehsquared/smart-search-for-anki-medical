# Smart Search for Anki — Medical v1.0.20

This public-beta release keeps review sessions isolated from search maintenance
and makes local indexes more durable, targeted, and interruption-safe.

## Highlights

- Reviewing now triggers no Smart Search indexing, collection reads,
  typo-vocabulary refreshes, preview refreshes, or Semantic work.
- Note and card changes are retained in a crash-safe local queue and applied in
  small targeted batches outside review mode.
- Sync, import, and undo/redo use compact collection manifests to identify
  affected notes and cards before scheduling repairs.
- Filtered-deck scheduling changes resolve against each card's stable home deck,
  preventing unnecessary stale-index work.
- Semantic inference loads only when requested, stops cooperatively when
  reviewing begins, and releases active resources after use.
- First-time setup and manual rebuilds are bounded, cancellable, and publish
  only complete, internally consistent indexes.

## Reliability and safety

- Durable maintenance resumes after restart without losing exact edit hints.
- Successive edits coalesce with the newest live or deleted state winning.
- Lexical and Semantic generations, pending maintenance, and vector counts are
  checked before Semantic Search is reported ready.
- Background maintenance yields between bounded batches and never edits
  `collection.anki2` directly.

## Validation

- The complete 431-test suite passed with zero skips on each supported Anki
  release family: 24.11, 25.02.7, 25.07.5, 25.09.4, 25.09.5, 26.05, and 26.08.
- Anki's official add-on installer passed clean-install and
  v1.0.19-to-v1.0.20 upgrade tests in all seven environments.
- Customized settings and `user_files` data were preserved, with no duplicate
  installation or stranded backup directory.

## Compatibility

- Smart and Exact: Anki Desktop 24.11 through 26.08.
- Semantic Search: macOS 14 or later on Apple silicon.
- Windows, Linux, and Intel Mac integration testing is not part of this
  public-beta support claim; Smart and Exact remain available when Semantic is
  unsupported.

Install or update from AnkiWeb with code **677438639**, or use Anki's
**Tools → Add-ons → Install from file…** command with the attached archive.
