# Privacy

Smart Search for Anki — Medical is designed to search locally.

## Data that stays on the computer

- Search queries
- Anki note and card text
- Deck, tag, note-type, flag, and suspension metadata
- Typo-correction data derived from the local collection
- Smart/Exact search databases
- Semantic vector indexes
- Settings and preparation status

The add-on does not include analytics, advertising identifiers, or telemetry.
It does not send searches or card content to MedBrevia.

## Network access

Smart and Exact require no model download.

Anki may contact AnkiWeb to check installed add-ons for updates. The
**Check & Update** button in Smart Search's About tab delegates to Anki's
native updater for public item `677438639`; no card text, query, tag, deck,
profile, collection identifier, or embedding is included by Smart Search.

Semantic Search is optional. When a user explicitly chooses to set it up, the
add-on installs the reviewed runtime files bundled in the add-on and downloads
the disclosed model/tokenizer assets from their documented source over HTTPS.
It verifies the expected files before enabling local inference. Missing
Semantic files are not installed merely because Anki started or a profile was
opened.

Model and tokenizer files are requested only from the immutable Hugging Face
revision
`medbrevia/medembed-small-v0.1-onnx-int8@6cbe4664f1e0067da935f5abc24e4f8b5406b13f`.
Ordinary network metadata such as an IP address, request time, user-agent, file
path, and optional resume byte range may be visible to the download host. Card
text, queries, tags, deck names, profile names, collection identifiers, and
embeddings are not included in those requests.

Exact asset sizes and cryptographic digests are recorded in
`DATA_SOURCES.md`; licenses and attribution are in
`THIRD_PARTY_NOTICES.md`. If the endpoint or assets change, these documents
must be updated before release.

Opening the **Mobile App** or **Feedback** link is also an explicit user action
and opens the corresponding page in the system browser.

## Local storage

Disposable indexes and optional Semantic assets are stored under the add-on's
`user_files` area so Anki preserves them across add-on updates. Indexes are
profile-scoped and are not written into `collection.anki2`.

These files can contain derived representations of card text. They should be
treated with the same privacy as the Anki profile itself, excluded from public
bug reports and release archives, and removed according to the user's normal
device-disposal practices.

## Changes to the Anki collection

Searching is read-only. User-requested tag, flag, suspend, and unsuspend actions
modify the Anki collection through Anki's supported undoable operations. The
add-on does not directly edit the collection database or add custom tables to
it.

## Sharing diagnostics

Do not attach an Anki collection, profile folder, local search database, vector
index, or unredacted screenshot to a public issue. Prefer synthetic examples.
For a suspected privacy or security issue, email `product@medbrevia.com` rather
than opening a public issue.

## Contact

Privacy questions: `product@medbrevia.com`

MedBrevia mobile app: https://medbrevia.com/app
