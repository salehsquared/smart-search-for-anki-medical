# Changelog

## [1.0.23] — 2026-08-03

### Added

- Result menus can now move the exact selected cards to another deck through a
  searchable, single-destination version of the hierarchical deck picker.
  Filtered decks remain available as search filters but are never offered as
  move destinations.
- Result menus can now bury or unbury cards, including mixed selections. The
  operation distinguishes both native buried queues from suspension and uses
  one undoable Anki collection action.

### Fixed

- Deck changes now preserve Anki's timestamp-sized 64-bit deck IDs across the
  Qt interface, so valid decks are no longer rejected as unavailable.
- Filtering the move destination list and pressing Return now chooses the
  matching nested deck instead of a visible ancestor. A failed deck-list
  refresh also keeps the last usable choices available.

## [1.0.22] — 2026-08-03

### Added

- The inline card preview now uses reviewer-style keyboard controls while the
  result list is focused: Space or Right Arrow reveals the answer, Left Arrow
  returns to the front, and Up/Down continues moving between results.
- The same Space/Right/Left controls now remain active after clicking inside
  the rendered card; Space reveals the answer instead of scrolling the card.
- Shift+Space now checks or unchecks a result for bulk actions, preserving a
  dedicated keyboard selection shortcut without conflicting with card reveal.
- Semantic search now keeps its isolated model worker warm for a normal search
  session instead of cold-starting it again after only two seconds. Closing
  search or entering review mode still releases it immediately. Superseded
  typing discards the stale result without tearing down the loaded model.
- Flag, suspension, and tag actions now update the visible rows in place and
  never rerun or replace the result list automatically. Press Enter or search
  again to reapply filters such as `is:suspended`, `flag:`, or `tag:`.

## [1.0.21] — 2026-08-02

### Memory and CPU

- Fixed high idle RAM after Semantic work. ONNX Runtime, Tokenizers, the model,
  and their native allocator caches previously lived inside Anki and could
  remain mapped even after the active model object was released.
- Semantic inference now runs in a disposable, pinned local worker. The worker
  starts only for Semantic work, is stopped before review and after use, and
  lets macOS reclaim its native memory when it exits.
- Anki retains only a NumPy-based vector-ranking layer. Vector scans use
  512-note chunks, while inference uses one thread, one sequence at a time,
  bounded 16 KiB inputs, and a 256 MiB worker memory ceiling.

### Compatibility and safety

- Existing downloaded models and profile indexes remain reusable; upgrading
  performs a one-time local runtime migration without changing cards or review
  data.
- Worker startup, cancellation, crashes, profile transitions, and repeated
  launch/exit cycles are covered by protocol, lifecycle, and real-runtime
  integration tests across the supported Python 3.9 and 3.13 hosts.

## [1.0.20] — 2026-08-02

### Performance

- Reviewing now triggers no Smart Search indexing, collection reads, fuzzy
  refreshes, preview refreshes, or Semantic work.
- Semantic inference loads only when requested, is released after use, and is
  canceled cooperatively when reviewing begins. Full vector maintenance now
  processes bounded batches instead of retaining all note text or candidates.
- Typo-matching vocabulary preparation is deferred until Smart Search opens.

### Index freshness and reliability

- Added a crash-safe profile-local maintenance journal so exact edits resume
  after restart and are hydrated in bounded batches outside review mode.
- Added compact note, card/home-deck, deck, and note-type manifests for
  targeted sync/import/undo audits without rebuilding every note.
- First-run manifest seeding now verifies fixed-size field/tag digests,
  note-type schema, card scope, and canonical decks against the existing index,
  closing aggregate-fingerprint collision and same-second edit gaps.
- Filtered-deck reviews use each card's stable home deck and no longer make the
  search index appear stale.
- Damaged maintenance state is preserved and recreated without disabling a
  healthy Smart/Exact index.

### Safety

- Journal writes and all substantial index/model work run off Anki's GUI
  thread. Background work yields between batches and resumes only after the
  reviewer has settled.
- Full setup and manual rebuilds now read the collection in cancellable keyset
  pages and write vocabulary/manifests in bounded transactions, so entering
  review can interrupt even first-run work without publishing partial data.
- Transient collection/index errors retain their exact durable work and retry
  with bounded backoff; failed journal writes retain a conservative full-audit
  fallback instead of losing an edit hint.
- Semantic cancellation now reaches inference and prevents a canceled batch
  from publishing vectors or being mislabeled as index damage.
