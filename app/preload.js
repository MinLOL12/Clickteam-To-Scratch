'use strict';
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('cts', {
  pickFiles: () => ipcRenderer.invoke('pick-files'),
  pickSave: (inputName) => ipcRenderer.invoke('pick-save', inputName),
  runtimeStatus: () => ipcRenderer.invoke('runtime-status'),
  runtimeSetup: () => ipcRenderer.invoke('runtime-setup'),
  ctfakStatus: () => ipcRenderer.invoke('ctfak-status'),
  pickCtfak: () => ipcRenderer.invoke('pick-ctfak'),
  convert: (input, output) => ipcRenderer.invoke('convert', { input, output }),
  openExternal: (url) => ipcRenderer.invoke('open-external', url),
  onLog: (cb) => {
    const listener = (_e, line) => cb(line);
    ipcRenderer.on('log', listener);
    return () => ipcRenderer.removeListener('log', listener);
  },
  onProgress: (cb) => {
    const listener = (_e, p) => cb(p);
    ipcRenderer.on('progress', listener);
    return () => ipcRenderer.removeListener('progress', listener);
  },
  onConvertProgress: (cb) => {
    const listener = (_e, ev) => cb(ev);
    ipcRenderer.on('convert-progress', listener);
    return () => ipcRenderer.removeListener('convert-progress', listener);
  },
});
