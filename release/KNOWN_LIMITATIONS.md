# Known limitations

## Platform and installation

- Smart Search runs in Anki Desktop only. AnkiMobile and AnkiDroid do not load
  desktop add-ons.
- v1.0.25 supports Anki Desktop 24.11 through 26.08, including the 25.02,
  25.07, 25.09, 26.05, and 26.08 release families.
- The supported-version matrix was exercised on macOS with Apple silicon.
  Windows, Linux, and Intel Mac integration are not part of the v1.0.25 support
  claim.
- Semantic Search currently supports **macOS 14 or later on Apple-silicon Macs
  only**.
- On an unsupported computer, Semantic should present an unavailable state;
  Smart and Exact remain usable.
- Only the Anki versions declared on the release's AnkiWeb listing are supported.
- A manually installed development copy uses a named add-on folder, while
  AnkiWeb installs into its assigned numeric folder. Loading both copies at
  once can register duplicate actions; remove or disable the manual copy before
  installing from AnkiWeb.

## Indexes and sync

- Smart/Exact and Semantic use separate local indexes. Each Anki profile needs
  its own initial preparation.
- The indexes do not sync through AnkiWeb. A second computer prepares its own
  local copy.
- Smart and Exact become available after their initial fast setup. Semantic
  preparation is separate, typically longer, and temporarily starts a local
  worker that uses additional CPU and memory while it computes embeddings.
- Smart and Exact remain available while Semantic indexes.
- Adds, edits, and deletes are normally refreshed incrementally. Sync, import,
  undo/redo, native bulk operations, deck changes, and note-type changes may
  require a later compact manifest audit because Anki does not always expose
  the affected note IDs. Only detected candidates are then hydrated.
- Collection reads share Anki's serialized collection worker. They run away from
  the graphical interface but can briefly queue behind other collection work.
- Local model inference still competes for finite system CPU and memory while
  active. It is limited to one ONNX thread, one text sequence per inference,
  bounded messages, and a 256 MiB macOS process-memory ceiling, but a full
  first-time Semantic build is intentionally substantial work.
- Inference runs in a standalone helper process, not inside Anki. The helper is
  stopped before review and after a short warm search session, so its model,
  native libraries, and allocator caches are reclaimed by the operating
  system. A small NumPy-only vector layer remains available in Anki for
  Semantic ranking.
- The bundled standalone interpreter increases add-on download and installed
  disk size. The downloaded model and per-profile vector index add further
  local disk use.

## Search behavior

- Smart search improves misspellings and expands supported aliases, but no
  spelling or terminology system can infer every intended term.
- RxTerms improves medication-name matching but is not a comprehensive drug,
  disease, procedure, laboratory, or guideline ontology.
- Semantic ranking is approximate. It can miss relevant cards or rank a loosely
  related card too highly.
- Adaptive relevance cutoffs intentionally hide weak trailing results. A broader
  query, Exact mode, or native Anki Browser search may reveal additional cards.
- Native Anki operators are delegated to Anki. Results may therefore change when
  Anki changes its search grammar or semantics.
- Results are grouped for readability, but card-specific filters and actions
  operate on the exact matching card IDs.

## Actions and state

- Tags apply to notes; flags, suspension, burial, and deck moves apply to cards.
  A selected note can represent multiple sibling cards, so the selection
  summary should be reviewed before a bulk action.
- Cards can be moved only to normal decks. Filtered decks remain available as
  search scopes but are intentionally excluded as move destinations.
- **Create Copy…** accepts one selected note and opens Anki's Add Cards editor;
  it is not an immediate card clone. Multi-template and cloze notes may create
  their normal sibling cards after the user clicks **Add**. Scheduling, review
  history, flags, suspension, and burial are not copied.
- “All shown” means the currently displayed result set, not every possible
  result when a relevance cutoff or display limit is active.
- Anki's Browser remains the authoritative interface for reviewing complex
  queries and collection state.

## Clinical use

- The add-on searches the user's existing study material. It does not verify
  whether a card is current or medically correct.
- Search results and generated similarity rankings are not medical advice and
  should not be used as a substitute for current clinical references or
  professional judgment.
