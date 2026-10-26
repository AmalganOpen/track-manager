# Track Manager

Universal downloader. Paste a link, get a file in your library.

```
tm "https://open.spotify.com/track/..."
```

`tm` is short for `track-manager`. A bare URL runs download. Default format is **AIFF** (PCM 16-bit / 44.1 kHz) so it plays on DJ gear. Override with `-f m4a` or `-f mp3`.

Needs `ffmpeg` and `ffprobe` on your PATH. Check with `tm check-setup`.

## What can I download?

**Single tracks from almost anywhere.** Spotify, Apple Music, YouTube, SoundCloud, Deezer, Amazon Music, TIDAL, and similar links go through song.link, then a public lossless proxy when one is up. No account on those sites.

**YouTube, SoundCloud, and Instagram are also direct sources.** They are the fallback when the lossless path misses, and they work as the original link too.

**Playlists:** YouTube and SoundCloud playlists work with no extra setup. Spotify playlists and albums need a Spotify developer app (next section). Instagram carousels work. Profile URLs do not.

**Not a playlist of your whole library.** One URL per command. A playlist URL expands to its tracks.

## Where do files go?

`config.yaml` in the repo root. Copy `config.example.yaml` if you do not have one.

```yaml
output_dir: "~/Music/tm/tracks"
failed_log: "~/Music/tm/failed-downloads.txt"
```

`-o` / `--output` overrides the folder for one run. Duplicate handling is `duplicates.handling`: `interactive`, `skip`, or `keep`.

-# Do not commit config.yaml. It is gitignored because it holds your keys.

## Why is this track not lossless?

Order of attempts:

1. song.link resolves the URL to catalogue IDs
2. Public Qobuz / TIDAL proxies try for lossless FLAC
3. Optional Soulseek, if you configured it
4. YouTube, SoundCloud, Instagram, or spotdl

A YouTube rip is about **128 kbps**. SoundCloud public streams are similar. Lossless is 16-bit / 44.1 kHz, then written as AIFF by default.

`tm check-quality` groups the library into high, medium, and low. The tag stores the **source** bitrate, not the re-encode bitrate.

`--dumb` skips the smart path and downloads the URL as-is. `--no-cache` ignores the saved song.link → TIDAL id map when a cached id is stale.

If song.link returns **401**, lookups stop for that run and everything falls back. A key is optional: `songlink.api_key` or `SONGLINK_API_KEY`. Email developers@song.link.

## Spotify says I need credentials

**A single track does not.** `tm "https://open.spotify.com/track/..."` is enough.

**A playlist or album does.** Spotify will not list the tracks without an app.

1. https://developer.spotify.com/dashboard → create an app → copy client id and secret
2. Put them in `config.yaml`:

```yaml
spotdl:
  client_id: "..."
  client_secret: "..."
```

The secret is a password. It stays in your local config, not in git or in this channel.

## YouTube 403 / unable to download video data

Almost always an old yt-dlp, stale cookies, or a missing JS runtime. Not a private video.

1. `pip install -U 'yt-dlp[default]'`
2. Install Deno (`brew install deno` on macOS) so YouTube's JS challenges can run
3. If `youtube.cookies_from_browser` is set, log into YouTube again in that browser, or clear it unless the video is age-restricted. **Stale cookies cause 403s on public videos.**
4. Leave `youtube.player_clients` empty
5. `tm check-setup`

Still failing: open the URL in a browser. Private, removed, age-restricted, or geo-blocked videos will not download. Age-restricted needs fresh cookies. Wait and retry if you are being rate limited. Details land in `failed-downloads.txt`.

## SoundCloud is quiet or low quality

The track has to be public. Private tracks and private sets fail. If the artist disabled download, you only get the stream.

Public streams are ~128 kbps MP3. Original WAV / AIFF / FLAC is only available when the uploader turned on "Download file" **and** you set a SoundCloud oauth token:

1. Log in at https://soundcloud.com
2. DevTools → Application → Cookies → copy `oauth_token`
3. `soundcloud.oauth_token` in `config.yaml`

