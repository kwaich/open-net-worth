import type { BookFolder } from './book-folder';
import type { BookEntry } from './book-switch';

interface InitialChoiceActions {
  open(folder: BookFolder, entry: BookEntry): Promise<boolean>;
}

// An empty folder starts setup straight away; restoring a backup is offered on its first step.
export class InitialBookChoice {
  constructor(private readonly actions: InitialChoiceActions) {}

  select(folder: BookFolder, restore = false): Promise<boolean> {
    return this.actions.open(folder, restore && !folder.existing ? 'restore' : 'folder');
  }
}
