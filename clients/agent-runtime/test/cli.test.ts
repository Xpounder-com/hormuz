import test from 'node:test';
import assert from 'node:assert/strict';
import { readHostLines, createHostWriter } from '../src/cli.ts';

test('JSONL host bounds partial frames and recovers at the next newline', async () => {
  async function* chunks() {
    yield Buffer.from('first\r\n');
    yield Buffer.from('12345678'); yield Buffer.from('12345678');
    yield Buffer.from('still oversized\nvalid\nlast');
  }
  let exceeded = 0;
  const lines: string[] = [];
  for await (const line of readHostLines(chunks(), () => { exceeded++; }, 12)) lines.push(line);
  assert.equal(exceeded, 1); assert.deepEqual(lines, ['first', 'valid', 'last']);
});
test('JSONL host stops accepting output when its consumer does not drain', () => {
  const frames: Buffer[] = [], completions: Array<() => void> = [];
  let stopped = 0;
  const send = createHostWriter({ write: (frame, complete) => { frames.push(frame); completions.push(complete); } }, () => { stopped++; }, 32);
  send({ value: 'first' }); send({ value: 'second' }); send({ value: 'third' });
  assert.equal(frames.length, 1); assert.equal(stopped, 1);
  completions[0]!(); send({ value: 'after shutdown' });
  assert.equal(frames.length, 1);
});
test('JSONL host preserves UTF8 across chunks without treating code units as bytes', async () => {
  const bytes = Buffer.from('hi 🧭\n');
  async function* chunks() { yield bytes.subarray(0, 4); yield bytes.subarray(4); }
  const lines: string[] = [];
  for await (const line of readHostLines(chunks(), () => assert.fail('within bound'), 8)) lines.push(line);
  assert.deepEqual(lines, ['hi 🧭']);
});