- Successive exact edits now coalesce across durable batches with the newest
  live/deleted state winning. Semantic publication also rechecks the durable
  queue at commit time and falls back to one full refresh when exact coverage
  cannot be proven.
- Failed journal writes register their conservative audit fallback before the
  semantic gate reopens, and older maintenance completions cannot erase newer
  fallback intent.
- Lexical rebuilds detach Semantic before mutation and publish their new
  generation immediately after the atomic swap, preventing stale vectors from
  appearing ready if later cleanup is canceled.
- Semantic repair/install, hash scans, removals, and lexical deletions are
  cooperatively cancellable; vector deltas and durable acknowledgements are
  processed in bounded transactions.

## [1.0.19] — 2026-08-01

### Added

- Added a simple **Check & Update** action to the About tab for public
  AnkiWeb installations.

### Safety and reliability

- Updates delegate to Anki's native, compatibility-aware installer and retain
  Anki's normal restart and error handling.
- Manual and development installations hide the update action so the public
  numeric add-on cannot be installed beside a second live copy.
- Repeated update requests are coalesced, local `user_files` remain under
  Anki's native preservation rules, and no custom self-replacement code runs.
- Update installation claims an exclusive maintenance lane, waits for active
  work to finish, stops deferred writers, and closes profile-owned search files
  off the UI thread before Anki replaces the add-on bundle.
- Updated the privacy disclosure for AnkiWeb update checks.

## [1.0.18] — 2026-08-01

### Added

- Added a tested compatibility layer for Anki Desktop 24.11 through 26.08,
  covering the 25.02, 25.07, 25.09, 26.05, and 26.08 release families.
- Added a pinned Python 3.9 Apple-silicon runtime so Semantic Search remains
  available on Anki 24.11 and 25.02 rather than becoming a reduced-feature
  fallback.

### Changed

- Optional Anki hooks and UI integrations are feature-detected so the core
  search path can remain available if a nonessential host API is absent.
- Python 3.10-only dataclass, integer, and iteration conveniences now use
  compatible fallbacks on Anki's Python 3.9 releases.
- The release matrix now covers Python 3.9 and 3.13, the Qt 6.6, 6.9, and 6.11
  generations, and every supported Anki release family.

## [1.0.17] — 2026-08-01

### Added

- A selected parent deck can now keep individual child branches excluded:
  uncheck an inherited subdeck to omit that subdeck and all of its descendants.

### Changed

- Included children remain interactive, selected parents show a partial state
  when exclusions are active, and rechecking an exclusion restores its branch.
- Deck exclusions are expressed as visible native Anki search syntax and apply
  consistently in Smart, Exact, and Semantic modes.

### Fixed

- Excluded decks that are no longer available in the profile remain visible in
  the picker so their stale exclusion can be removed safely.

## [1.0.16] — 2026-07-31

### Added

- Added a searchable, hierarchical deck picker directly inside the search
  field, with All decks, Current deck, nested multi-selection, and keyboard
  controls.
- Deck choices load through Anki's background collection operation, so opening
  the picker does not block the interface or interrupt a running search.

### Changed

- Picker selections are written as visible native Anki `deck:` syntax, keeping
  deck scope identical in Smart, Exact, and Semantic modes.
- Advanced hand-written deck expressions are labeled as custom and preserved;
  users edit that logic directly in the visible search field.

## [1.0.15] — 2026-07-30

### Changed

- Moved sibling position, audio replay, and answer controls into the compact
  preview header so the card body has more room.
- Preview X now dismisses the pane only until the next result selection or
  same-row click; disabling Card preview in Settings remains persistent.
- Search settings now use native macOS combo-box and stepper rendering with
  aligned, font-aware sizing and shorter semantic-status copy.

### Fixed

- Removed the custom compound-control styling that could leave clipped dark
  strips beside Default mode and Result limit on macOS.
- Fixed a managed-close recursion that could leave the Smart Search dialog
  visible after its editor-save cleanup completed.

## [1.0.14] — 2026-07-30

### Added

- Replaced the separate Preview window with a resizable right-hand pane inside
  Smart Search.
- Added rendered **Card** and native **Edit** views, sibling-card navigation,
  and in-window expand, restore, and close controls.
- The native editor is created only when first used and saves through Anki's
  supported note operation.

### Fixed

- Opening one or more results in Anki's Browser no longer closes Smart Search
  or clears its query, results, highlighted row, or checked selection.
- Window close and Escape handling are now separate: Escape still clears a
  query first, while an actual close request closes the dialog normally.

## [1.0.13] — 2026-07-30

