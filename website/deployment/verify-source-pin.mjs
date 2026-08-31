import assert from 'node:assert/strict';
import { appendFile, readFile } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

export function validateSourcePin(value) {
  assert.ok(value && typeof value === 'object' && !Array.isArray(value), 'Expected a source-pin object');
  assert.deepEqual(Object.keys(value).sort(), ['repository', 'revision'], 'Unexpected source-pin fields');
  assert.equal(value.repository, 'Xpounder-com/hormuz', 'The source repository is fixed');
  assert.equal(typeof value.revision, 'string', 'Expected a commit revision');
  assert.match(value.revision, /^[a-f0-9]{40}$/, 'Pin a full, lowercase commit SHA, never a branch or tag');
  return value.revision;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const revision = validateSourcePin(JSON.parse(await readFile('site-source.json', 'utf8')));
  assert.ok(process.env.GITHUB_OUTPUT, 'This entrypoint runs inside GitHub Actions');
  await appendFile(process.env.GITHUB_OUTPUT, `revision=${revision}\n`);
  console.log(`Reviewed website source: Xpounder-com/hormuz@${revision}`);
}
