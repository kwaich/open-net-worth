import { powerSaveBlocker } from 'electron';

// 'prevent-app-suspension' blocks idle system sleep only: the display may still turn off, and a closed lid still sleeps.
let blocker: number | null = null;

export function setKeepAwake(enabled: boolean): void {
  if (enabled && blocker === null) blocker = powerSaveBlocker.start('prevent-app-suspension');
  else if (!enabled && blocker !== null) {
    if (powerSaveBlocker.isStarted(blocker)) powerSaveBlocker.stop(blocker);
    blocker = null;
  }
}
