# Duplicate Detection: Hashing & Acoustic Fingerprinting

This document surveys approaches for **content-based** duplicate detection in
track-manager's audio library, and recommends a pragmatic strategy for catching
the same song downloaded from different sources (Spotify vs TIDAL vs YouTube, at
different bitrates and in different formats).

It is a research / design doc — nothing here is implemented yet.

## Where we are today

track-manager currently detects duplicates using **embedded metadata only**.
See [`track_manager/duplicates.py`](../track_manager/duplicates.py):

- **`find_duplicates_by_track_url`** — compares the `TRACK_URL` provenance tag
  (MP4 freeform atom `----:com.apple.iTunes:TRACK_URL`, or ID3 `TXXX:TRACK_URL`).
- **`find_duplicates_by_isrc`** — compares the ISRC code (MP4
  `----:com.apple.iTunes:ISRC`, or ID3 `TSRC` frame).
- **`find_duplicates`** / **`scan_library`** — compares normalized `artist` +
  `title` tags. `normalize_text()` lowercases and strips junk patterns
  (`[Official Video]`, `(HD)`, `- Topic`, `feat.` variants, etc.) while
  deliberately *preserving* meaningful distinctions like `Live`, `Acoustic`,
  `Remix`, `Edit`.

The handling mode (`interactive` / `skip` / `keep`) is configured under
`duplicates:` in `config.yaml`.

**There is no file-content hashing or audio fingerprinting today.** Dedup is
purely a function of tags.

## Why metadata-only dedup misses things

Tags are cheap and fast, but they break in exactly the cases this project cares
about — the *same recording* arriving from different pipelines:

- **Different source, same recording.** The same master pulled from TIDAL
  (FLAC), Spotify→TIDAL, and YouTube can carry different `TRACK_URL` values and
  inconsistent (or absent) ISRCs. YouTube rips frequently have no ISRC at all.
- **Different encoding / bitrate / format.** AIFF (PCM) vs M4A (AAC 256k) vs MP3
  (CBR 320) of the same audio are *byte-different* and often *tag-different*, so
  none of the current checks fire reliably across them.
- **Tag drift.** "Artist – Title" punctuation, featured-artist formatting,
  remaster suffixes ("- 2011 Remaster"), and capitalization differ between
  providers. `normalize_text()` catches a lot of this but not everything.
- **Missing ISRC.** ISRC is the strongest signal we have, but it is only present
  when a provider supplies it, and *different masters/remasters of the same song
  legitimately have different ISRCs* — so ISRC equality is a good positive
  signal but its absence proves nothing.

Conversely, metadata can over-match: a **remix**, **live**, or **acoustic**
version may share artist+title with the studio original. The current code keeps
those distinctions in the title string, which only helps when the provider
labelled them.

What metadata genuinely *cannot* do is confirm that two files contain the **same
audio**. That requires looking at the bytes or the sound itself.

## The options

Approaches fall into three tiers, from "cheap and exact" to "expensive and
perceptual":

1. **Exact file hashing** — hash the file bytes.
2. **Decoded-audio hashing** — hash the raw decoded PCM samples.
3. **Perceptual / acoustic fingerprinting** — model how the audio *sounds*.

### 1. Exact file hashing (MD5 / SHA-256)

Hash the raw file bytes (`hashlib.sha256(path.read_bytes())`).

- **Catches:** byte-identical files only — i.e. the literal same file copied
  twice, re-downloaded identically, or saved under two names.
- **Misses:** *everything else.* Re-tagging a file (which track-manager does on
  every download — it writes `TRACK_URL`, ISRC, cover art, etc.) changes the
  bytes, so even two downloads of the same source can hash differently. Any
  re-encode, container change, or bitrate change defeats it completely.
- **Cost:** effectively free. Standard library, no dependencies, microseconds
  per MB, trivially cacheable.

This is worth doing as a fast pre-filter, but on its own it will catch very
little in *this* project because we mutate tags post-download.

### 2. Decoded-audio hashing (PCM hashing)

