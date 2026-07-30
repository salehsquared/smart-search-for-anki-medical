# Security

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability, accidental
exposure of card content, or collection-integrity concern.

Email `product@medbrevia.com` with the subject
`Smart Search security report`. Include:

- the Smart Search and Anki versions;
- operating system and processor architecture;
- a minimal description and numbered reproduction steps; and
- a safe way to reach you.

Do **not** attach an Anki collection, profile directory, search database, vector
index, credentials, patient information, or identifiable card content.

Reports are handled on a best-effort basis. Collection-integrity, startup-crash,
and private-data issues receive priority.

## Design boundary

Smart Search does not write directly to Anki's SQLite database. Explicit tag,
flag, suspend, and unsuspend actions use Anki's supported, undoable collection
operations. Search indexes and semantic vectors are disposable, profile-scoped
files under the add-on's `user_files` directory.

The add-on contains no analytics, advertising, or telemetry. Semantic setup is
optional and is the only add-on-initiated network operation: after an explicit
user action, pinned model files are downloaded over HTTPS and verified by
SHA-256. Card text, queries, tags, profile names, and embeddings are not sent.

See [PRIVACY.md](PRIVACY.md) for the full data-flow disclosure.
