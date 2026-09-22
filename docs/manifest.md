# Review bundle format

A bundle is a dedicated folder containing `data.json` and its referenced media. All time values are seconds. `start` and `end` refer to the rendered movie; `sourceStart` and `sourceEnd` describe the original source interval for feedback. The viewer does not reconstruct a movie from these records.

```json
{
  "schemaVersion": 1,
  "title": "My review",
  "duration": 30,
  "fps": 24,
  "revision": "first-review",
  "videoUrl": "media/preview.mp4",
  "posterUrl": "media/poster.jpg",
  "tracks": [
    {
      "id": "picture",
      "name": "Picture",
      "kind": "video",
      "parked": false,
      "clips": [
        {
          "id": "shot-one",
          "label": "Opening shot",
          "sourceFilename": "opening.mp4",
          "start": 0,
          "end": 10,
          "duration": 10,
          "sourceStart": 2,
          "sourceEnd": 12,
          "speed": 1,
          "hidden": false,
          "volume": 1,
          "thumbnail": "thumbs/opening.jpg",
          "colorPending": false
        }
      ]
    }
  ],
  "notes": ["Review the opening pace."],
  "waveform": null
}
```

The example omits the rest of the movie's clip annotations deliberately; annotations need not cover every frame. Movie duration and frame rate must describe the actual rendered preview. `prepare` reads the movie duration itself.

Required project fields: `schemaVersion`, `title`, `duration`, `fps`, `videoUrl`, `tracks`. `revision` defaults to `local`. Poster, notes and waveform are optional.

Each lane needs a unique `id`, `name`, `kind` (`video` or `audio`) and `clips` array. A `parked` lane is concealed until the user chooses to show parked takes. This does not alter the rendered movie.

Each clip needs a globally unique `id`, `label`, `start`, `end`, `duration`, `sourceStart`, and `sourceEnd`. Optional fields are `layerId`, `sourceFilename`, `speed` (default 1), `hidden`, `volume`, `thumbnail`, and `colorPending`. Label text is treated as text, never as HTML. Use basenames rather than private absolute paths in `sourceFilename`.

Waveform, when supplied, is `{"step": 0.1, "peaks": [0.1, 0.2, 0.15]}`. Samples are peak amplitudes in the range 0 to 1, spaced by `step` seconds from movie zero. Supply the mixed movie waveform, not an unrelated source stem. It is drawn in one separate mix lane and never presented as the waveform of every audio clip.

## Validation and resource limits

- Manifest version 1; maximum JSON size 10 MB.
- Positive finite duration up to 86,400 seconds; positive frame rate up to 240.
- At most 200 lanes, 10,000 clips and 50,000 waveform samples.
- Unique identifiers; no negative, inverted, nonfinite or inconsistent clip ranges.
- Local relative media paths only. No absolute paths, remote URLs, traversal, query strings or fragments.
- Movie extensions: MP4, WebM, MOV and M4V. Image extensions: PNG, JPEG, WebP and GIF. Codec support depends on the browser; H.264/AAC MP4 is the recommended review format.
- Every referenced file must exist inside the bundle. Symlink escapes are rejected.

The server only serves validated manifest fields and explicitly listed assets. Extra editor metadata is not exposed through `data.json`. Rebuild the bundle after changing the movie or timeline; stale annotations can otherwise refer to the wrong revision.
