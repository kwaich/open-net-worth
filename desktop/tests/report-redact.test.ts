import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { redactLogLines, redactText, type RedactionContext } from '../src/main/report-redact';

interface Context { accounts: string[]; payees: string[]; paths: [string, string][] }
interface Vectors {
  text: { name: string; input: string; context: Context; expected: string }[];
  logs: { name: string; input: string[]; limit: number; context: Context; expected: string[] }[];
}
// The backend's redactor runs the same file (tests/test_problem_report_redaction.py): the two
// implementations may differ in code, never in behaviour.
const vectors = JSON.parse(readFileSync(join(__dirname, '..', '..', 'tests', 'fixtures', 'problem_report_redaction.json'), 'utf8')) as Vectors;
const context = (value: Context): RedactionContext => value;

for (const vector of vectors.text) {
  test(`redaction vector: ${vector.name}`, () => assert.equal(redactText(vector.input, context(vector.context)), vector.expected));
}
for (const vector of vectors.logs) {
  test(`log redaction vector: ${vector.name}`, () => assert.deepEqual(redactLogLines(vector.input, context(vector.context), vector.limit), vector.expected));
}

test('a replacement token containing $ is inserted literally', () => {
  assert.equal(redactText('paid Acme', { payees: ['Acme'], paths: [] }), 'paid [payee]');
  assert.equal(redactText('at /tmp/x', { paths: [['/tmp/x', '$&']] }), 'at $&');
});