That cookie is a year-long login. Do not paste it here. Logging out or changing your password kills it.

## Instagram wants a login

Most reels and posts do. Set a browser you are logged into:

```yaml
instagram:
  cookies_from_browser: "firefox"
```

`chrome`, `safari`, `brave`, and `edge` work too. Or a Netscape `cookies_file`. If Instagram's browser field is empty, YouTube's `cookies_from_browser` is reused.

**Use a post link**, not a profile: `/reel/`, `/p/`, `/tv/`, or `/share/`. `instagram.com/username` is rejected.

Instagram has no artist, title, or ISRC. Captions get flagged into `tracks-metadata-review.csv`. Fix them and run `tm apply-metadata`.

## What is Soulseek doing here?

Optional lossless fallback **after** the public proxies miss, **before** YouTube. It stays off until both `soulseek.username` and `soulseek.password` are set.

There is no signup page. First successful login creates the account. If the name is taken, pick another.

Install [sockseek](https://github.com/fiso64/sockseek/releases) and put it on your PATH (`sldl` still works). Search is artist + title + duration.

-# sockseek does not share files. If you already run Nicotine+ or slskd, use a separate Soulseek account here so the two clients do not fight.

## It says I already have this track

Duplicates match **artist + title tags**, not the filename. Case does not matter. `feat.` and junk like `[Official Video]` are folded together. An M4A and an AIFF of the same song count as one track.

You are asked to skip, keep both, or replace. `duplicates.handling: skip` answers that for you.

No tags means no match. Fix metadata first, then `tm check-duplicates`.

## Titles are wrong or missing

Flagged downloads go to `tracks-metadata-review.csv` in the project directory.

1. Fill in artist and title
2. `tm apply-metadata`
3. `tm apply-metadata --show` previews without writing

Failed URLs are separate: `~/Music/tm/failed-downloads.txt`.

```
tm retry-failed --list
tm retry-failed --dry-run
tm retry-failed
tm retry-failed --clear
```

## Tune, pad, Rekordbox

**`tm tune` changes pitch in place.** BPM tag stays. The file is the same path.

```
tm tune "midnight" 2.5
tm tune "midnight" -3
tm tune "midnight" 50 -c
```

`2.5` is percent. `-c` is cents. `-a` treats the argument as a filesystem path.

**`tm check-tuning`** estimates tuning against A440. It does not write the file.

**`tm pad`** snaps the file to the Rekordbox bar grid (pad start and end onto the 1 when you are past the 3).

```
tm pad "midnight" -n
tm pad "midnight"
tm pad "midnight" --undo
```

Quit Rekordbox first, including menu-bar leftovers. Pad writes `~/Library/Pioneer/rekordbox/master.db` and shifts cue times for a start pad. **Do not re-analyse the track after** or cues and loops drift.

`-n` is a dry run. `--end-tail silence` is dry silence. The default tail is a quiet reverb wash. The body of the track is not faded.

Prefer AIFF. Padding M4A or MP3 re-encodes the whole file. `tm migrate-to-aiff` converts the library. M4A/MP3 padding is lossy on purpose.

## `tm` is not a command

The install is an editable checkout:

```
cd track-manager
pip install -e .
pip install -e ".[tune]"
```

`python -m track_manager` works even when the script is off your PATH. `tm check-setup` prints what is missing.

`tm update` only works on a git checkout (`git pull`, then `pip install -e .` if dependencies changed). Installed from pip instead: `pip install --upgrade track-manager`.

Shell completion: `tm completions zsh` (also `bash` and `fish`). Re-run it after CLI changes.

## I want to change the code

Repo: https://github.com/AmalganOpen/track-manager

`main` does not take a direct push. Open a pull request. It needs **one approval from someone else**. Pushing more commits clears that approval.

```
pip install -e ".[dev]"
```

Tests and style are in `docs/development.md`. Keep secrets in your local `config.yaml`. PRs that add keys, webhook URLs, or your `config.yaml` will be rejected.

Bug reports need the command, the full error, and the output of `tm check-setup`.
