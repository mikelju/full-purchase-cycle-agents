# Purchase cycle flow animation - source

Source of the flow animations, kept so they can be regenerated:
- `docs/assets/purchase-cycle-flow.gif` and `.mp4` (change 005, orders).
- `docs/assets/quotes-flow.gif` and `.mp4` (change 008, quotes).
- `docs/assets/reconcile-flow.gif` and `.mp4` (change 011, invoice reconciliation; redesigned in change 017).
- Spanish copies of the three, with an `-es` suffix (`docs/assets/purchase-cycle-flow-es.gif` and `.mp4`, and the same for `quotes-flow-es` and `reconcile-flow-es`; change 016).

## Files
- `purchase-cycle-flow.html`: the orders animated SVG page; it loads the Inter and JetBrains Mono fonts from Google Fonts, so capturing needs network access.
- `quotes-flow.html`: the quotes animated SVG page (customer, three suppliers, quotes and decision graphs, database); same fonts, about 37 s.
- `reconcile-flow.html`: the reconciliation animated SVG page (one connected graph in two rounds: the quantity, price and total of the order, delivery note and invoice are extracted and compared; round 1 matches and goes to register, round 2 charges 40 units against 35 delivered and goes through the claim subgraph, compute difference and ask for credit; no captions); same fonts, about 30 s.
- `purchase-cycle-flow-es.html`, `quotes-flow-es.html` and `reconcile-flow-es.html`: the Spanish copies of the three pages, with the same layout and timeline (the reconciliation one rebuilt with its English page in change 017); they declare `<meta charset="utf-8">` so accents and the letter ñ are not misread from the file.
- `record.py`: captures a page (optional name argument, default the orders page) frame by frame into `frames/<page>/` (git-ignored; the folder is cleaned before each capture) with a virtual clock, so the output is deterministic: 15 fps, 1280x720 PNG.
  Before the first frame it loads every font face the page declares (each family, weight and character subset), so no frame falls back to another font while a weight or the accented glyphs load.

The flows are conceptual, as stated in `docs/changes/005-flow-animation.md`, `docs/changes/008-quotes-flow-animation.md`, `docs/changes/011-reconcile-flow-animation.md` and `docs/changes/017-simple-reconcile-animation.md`: it does not claim to show the exact current behavior of the code.

## Capture frames
Requires Google Chrome installed and Playwright, which is not a project dependency, so it is pulled only for this run:

```
uv run --with playwright python docs/assets/flow-animation/record.py
uv run --with playwright python docs/assets/flow-animation/record.py quotes-flow.html
uv run --with playwright python docs/assets/flow-animation/record.py reconcile-flow.html
uv run --with playwright python docs/assets/flow-animation/record.py purchase-cycle-flow-es.html
uv run --with playwright python docs/assets/flow-animation/record.py quotes-flow-es.html
uv run --with playwright python docs/assets/flow-animation/record.py reconcile-flow-es.html
```

Playwright drives the installed Chrome (`channel="chrome"`), so no browser download is needed.

## Encode
The frames are encoded with ffmpeg into a GIF of 960x540 (looping) and an MP4 of 1280x720 (H.264).
The commands below are the ones used for the quotes animation (change 008) and, with `quotes-flow` replaced by `reconcile-flow`, for the reconciliation one (change 011); the three Spanish copies (change 016) use the same commands with `quotes-flow` replaced by `purchase-cycle-flow-es`, `quotes-flow-es` or `reconcile-flow-es`; the orders animation (change 005) was encoded earlier with unrecorded commands.
They need an ffmpeg build with `palettegen` and `paletteuse` (a full build; a minimal one fails with "No such filter").
Run them from the repository root, with `F=docs/assets/flow-animation/frames/quotes-flow`:

```
ffmpeg -y -framerate 15 -i $F/f%04d.png -vf "scale=960:540:flags=lanczos,palettegen=stats_mode=diff" $F/pal.png
ffmpeg -y -framerate 15 -i $F/f%04d.png -i $F/pal.png -lavfi "scale=960:540:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" -loop 0 docs/assets/quotes-flow.gif
ffmpeg -y -framerate 15 -i $F/f%04d.png -vf "scale=1280:720,format=yuv420p" -c:v libx264 -crf 20 -preset slow -movflags +faststart docs/assets/quotes-flow.mp4
```

ffmpeg is not a project dependency.
