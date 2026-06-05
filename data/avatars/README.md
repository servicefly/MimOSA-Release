# MimOSA Avatar Assets

This directory holds avatar artwork for the GTK4 desktop presence (Phase 3,
M3.1).

## Files

| File | Purpose |
|------|---------|
| `default.svg` | Resolution-independent resting glyph / app icon. |

## How rendering works

The **live** desktop avatar is drawn procedurally with **Cairo** in
[`mimosa/ui/avatar_renderer.py`](../../mimosa/ui/avatar_renderer.py). Each voice
state (idle, listening, processing, speaking) has its own animation, so the
avatar is fully geometric and **needs no raster assets at runtime**.

`default.svg` is therefore a *fallback / packaging* asset: it is used as the
window/app icon and as the static design reference. If the SVG is missing,
`AvatarAssets.default_svg_path()` returns `None` and the UI simply relies on the
procedural renderer — there is no hard dependency on any file here.

## Theming

Color themes (`aurora`, `ember`, `mono`) live in
[`mimosa/ui/ui_config.py`](../../mimosa/ui/ui_config.py) as RGB tuples consumed
directly by the renderer; you do not need a separate image per theme.

## Adding your own

Drop additional SVGs here and reference them through
`mimosa.ui.avatar_assets.AvatarAssets`. Keep them square and centered on a
200×200 viewBox to match the default.
