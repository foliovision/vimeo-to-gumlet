# FV video folders → Gumlet.tv uploader

## Layout
Each video lives in its own directory named `{video_id} {video name}`:

```
196881410 Foliovision Promo Video/
    Foliovision Promo Video-source.mp4            # preferred source
    Foliovision Promo Video-{360,540,720,1080}p.mp4
    subtitles-en-English Foliovision Promo Video.vtt.vtt
    subtitles-en-Updated English Foliovision Promo Video.vtt.vtt
    subtitles-sk-Slovak Foliovision Promo Video.vtt.vtt
    subtitles-sk-Updated Slovak Foliovision Promo Video.vtt.vtt
    thumbnail-{100x75,200x150,295x166,640x360,960x540,1280x720}.jpg
    video-details.json
```

## Files here

* `generate_samples.sh` – creates two synthetic folders (above) using ffmpeg.
* `upload_to_gumlet.py` – uploads each folder to Gumlet.tv (source video +
  largest thumbnail + every VTT subtitle).
* `mock_gumlet.py` – tiny local stand-in for `api.gumlet.com`, used for
  sanity-checking the uploader without hitting the real API.

## Source-selection rule
The uploader picks the source file like this:

1. Use `*-source.mp4` if present **and** non-empty.
2. Otherwise, use the highest-resolution `*-{N}p.mp4` available
   (`1080p` > `720p` > `540p` > `360p`).

## Uploader usage

Requires Python 3.9+. On macOS, `pip` is not on `PATH` by default —
use `python3 -m pip` (or `pip3`).

```bash
python3 -m pip install -r requirements.txt

export GUMLET_API_KEY=...          # Bearer token
export GUMLET_COLLECTION_ID=...    # workspace id
export GUMLET_PARENT_ID=...        # (optional) destination folder id

# Dry run — shows what would be uploaded, makes no HTTP calls:
python3 upload_to_gumlet.py --root ./videos --dry-run -v

# Real run (writes ./manifest.json by default):
python3 upload_to_gumlet.py --root ./videos -v

# Same, with a custom manifest path:
python3 upload_to_gumlet.py --root ./videos -v --output-json /path/to/manifest.json
```

### JSON manifest (`--output-json`)

The script **always** maintains a JSON manifest file (default
`./manifest.json`, overridable with `--output-json`). On startup it is
read; any folder whose `fv_video_id` is already in the file is skipped
(no duplicate upload). After the uploads (and optional folder move)
finish, each newly-created asset is fetched via
`GET /v1/video/assets/{id}` and merged back into the file by
`fv_video_id` (new ids are appended, existing ids are replaced with the
latest URLs). Unrelated entries are preserved untouched.

Each entry has the shape:

```json
[
  {
    "fv_video_id": "196881410",
    "title": "Foliovision Promo Video",
    "asset_id": "69e8d9c08dd5a216e299e96c",
    "status": "ready",
    "playback_url": "https://video.gumlet.io/<ws>/<asset>/main.m3u8",
    "dash_playback_url": null,
    "thumbnail_urls": ["https://video.gumlet.io/<ws>/<asset>/thumbnail-1-0.png?v=..."],
    "preview_thumbnails_url": "https://video.gumlet.io/<ws>/<asset>/preview_thumbnails.vtt",
    "transcription_url": "https://video.gumlet.io/<ws>/<asset>/<asset>-transcription-word-level-timestamp.json?token=...",
    "subtitles": [
      {
        "language_code": "en",
        "name": "English",
        "vtt_url": "https://video.gumlet.io/<ws>/<asset>/subtitles-en.vtt",
        "hls_playlist_url": "https://video.gumlet.io/<ws>/<asset>/subtitles-en.m3u8"
      }
    ]
  }
]
```

Reachability of the URLs depends on the workspace's security settings in
the Gumlet dashboard. In a freshly-provisioned workspace you will typically
see:

| URL | Default public | Notes |
|---|---|---|
| `thumbnail-*.png` | ✓ | always public |
| `preview_thumbnails.vtt` | ✓ | timeline-preview sprite map |
| `main.m3u8` | ✗ (401) | HLS master is token-protected by default |
| `<asset_id>_<idx>_<lang>_v<n>.vtt` | ✓ | the actual VTT Gumlet serves for the language |
| `...-transcription-word-level-timestamp.json` | ✓ (signed) | pre-signed URL with `?token=&expires=` |

To make `main.m3u8` directly fetchable without a token, turn off
**Security → Secure Token** for the workspace, or use Gumlet's signed-URL
helper in your player.

### Using a virtualenv (recommended on macOS)

The system Python on macOS is managed by Apple and `pip install` into it
may fail with "externally-managed-environment". Use a venv:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 upload_to_gumlet.py --root ./videos --dry-run -v
```

For each folder the script:

1. `POST /v1/video/assets/upload` → `{asset_id, upload_url}`, then `PUT` the
   source MP4 to `upload_url`.
2. `POST /v1/video/assets/{asset_id}/thumbnail` → `{upload_url}`, then `PUT`
   the largest JPEG thumbnail.
3. For every `subtitles-{lang}-{label}.vtt.vtt`:
   `POST /v1/video/assets/{asset_id}/subtitle/upload`
     body `{"language_codes": ["<lang>"]}`
   then `PUT` the VTT to the returned URL. Files are processed in
   lexicographic order, so when multiple VTTs exist for the same
   language the one starting with `Updated …` wins.
4. Once all VTTs are uploaded:
   `POST /v1/video/assets/{asset_id}/subtitle/upload/event`
     body `{"upload_responses": [{"language_code": "<lang>", "uploaded": true}, …]}`
   This is the handshake that actually triggers Gumlet's subtitle
   transcoding — without it the subtitles sit in `input.additional_tracks`
   forever but never appear in the dashboard or the HLS manifest.
5. If `--parent-id` / `GUMLET_PARENT_ID` is set, all newly-created assets
   are moved into that folder at the end via
   `POST /v1/video/workspaces/{workspace_id}/folders/{folder_id}`
     body `{"asset_ids": [...]}` — a single call for the whole batch.

The folder's `video-details.json` is attached as `metadata` / `description`
on the asset, and `fv-video-id:{id}` is added as a tag.
