import assert from 'node:assert/strict';
import { test } from 'node:test';
import { RELEASE_LABEL, displayVersion } from '../src/main/feature-flags';

test('the About panel names the version with the beta label, never inside the version number', () => {
  assert.equal(RELEASE_LABEL, 'Beta');
  assert.equal(displayVersion('0.1.6'), '0.1.6 (Beta)');
});
