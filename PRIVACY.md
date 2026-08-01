# Privacy

**Effective date:** July 29, 2026
**Applies to:** Smart Search for Anki — Medical 1.0.17

## Plain-language summary

Smart Search is designed to search your Anki collection locally.

- It does not create a Smart Search account.
- It does not contain analytics, advertising, telemetry, or crash reporting.
- It does not upload your notes, cards, search queries, tags, deck names, or
  semantic embeddings.
- Semantic setup makes one kind of network request: it downloads pinned model
  files from Hugging Face after you explicitly start setup.
- The add-on stores disposable search indexes on your computer. Those indexes
  contain or are derived from your note content and should be treated as
  private.

This document describes the add-on itself. Anki, AnkiWeb synchronization, your
operating system, links you open, and download hosts have their own privacy
practices.

## Data the add-on reads

To build and maintain its search index, Smart Search reads through Anki's
supported collection interfaces:

- note field names and field contents, including text extracted from HTML and
  cloze markup;
- note and card identifiers;
- tags, deck names, and note-type names;
- note modification metadata and note GUIDs needed to detect changes; and
- current card flags and suspension state when displaying or acting on search
  results.

The add-on does not need or persist review answers, review timing, ease
ratings, or scheduling history in its own search indexes.

## Local processing and storage

Searches, spelling-distance calculations, alias expansion, indexing, and
semantic inference run on the user's computer.

Smart Search stores add-on-owned files below its Anki `user_files` directory:

- `profiles/<profile-key>/search.sqlite3` contains a disposable full-text
  index. It includes note text, raw field values, field names, note/card
  identifiers, tags, deck names, note-type names, and derived vocabulary.
- `profiles/<profile-key>/vectors/` contains note identifiers, content hashes,
  and embeddings derived from note text. It does not contain a second plain
  text copy of the notes, but embeddings are still derived from private
  content and should be protected accordingly.
- `model/` contains the optional semantic model and tokenizer.
- `runtime/` contains the optional local inference libraries.

The profile key is a one-way hash of the Anki profile name and collection path.
It is used to keep indexes separate; it is not sent anywhere.

Anki's add-on configuration stores interface preferences such as search mode,
result limit, window size, and saved filter chips. Free-text search queries are
not intentionally saved as search history.

These files are not separately encrypted by Smart Search. Their protection
depends on the user's device, operating-system account, disk encryption, and
backup practices.

## Network activity

Smart and Exact search do not require a network connection.

When a user explicitly starts Semantic setup, the add-on downloads pinned
model and tokenizer files over HTTPS from:

`https://huggingface.co/medbrevia/medembed-small-v0.1-onnx-int8`

The add-on requests only files under immutable revision
`6cbe4664f1e0067da935f5abc24e4f8b5406b13f`. The public repository contains
the model, tokenizer/configuration files, licenses, modification notice,
reproduction script, and provenance record. It does not contain or receive
the user's collection data. The public provenance record is:

<https://huggingface.co/medbrevia/medembed-small-v0.1-onnx-int8/blob/6cbe4664f1e0067da935f5abc24e4f8b5406b13f/PROVENANCE.json>

The requests include ordinary web-request metadata, such as the user's IP
address, a Smart Search user-agent string, requested file paths, and an
optional byte range for resuming a partial download. They do **not** include
Anki note or card content, embeddings, search queries, tags, deck names, Anki
profile names, or collection identifiers. Hugging Face may process network
metadata according to its own privacy policy.

The bundled RxTerms snapshot is read locally. The add-on does not query NLM's
RxTerms API during search.

Clicking a website, mobile-app, privacy, source, or support link opens an
external site in the user's browser. That browser visit is governed by the
destination's privacy policy.

## Changes to the Anki collection

Searching and indexing are read-only. If a user explicitly chooses an action
such as adding/removing tags, changing a flag, or suspending/unsuspending
cards, the add-on asks Anki to perform that operation through Anki's supported,
undoable collection operations.

Those user-requested changes become ordinary Anki collection data. If the user
has Anki synchronization enabled, Anki may synchronize them under Anki's own
terms and privacy practices. Smart Search does not independently transmit
them.

## Retention and deletion

The local indexes remain until they are rebuilt or deleted. With Anki closed,
a user may delete the add-on's `user_files/profiles/` directory; Smart Search
will recreate the indexes from the Anki collection when needed.

The optional `user_files/model/` and `user_files/runtime/` directories may
also be deleted to remove Semantic Search assets. Smart and Exact search will
remain available, and Semantic setup will be required again before Semantic
Search can run.

Deleting an index does not delete or change the underlying Anki notes. Deleting
the Anki notes does not necessarily securely erase old copies from device
backups or filesystem snapshots.

## Integrity and security

Bundled runtime wheels and downloaded model files are pinned by version and
verified with SHA-256 before use. This protects against accidental corruption
and unannounced file changes at the pinned locations; it is not a substitute
for operating-system security or an independent security audit. The semantic
model's pinned source revision, transformation parameters, toolchain,
artifact hashes, and numerical parity sanity check are published in the
provenance record linked above.

## Children and sensitive information

Smart Search does not knowingly collect information from anyone. Because Anki
notes may contain sensitive personal, educational, or health information,
users should avoid putting identifiable patient information into flashcards
and should protect their device and backups appropriately.

## Contact

Questions about this privacy notice or the add-on can be sent to
`product@medbrevia.com`.

MedBrevia mobile app: <https://medbrevia.com/app>