Decode the file to raw PCM samples (via `ffmpeg`/`librosa`/`soundfile`) and hash
*those* — optionally after normalizing sample rate and channel layout.

- **Catches:** files whose **audio stream is identical** but whose container or
  tags differ — e.g. the same WAV wrapped as AIFF, or a FLAC and a WAV decoded
  from the same source, or two M4As with different tags but the same AAC stream
  decoded to identical PCM.
- **Misses:** anything that changes the actual samples — lossy re-encoding (AAC
  vs MP3 vs the FLAC they came from), bitrate changes, resampling, volume
  normalization, or even re-encoding at the *same* nominal bitrate with a
  different encoder. Lossy codecs are not deterministic across encoders, so two
  "320 kbps MP3s" of the same song will essentially never produce identical PCM.
- **Cost:** moderate — you pay for full decode (similar cost to fingerprinting),
  but no extra heavy dependencies beyond an audio decoder you likely already
  have (FFmpeg). Cache the hash keyed on file path + mtime.

Decoded-audio hashing is a meaningful step up from byte hashing for *this*
library specifically, because track-manager often transcodes (e.g. FLAC → M4A)
and re-tags. It still only catches *bit-exact audio*, not perceptual matches
across encodings — which is the real goal.

### 3. Perceptual / acoustic fingerprinting

These model the *sound* and tolerate encoding/bitrate differences. This is the
only tier that can recognize "same song from Spotify vs YouTube at different
bitrates" as a duplicate.

#### 3a. Chromaprint / AcoustID (the de facto open-source standard)

