import assert from 'node:assert/strict';
import { test } from 'node:test';
import { desktopProfile } from '../src/main/desktop-profile';

test('a source run is the normal app unless the dev profile is asked for', () => {
  assert.deepEqual(desktopProfile(false, {}), { name: 'Open Net Worth', settingsFolder: null, bookFolder: '.open-net-worth' });
  assert.equal(desktopProfile(false, { ONW_DESKTOP_PROFILE: 'other' }).name, 'Open Net Worth');
});

test('the dev profile has its own name, settings folder and default book', () => {
  assert.deepEqual(desktopProfile(false, { ONW_DESKTOP_PROFILE: 'dev' }),
    { name: 'Open Net Worth Dev', settingsFolder: 'Open Net Worth Dev', bookFolder: '.open-net-worth-dev' });
});

test('an installed app ignores the switch', () => {
  assert.equal(desktopProfile(true, { ONW_DESKTOP_PROFILE: 'dev' }).name, 'Open Net Worth');
});
