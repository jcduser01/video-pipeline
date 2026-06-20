# config/safezone/

Safe-zone specs, one per output profile, derived from reference template PNGs.

## What lives here

Reference template PNGs, one per output profile, all sourced from
[adkit.so](https://adkit.so/tools/safe-zones/instagram). In each, the **danger**
region is an opaque overlay and the **safe** region is transparent.

- `instagram-safe-zone-reels-9x16.png` (1080×1920)
- `instagram-safe-zone-story-9x16.png` (1080×1920)
- `instagram-safe-zone-feed-portrait-4x5.png` (1080×1350)
- `instagram-safe-zone-feed-square-1x1.png` (1080×1080)
- `instagram-safe-zone-feed-landscape-16x9.png` (1080×608)

Generated machine-readable specs sit alongside as `<profile>.safezone.json`:

- `reels-9x16.safezone.json` — generated from the reels template.

Generate the spec for any other profile with the command below as it's needed.

## Why a polygon, not a box

An Instagram Reels safe zone is **not a rectangle**. It is a safe-area rectangle
with a smaller rectangle **notched out of the lower-right corner** for the
action-button cluster. The spec models this exactly (a row-convex run-length
encoding plus an orthogonal polygon), so the QC validator tests against the real
shape — including the notch — not four margin numbers.

## Regenerating (update-resilient)

When Instagram changes the safe zone, drop in an updated template PNG and
regenerate — no code change:

```
python -m video_pipeline.cli safezone-gen \
    config/safezone/instagram-safe-zone-reels-9x16.png \
    --profile reels-9x16 \
    -o config/safezone/reels-9x16.safezone.json
```

## Coordinate convention

Integer **pixel-edge** coordinates; origin top-left; x right, y down. Rectangles
are half-open: `x0 <= x < x1`, `y0 <= y < y1`.
