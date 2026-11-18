const {contextBridge, ipcRenderer} = require('electron');
contextBridge.exposeInMainWorld('inntorqueDesktop', {
  ready: () => ipcRenderer.invoke('desktop:ready'),
  saveData: packet => ipcRenderer.invoke('desktop:save-data', packet),
  openDataFolder: () => ipcRenderer.invoke('desktop:open-data'),
  dataDirectory: () => ipcRenderer.invoke('desktop:data-directory'),
  activity: active => ipcRenderer.send('desktop:activity', active),
});
window.addEventListener('wheel', event => {
  if (event.ctrlKey || event.metaKey) event.preventDefault();
}, {passive: false});
