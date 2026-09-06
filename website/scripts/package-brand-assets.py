"""Package reviewed public brand assets without local build artifacts."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

PUBLIC = Path(__file__).resolve().parents[1] / 'public'
BRAND = PUBLIC / 'brand'
icons = sorted(path for path in (BRAND / 'icons').rglob('*') if path.is_file())
with ZipFile(BRAND / 'hormuz-icons.zip', 'w', ZIP_DEFLATED) as archive:
    for source in icons:
        archive.write(source, source.relative_to(BRAND))
    archive.write(BRAND / 'README.md', 'README.md')
with ZipFile(BRAND / 'hormuz-brand-kit.zip', 'w', ZIP_DEFLATED) as archive:
    for name in ['hormuz-lockup.svg', 'hormuz-lockup-reverse.svg', 'hormuz-mark.svg', 'hormuz-mark-reverse.svg', 'controlled-passage.webp', 'README.md', 'tokens.css', 'hormuz-icons.zip']:
        archive.write(BRAND / name, name)
    for name in ['icon.svg', 'favicon.ico', 'apple-touch-icon.png']:
        archive.write(PUBLIC / name, name)
    archive.write(PUBLIC / 'og.png', 'social-preview.png')
    for source in icons:
        archive.write(source, source.relative_to(BRAND))
print(f'Packaged {len(icons)} icon assets and the complete brand kit.')
