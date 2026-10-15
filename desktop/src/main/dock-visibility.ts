import { app } from 'electron';

export function setDockVisible(visible: boolean): void {
  if (process.platform !== 'darwin' || !app.dock) return;
  if (visible) void app.dock.show();
  else app.dock.hide();
}
