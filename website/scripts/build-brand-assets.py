"""Export existing Hormuz identity with outlined Geist lettering (fonttools + brotli).

Run after a Next build has populated .next/static/media. Generated artwork is
supplied separately; this script does not generate or alter that artwork.
"""
from pathlib import Path
import json
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'public/brand'
OUT.mkdir(parents=True, exist_ok=True)

def font(family, weight):
    for path in (ROOT / '.next/static/media').glob('*.woff2'):
        candidate = TTFont(path)
        if candidate['name'].getDebugName(1) == family and ord('H') in candidate.getBestCmap():
            return instantiateVariableFont(candidate, {'wght': weight}) if 'fvar' in candidate else candidate
    raise RuntimeError(f'Build the website first to obtain the {family} font.')

MONO, SANS = font('Geist Mono', 650), font('Geist', 550)

def outlined(text, x, y, size, face=SANS, tracking=0):
    glyphs, cmap = face.getGlyphSet(), face.getBestCmap()
    scale, paths = size / face['head'].unitsPerEm, []
    for character in text:
        name = cmap[ord(character)]
        pen = SVGPathPen(glyphs)
        glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, x, y)))
        paths.append(f'<path d="{pen.getCommands()}"/>')
        x += glyphs[name].width * scale + tracking
    return ''.join(paths)

identity = json.loads((ROOT / 'lib/brand-mark.json').read_text())
MARK = '<g fill="currentColor">' + ''.join(f'<path d="{d}"/>' for d in identity['paths']) + '</g>'
def svg(content, width, height, title):
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img"><title>{title}</title>{content}</svg>\n'

for name, color in [('hormuz-lockup', '#24392D'), ('hormuz-lockup-reverse', '#F4F3EE')]:
    content = f'<g style="color:{color}" fill="{color}"><g transform="translate(8 8)">{MARK}</g>{outlined("HORMUZ", 92, 54, 38, MONO, 5.3)}</g>'
    (OUT / f'{name}.svg').write_text(svg(content, 360, 80, 'Hormuz'))
(OUT / 'hormuz-mark.svg').write_text(svg(f'<g style="color:#24392D">{MARK}</g>', 64, 64, 'Hormuz mark'))
(OUT / 'hormuz-mark-reverse.svg').write_text(svg(f'<g style="color:#DCEDA6">{MARK}</g>', 64, 64, 'Hormuz mark'))

# A native vector social layout. The abstract artwork is embedded by the renderer.
content = '<rect width="1200" height="630" fill="#F4F3EE"/>'
content += f'<g style="color:#24392D" fill="#24392D"><g transform="translate(56 44) scale(.75)">{MARK}</g>{outlined("HORMUZ", 119, 78, 25, MONO, 3.5)}</g>'
content += '<clipPath id="art"><rect x="740" y="28" width="432" height="574" rx="24"/></clipPath><image href="ARTWORK_DATA_URI" x="740" y="28" width="432" height="574" preserveAspectRatio="xMidYMid slice" clip-path="url(#art)"/>'
content += f'<g fill="#526647">{outlined("OPEN-SOURCE AI POLICY GATEWAY", 58, 169, 13, MONO, .9)}</g>'
content += f'<g fill="#202723">{outlined("Give your", 54, 266, 74)}{outlined("team AI.", 54, 348, 74)}</g>'
content += f'<g fill="#31684B">{outlined("Keep control.", 54, 430, 74)}</g>'
content += f'<g fill="#5C6758">{outlined("Policy. Budgets. Content-free evidence.", 58, 494, 21)}{outlined("usehormuz.github.io", 58, 571, 15, MONO, .3)}</g>'
(ROOT / '.artifacts/brand').mkdir(parents=True, exist_ok=True)
(ROOT / '.artifacts/brand/social-layout.svg').write_text(svg(content, 1200, 630, 'Hormuz — Give your team AI. Keep control.'))
print('Exported outlined lockups, passage H marks, and social layout. Run build-brand-icons.mjs next.')
