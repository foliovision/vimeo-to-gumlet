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

# Real run:
python3 upload_to_gumlet.py --root ./videos -v
```

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
   then `PUT` the VTT to the returned URL.
4. If `--parent-id` / `GUMLET_PARENT_ID` is set, all newly-created assets
   are moved into that folder at the end via
   `POST /v1/video/workspaces/{workspace_id}/folders/{folder_id}`
     body `{"asset_ids": [...]}` — a single call for the whole batch.

The folder's `video-details.json` is attached as `metadata` / `description`
on the asset, and `fv-video-id:{id}` is added as a tag.
