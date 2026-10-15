interface Contents { isDestroyed(): boolean }
interface WindowOwner<T extends Contents> { isDestroyed(): boolean; readonly webContents: T }

export class WindowLifetime<T extends Contents> {
  private retired = false;
  constructor(private readonly window: WindowOwner<T>) {}
  retire(): void { this.retired = true; }
  current(): T | null {
    if (this.retired || this.window.isDestroyed()) return null;
    const contents = this.window.webContents;
    return contents.isDestroyed() ? null : contents;
  }
}
