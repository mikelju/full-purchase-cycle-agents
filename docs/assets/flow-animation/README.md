# Purchase cycle flow animation - source

Source of `docs/assets/purchase-cycle-flow.gif` and `docs/assets/purchase-cycle-flow.mp4` (change 005), kept so the animation can be regenerated.

## Files
- `purchase-cycle-flow.html`: the animated SVG page; it loads the Inter and JetBrains Mono fonts from Google Fonts, so capturing needs network access.
- `record.py`: captures the page frame by frame into `frames/` (git-ignored) with a virtual clock, so the output is deterministic: 15 fps, 1280x720 PNG.

The flow is conceptual, as stated in `docs/changes/005-flow-animation.md`: it does not claim to show the exact current behavior of the code.

## Capture frames
Requires Google Chrome installed and Playwright, which is not a project dependency, so it is pulled only for this run:

```
uv run --with playwright python docs/assets/flow-animation/record.py
```

Playwright drives the installed Chrome (`channel="chrome"`), so no browser download is needed.

## Encode
The frames are encoded with ffmpeg into a GIF of 960x540 (looping) and an MP4 of 1280x720 (H.264).
The exact ffmpeg commands were not recorded.
ffmpeg is not a project dependency.