[Chromaprint](https://github.com/acoustid/chromaprint) is the fingerprinting
library behind [AcoustID](https://acoustid.org/), used by MusicBrainz Picard and
[beets](https://beets.readthedocs.io/). Python access is via
[`pyacoustid`](https://pypi.org/project/pyacoustid/), which calls either
`libchromaprint` or the `fpcalc` command-line tool.

Two distinct things, often conflated:

- **Chromaprint (local):** generates a compact fingerprint from a file's audio.
  You can compare fingerprints *locally* (no network) to find duplicates within
  your own library. This is what we'd primarily want.
- **AcoustID (web service):** looks a fingerprint up against a crowd-sourced
  database to resolve it to MusicBrainz recording IDs (MBIDs). Requires a free
  **API key** and network access. Useful for *identifying* a track, not strictly
  needed for *intra-library* dedup.

Chromaprint's authors are explicit about scope: it is built to identify
**near-identical audio** (full-file dedup, stream monitoring) and *deliberately*
trades precision/robustness for compact fingerprints and fast search. It is
**not** designed to detect "similar-sounding" recordings or different renditions
([upstream issue #68](https://github.com/acoustid/chromaprint/issues/68)). That
scope is a near-perfect fit for our use case (same recording, different
encoding) and a poor fit for "is this the live version of that studio track."

- **Status (2026):** actively maintained. Chromaprint **1.6.0** shipped
  Aug 2025 (added FFmpeg 8.0 support); `pyacoustid` is stable.

#### 3b. Dejavu (Shazam-style spectrogram peak hashing)

[Dejavu](https://github.com/worldveil/dejavu) implements landmark/peak-pair
hashing over a spectrogram (the classic Shazam approach) and stores fingerprints
in a SQL database (MySQL/PostgreSQL). It excels at recognizing a short, noisy
*query clip* against a known catalogue.

- **Status (2026):** **largely unmaintained.** Recent issues report it failing
  to match even identical files on modern stacks, plus long-standing NumPy
  incompatibilities (it tends to need pinned/older NumPy). A maintainer has
  signalled intent to revive it, but as of now it's effectively legacy and not a
  safe dependency for a small project.
- **Operational weight:** requires standing up and populating a database, which
  is heavy for a personal library that just wants pairwise dedup.

For our problem (whole-file dedup, not clip-spotting), Dejavu is over-built and
under-maintained.

#### 3c. Other notable options

- **audfprint** (Dan Ellis / LabROSA) — Python, landmark-based, Shazam-style.
  Strong at identifying short/noisy excerpts against a database; matches need
  only a few common landmarks. Robust to noise and truncation, but (like most
  landmark systems) **not robust to time-stretch / pitch-shift beyond ~5%**.
  More of a research tool; usable but niche, and you manage the hash database
  yourself.
- **Panako** (Joren Six) — **Java** (AGPL-3.0), explicitly engineered to be
  robust to **time-scale, pitch-shift, and speed changes** (e.g. DJ sets,
  vinyl-speed drift). Technically excellent and actively published, but the JVM
  dependency and AGPL licensing make it awkward to embed in a Python CLI.
- **Echoprint** (Spotify / The Echo Nest) — **deprecated.** The codegen repo is
  explicitly "no longer actively maintained" and the resolver service is
  discontinued for new submissions. Avoid for new work.
- **Commercial APIs** — [ACRCloud](https://www.acrcloud.com/) (150M+ track DB,
  Spotify/Apple/Deezer/ISRC cross-IDs, 14-day free trial then paid),
  **Audible Magic** (enterprise rights-management, negotiated pricing),
  **Gracenote** (extensive but opaque/expensive), **AudD** (developer-friendly
  REST, ~$5/1000 requests). These *identify* tracks against huge catalogues with
  high accuracy, but add cost, network dependency, per-track latency, and
  privacy considerations (you upload your audio). Overkill for personal-library
  dedup; relevant only if you also want rich catalogue identification.
- **`librosa`-based custom approaches** — you can roll your own (chroma features,
  MFCCs, spectral hashing) with `librosa`/`numpy`. Educational and flexible, but
  you'd be reinventing Chromaprint with more bugs and no community-tuned
  thresholds. Not recommended as a primary mechanism; fine for experimentation.

## Comparison

Recall = catches true duplicates. False hits = wrongly matches different tracks.
Numbers are intentionally qualitative — see the note on accuracy figures below.

### Recall (does it catch true duplicates across encodings/bitrates/formats?)

| Approach | Recall across encodings/bitrates/formats |
|---|---|
| Exact file hash (MD5/SHA-256) | **Very low.** Only byte-identical files. Defeated by re-tagging (which we always do) and any re-encode. |
| Decoded-audio (PCM) hash | **Low–moderate.** Catches re-containerized / re-tagged but bit-identical audio; defeated by any lossy re-encode, resample, or volume change. |
| Chromaprint (local compare) | **High** for the same recording across codecs/bitrates/formats — its core design target. |
| AcoustID (web lookup) | **High** *if the track is in the database*; lower for obscure/new/underground tracks not yet submitted. |
| Dejavu | High in principle, but **unreliable in practice today** (maintenance). |
| audfprint | High for the same recording; also good on partial/noisy clips. |
| Panako | High, plus robust to speed/pitch changes others miss. |
| Commercial (ACRCloud/AudD/…) | **Very high** against their large catalogues. |

### Known false-hit / false-positive scenarios

| Approach | Known false-hit behavior |
|---|---|
| Exact file hash | Essentially **none** (a SHA-256 collision is not a practical concern). |
| Decoded-audio hash | Essentially none; bit-exact PCM equality is a near-certain true match. |
| Chromaprint / AcoustID | Real, scenario-specific: **very short tracks** (a few seconds) and **silent/near-silent** sections produce low-entropy fingerprints that can collide; tracks **sharing the same sample/loop** can score as similar; **classical music** with many recordings of the same piece is notoriously confusable; and **AcoustID can map one fingerprint to multiple MBIDs** (and vice versa), so a lookup may return several candidate identities. Conversely it tends to *miss* (false-negative) live/acoustic/remix variants — which for dedup is the safer error. |
| Dejavu | Tunable thresholds; aggressive settings raise false matches. With low confidence, short/repetitive audio can mis-match. |
| audfprint | Landmark systems can false-match highly repetitive or sample-heavy material at low match counts; mitigated by requiring more matching landmarks. |
| Panako | Generally precise; robustness to speed/pitch slightly widens what counts as "same," so verify near threshold. |
| Commercial | Low, but can return the wrong release/version (e.g. a different remaster) and occasionally misfire on covers. |

### Viability / integration cost

| Approach | Dependencies & cost |
|---|---|
| Exact file hash | `hashlib` (stdlib). Zero install, trivial, fast. |
| Decoded-audio hash | An audio decoder (FFmpeg, or `soundfile`/`librosa`). Full-decode CPU cost; no network. |
| Chromaprint (local) | `pyacoustid` (pip) **+ the `fpcalc` binary or `libchromaprint`** (native; via Homebrew `chromaprint` / apt `libchromaprint-tools`), which needs FFmpeg. **LGPL-2.1** library. No network for local compare. Fast. |
| AcoustID (web) | Above **+ free API key + network**. Rate-limited; per-request latency. |
| Dejavu | pip install + **SQL database** to stand up and populate; pinned/older NumPy; maintenance risk. Heavy. |
| audfprint | Python + NumPy/scipy + you manage a hash DB. Moderate; niche. |
| Panako | **JVM/Java runtime**, AGPL-3.0. Awkward to embed in a Python CLI. |
| Commercial | Account + **paid plan** + network + uploading your audio (privacy). Lowest code effort, highest ongoing cost/dependency. |

### Robustness summary

| Approach | Bitrate/codec | Trim/silence pad | Volume normalization | Partial overlap |
|---|---|---|---|---|
| Exact file hash | ✗ | ✗ | ✗ | ✗ |
| Decoded PCM hash | ✗ (lossy) | ✗ | ✗ | ✗ |
| Chromaprint | ✓ | partial (alignment-sensitive) | ✓ (amplitude-robust) | partial |
| audfprint | ✓ | ✓ | ✓ | ✓ (excerpts) |
| Panako | ✓ (+ speed/pitch) | ✓ | ✓ | ✓ |
| Commercial | ✓ | ✓ | ✓ | ✓ |

## A note on accuracy numbers

Published "accuracy" figures for fingerprinters are highly
dataset-dependent (clean full files vs phone-recorded clips vs DJ mixes) and not
comparable across systems without identical test conditions. Treat any single
percentage with suspicion. The reliable, repeatedly-stated facts are
*qualitative*:

- Chromaprint is explicitly **scoped to near-identical audio** and is the
  community default for library dedup (per its own README and upstream issues).
- Landmark systems (audfprint, Dejavu, Shazam-style) excel at **short noisy
  excerpts**; Panako additionally handles **speed/pitch** changes.

For a confident number specific to *this* library, measure it: take a set of
known cross-source duplicates, run candidate fingerprinters, and record recall /
false-hit rates on your own data.

## Recommendation for track-manager

This is a personal/small library that transcodes and re-tags on download, and
wants to catch the *same recording* arriving from different sources. The pragmatic
answer is a **layered cascade**, cheapest and most certain first, with metadata
corroboration before any destructive action.

**Layer 0 — Metadata (keep what we have).**
Continue using ISRC and `TRACK_URL` as *high-confidence positive* signals, and
normalized artist+title as a *candidate generator*. ISRC match → treat as a
strong duplicate signal. These stay the first and cheapest filter.

**Layer 1 — Exact file hash (SHA-256).**
Cheap pre-pass to collapse literally identical files (cache by path+mtime). Low
yield here because we re-tag, but free and certain.

**Layer 2 — Decoded-audio (PCM) hash.**
Decode→normalize sample rate/channels→hash. Catches re-containerized /
re-tagged-but-identical audio (relevant given our FLAC→M4A + tagging pipeline).
Bit-exact match ⇒ confident duplicate.

**Layer 3 — Chromaprint (local) for cross-source matching.**
This is the layer that actually solves the stated problem. Fingerprint each file
with `fpcalc`/`pyacoustid`, cache the fingerprint in a sidecar/DB keyed by
path+mtime, and compare fingerprints *locally* (no AcoustID network call needed
for intra-library dedup). Use the well-known fingerprint-similarity comparison
(bit-error / correlation over aligned fingerprints) with a **conservative
threshold**.

**Layer 4 (optional) — AcoustID web lookup.**
Only when you also want to *identify/repair* metadata or resolve to MBIDs. Needs
an API key + network; not required for dedup itself. Commercial APIs
(ACRCloud/AudD) are out of scope unless you specifically want large-catalogue
identification and accept the cost/privacy tradeoffs.

### Handling false hits

Because Chromaprint *can* false-match short/silent/sample-sharing tracks, never
auto-delete on a fingerprint match alone:

- **Confidence threshold.** Require a strong fingerprint similarity score; tune
  on your own labelled duplicates rather than trusting a default.
- **Metadata corroboration.** Treat fingerprint matches as *confirmed* mainly
  when corroborated by metadata (matching ISRC, or close normalized artist+title
  via the existing `normalize_text`). Fingerprint *plus* metadata agreement is a
  strong signal; fingerprint alone on a 5-second track is not.
- **Guard the degenerate cases.** Skip or down-weight **very short** tracks and
  **near-silent** audio, where fingerprints are unreliable.
- **Prefer the safe error.** Chromaprint's tendency to *miss* remix/live/acoustic
  variants (rather than wrongly merge them) aligns with this project, which
  intentionally preserves those distinctions in titles. Lean conservative.
- **Keep a human in the loop.** Route fingerprint-based matches through the
  existing **`interactive`** handling (show both files, scores, and which is
  higher quality) before replacing/deleting. Reserve automatic `skip` for
  high-confidence layers (ISRC / exact / PCM hash).

### Licensing considerations

- **Chromaprint** is **LGPL-2.1** — fine to use via the `fpcalc` binary or
  dynamic linking; just don't statically embed it into a differently-licensed
  binary without honoring LGPL terms. track-manager is MIT, and shelling out to
  `fpcalc` (as beets/Picard do) keeps this clean.
- **AcoustID web service** requires a **free API key** and is rate-limited;
  submitting fingerprints back is encouraged but optional.
- **Panako** is **AGPL-3.0** (copyleft, network-use clause) — consider carefully
  before integrating.
- **Commercial APIs** (ACRCloud, Audible Magic, Gracenote, AudD) are paid and
  upload your audio to a third party — weigh cost and privacy.

## Python libraries & runtime requirements (quick reference)

- **`hashlib`** — stdlib. Exact file hashing. No setup.
- **`soundfile` / `librosa` / FFmpeg** — decode to PCM for Layer 2 (and as a
  decoder backing fingerprinting). FFmpeg is the most robust decoder.
- **`pyacoustid`** (`pip install pyacoustid`) — Python bindings. **Requires the
  `fpcalc` binary or `libchromaprint`** at runtime:
  - macOS: `brew install chromaprint` (provides `fpcalc`; pulls FFmpeg).
  - Debian/Ubuntu: `apt install libchromaprint-tools` (provides `fpcalc`) or
    `libchromaprint1`.
  - Point `pyacoustid` at it via `$PATH` or the `FPCALC` env var; use
    `fingerprint_file(path)` for local fingerprints, and `force_fpcalc=True` if
    the dynamic library and CLI disagree on decoding.
- **`dejavu` / `PyDejavu`** — requires a **MySQL or PostgreSQL** database to
  store fingerprints, plus (often) **pinned older NumPy**. Currently
  poorly maintained — not recommended.
- **audfprint** — Python script + NumPy/scipy; you manage its hash database.
- **Panako** — needs a **JVM**; invoked as an external Java tool, not a Python
  package.

## Suggested first step

Prototype **Layer 3** in isolation: add an optional `pyacoustid` dependency,
fingerprint the existing library, cache fingerprints, and measure recall /
false-hit rate against a hand-labelled set of known cross-source duplicates from
your own collection. That data — not vendor accuracy claims — should drive the
threshold and whether fingerprinting graduates from `interactive`-only to an
automatic layer.