### Added

- Added a compact search-icon **Smart** entry immediately after Browse in
  Anki's main toolbar.

### Changed

- Reduced ordinary mouse-wheel movement in search results from about three
  result cards to about one card per notch while preserving native precision
  trackpad scrolling.

## [1.0.12] — 2026-07-30

### Changed

- Card Preview is enabled by default and opens automatically only after the
  user enters or moves through the result list, so ordinary search typing
  keeps its focus.
- Added a simple **Card preview** switch in Search settings.
- The native Preview remains in front and owns keyboard focus; Up/Down still
  moves through Smart Search results.

### Fixed

- Stopped and disposed dialog-owned status work before Qt deletes its widgets,
  preventing the stale `IndexStatusWidget has been deleted` traceback after
  profile or dialog teardown.
- Guarded queued search/rebuild callbacks after a dialog closes.
- Preview creation is queued outside result-selection reentrancy and cancelled
  safely when the dialog, profile, or preference changes.
- On macOS, Ctrl+Shift+P now uses the physical Control key instead of
  colliding with Anki's Command+Shift+P profile switcher.

## [1.0.11] — 2026-07-30

### Added

- Native Anki card Preview for the highlighted search result, including the
  rendered template, cloze behavior, media, MathJax, audio, flags, and theme.
- Up/Down navigation through search results while Preview is open.
- Exact-scope sibling-card navigation for results that represent more than one
  card, with a compact “Card 1 of N” window title.
- A Preview toolbar button and Ctrl+Shift+P shortcut.

### Reliability

- Preview follows the highlighted row without changing independent bulk-action
  checkboxes.
- Preview closes with the search dialog, tolerates cards deleted after a
  search, refreshes after card actions, and uses separate saved window geometry
  from Anki's Browser Preview.

## [1.0.10] — 2026-07-29

### Added

- Keyboard-first search palette opened with Command/Ctrl-K.
- Smart mode with case-insensitive matching, medical typo recovery, and RxTerms
  generic/brand aliases.
- Exact mode with literal matching and native Anki search syntax.
- Optional, local Semantic mode with a separate setup and indexing lifecycle.
- Card-aware handling of native deck, tag, note, flag, suspension, property,
  review-history, field, wildcard, and Boolean filters.
- Adaptive relevance cutoffs that hide weak trailing matches.
- Match explanations, correction chips, filter chips, and note-grouped previews.
- Multi-select controls with checkboxes, Shift-click ranges, All shown, None,
  Invert, and Command/Ctrl-Return.
- Browser opening for the exact selected card set.
- Undoable card flags, suspend/unsuspend actions, and note tags from the toolbar
  or result context menu.
- Palette-aware result cards, compact live flag/suspension indicators, a clear
  Semantic setup surface, and polished Settings/About screens.
- Automatic targeted refresh after ordinary note additions, edits, and deletes.
- Settings and About surfaces with privacy, support, MedBrevia, and creator
  details.

### Performance and reliability

- Search, external-index work, spelling-vocabulary construction, and Semantic
  inference are kept off Anki's graphical interface thread.
- Stale search requests are cancelled so mode switches and newer queries are
  not overwritten by older results.
- Smart and Exact remain usable while the separate Semantic index is preparing.
- Drug-name suggestions use concise single-word names while full product names
  remain searchable, and automatic typo corrections now continue through
  generic/brand alias expansion.
- Expensive refreshes are coalesced and delayed until edit activity settles.
- Profile-scoped search data is disposable and can be rebuilt without changing
  `collection.anki2`.

### Privacy and distribution

- Searches, card contents, and indexes remain local.
- Semantic setup requires an explicit user action before any model download.
- Replaced the earlier third-party model conversion with a reproducible,
  provenance-complete ONNX INT8 export pinned to an immutable public revision.
- Added complete RxTerms attribution, model lineage, wheel licenses, and a
  deterministic 120-component Tokenizers notice bundle.
- Public builds exclude profile-derived indexes, logs, caches, and expanded
  development/runtime artifacts.
- Added complete release-facing privacy, support, known-limitations, and
  third-party attribution materials.

### Compatibility

- Smart and Exact: supported on the Anki Desktop versions listed on the release
  page.
- Semantic Search: macOS 14 or later on Apple-silicon Macs only.

### Known limitations

- Local indexes do not sync to other computers.
- Semantic ranking is approximate and may miss or mis-rank relevant cards.
- Sync, imports, native bulk edits, undo/redo, and structural collection changes
  may schedule a later full reconciliation.
