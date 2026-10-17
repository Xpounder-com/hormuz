"""Resize the supplied Koto logo for plugin use. Requires Pillow for authoring."""

from pathlib import Path

from PIL import Image


def main():
    assets = Path(__file__).resolve().parents[1] / "assets"
    with Image.open(assets / "koto-logo.png") as source:
        for size in (32, 64, 128, 256, 512, 1024):
            with source.resize((size, size), Image.Resampling.LANCZOS) as icon:
                icon.save(assets / f"koto-icon-{size}.png", optimize=True)
        source.save(assets / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    print("Exported Koto icons and favicon; the original logo is unchanged.")


if __name__ == "__main__":
    main()
