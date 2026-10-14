import { readFile, writeFile } from 'node:fs/promises';
const definitions = [
  ['paper-roast', 'Paper roast', '/roast'],
  ['paper-rebuttal', 'Paper rebuttal', '/rebuttal'],
  ['evidence-review', 'Evidence review', '/review'],
];
const catalog = [];
for (const [id, name, command] of definitions) {
  const text = await readFile(new URL(`../skills/${id}/SKILL.md`, import.meta.url), 'utf8');
  const match = /^---\nname: ([a-z-]+)\ndescription: ([^\n]+)\n---\n([\s\S]*)$/.exec(text);
  if (!match || match[1] !== id || text.length > 8000) throw new Error(`Invalid bounded skill: ${id}`);
  catalog.push({ id, name, command, description: match[2], version: 1, instructions: match[3].trim() });
}
const target = new URL('../src/lib/skill-catalog.json', import.meta.url);
const output = JSON.stringify(catalog, null, 2) + '\n';
if (process.argv.includes('--check')) {
  if (await readFile(target, 'utf8') !== output) throw new Error('Skill catalog is stale. Run npm run skills:compile.');
} else await writeFile(target, output);
