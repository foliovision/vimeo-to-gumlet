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

```bash
pip install requests

export GUMLET_API_KEY=...          # Bearer token
export GUMLET_COLLECTION_ID=...    # workspace / source / collection id

# Dry run — shows what would be uploaded, makes no HTTP calls:
python3 upload_to_gumlet.py --root /home/ubuntu/fv-videos --dry-run -v

# Real run:
python3 upload_to_gumlet.py --root /home/ubuntu/fv-videos -v
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

The folder's `video-details.json` is attached as `metadata` / `description`
on the asset, and `fv-video-id:{id}` is added as a tag.
