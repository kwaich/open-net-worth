import { contextBridge, ipcRenderer } from 'electron';

if (process.isMainFrame && location.protocol === 'data:') {
  contextBridge.exposeInMainWorld('onwActivity', Object.freeze({
    save: (value: unknown) => ipcRenderer.invoke('desktop-activity:save', value),
    dismiss: () => ipcRenderer.invoke('desktop-activity:dismiss'),
  }));
}
