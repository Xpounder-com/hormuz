# Koto identity

Koto is the product name. Use the supplied wooden K logo as the product mark.

`koto-logo.png` is the original owner-supplied 1254 × 1254 PNG, copied without
alteration. Its SHA-256 is
`39de17d7e3a4e358385a142d41278830334c9bda7d89d53e445fd425bc6b3dbc`.

`koto-icon-source.png` is the separate plugin icon master, extracted with
Image Gen from the owner-supplied `Koto-Inspired Wooden K Icon.png`. It uses a
tight dark tile and enlarged wooden K so the mark remains visible in small
plugin slots. Transparent corners retain their alpha channel. See the
[image edit provenance](koto-icon-edit.md) for the source hash and edit prompt.
The icon master's SHA-256 is
`ee71d741636fa8297339cf34602362f72a638c1a8edbfcd6378c219d3d585505`.

Use the 512 px icon for plugin listings and the 64 px icon for the composer.
Regenerate the derived sizes with `python tools/build_brand_assets.py` from
an authoring environment with Pillow installed. The builder reads the icon
master, preserves alpha, and leaves both source images unchanged.

Keep the mark square. Preserve the wood, strings, lighting and dark background.
Place adjacent text outside the image and use the spelling **Koto**. Include
the alternative text “Koto wooden K logo” when the image provides the name.

There is no configured public product domain. Future hosting under Neuralint
will be configured separately. Repository URLs remain the existing working
addresses until a repository rename is completed.
