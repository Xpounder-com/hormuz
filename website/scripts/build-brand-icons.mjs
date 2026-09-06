// Run after build-brand-assets.py. Assets are committed; Pages needs no authoring tools.
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';

const root = fileURLToPath(new URL('../', import.meta.url));
const publicDir = path.join(root, 'public');
const mark = JSON.parse(await fs.readFile(path.join(root, 'lib/brand-mark.json'), 'utf8'));
const themes = [
  { id: 'forest', fg: mark.signal, bg: mark.forest },
  { id: 'signal', fg: mark.forest, bg: mark.signal },
  { id: 'transparent', fg: mark.forest, bg: null },
];
function svg(theme, n, micro = false) {
  const view = micro ? 16 : 64;
  const back = theme.bg ? `<rect width="${view}" height="${view}" rx="${view / 4}" fill="${theme.bg}"/>` : '';
  const paths = (micro ? mark.microPaths : mark.paths).map(d => `<path d="${d}"/>`).join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${n}" height="${n}" viewBox="0 0 ${view} ${view}" role="img"><title>Hormuz passage H</title>${back}<g fill="${theme.fg}">${paths}</g></svg>`;
}
for (const theme of themes) {
  const dir = path.join(publicDir, 'brand/icons', theme.id);
  await fs.mkdir(dir, { recursive: true });
  for (const n of mark.sizes) {
    await sharp(Buffer.from(svg(theme, n, n === 16))).png().toFile(path.join(dir, `hormuz-${theme.id}-${n}.png`));
  }
  await fs.writeFile(path.join(dir, `hormuz-${theme.id}-master.svg`), svg(theme, 64));
  await fs.writeFile(path.join(dir, `hormuz-${theme.id}-micro-16.svg`), svg(theme, 16, true));
  const sizes = [16, 32, 48];
  const images = await Promise.all(sizes.map(n => fs.readFile(path.join(dir, `hormuz-${theme.id}-${n}.png`))));
  const header = Buffer.alloc(6 + 16 * images.length);
  header.writeUInt16LE(1, 2); header.writeUInt16LE(images.length, 4);
  let offset = header.length;
  images.forEach((image, i) => {
    const p = 6 + 16 * i;
    header[p] = sizes[i]; header[p + 1] = sizes[i];
    header.writeUInt16LE(1, p + 4); header.writeUInt16LE(32, p + 6);
    header.writeUInt32LE(image.length, p + 8); header.writeUInt32LE(offset, p + 12);
    offset += image.length;
  });
  await fs.writeFile(path.join(dir, 'favicon.ico'), Buffer.concat([header, ...images]));
}
const primary = path.join(publicDir, 'brand/icons/forest');
await fs.copyFile(path.join(primary, 'favicon.ico'), path.join(publicDir, 'favicon.ico'));
await fs.copyFile(path.join(primary, 'hormuz-forest-180.png'), path.join(publicDir, 'apple-touch-icon.png'));

// SVG favicons adapt to the actual viewport so a 16 px tab receives the same
// deliberately wider passage as the dedicated PNG and ICO entry.
const paths = list => list.map(d => `<path d="${d}"/>`).join('');
const adaptive = `<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64" role="img"><title>Hormuz</title><style>.micro{display:none}@media(max-width:16px){.master{display:none}.micro{display:inline}}</style><rect width="64" height="64" rx="16" fill="${mark.forest}"/><g fill="${mark.signal}"><g class="master">${paths(mark.paths)}</g><g class="micro" transform="scale(4)">${paths(mark.microPaths)}</g></g></svg>`;
await fs.writeFile(path.join(publicDir, 'icon.svg'), adaptive + '\n');

// librsvg cannot reliably decode embedded WebP; normalize the existing artwork
// to lossless PNG for this render without changing the public WebP asset.
const art = await sharp(path.join(publicDir, 'brand/controlled-passage.webp')).png().toBuffer();
const social = (await fs.readFile(path.join(root, '.artifacts/brand/social-layout.svg'), 'utf8'))
  .replace('ARTWORK_DATA_URI', `data:image/png;base64,${art.toString('base64')}`);
await sharp(Buffer.from(social)).png().toFile(path.join(publicDir, 'og.png'));
console.log('Exported passage-H icons in 11 sizes, adaptive favicon, Apple icon, and social preview.');
