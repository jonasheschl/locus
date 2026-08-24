import { app, events, init, os, updater, window as nativeWindow } from '@neutralinojs/lib';

let trayAvailable = false;
const updateManifestUrl = import.meta.env.VITE_UPDATE_MANIFEST_URL || '';

function isNeutralinoWindow() {
  return typeof window.NL_MODE !== 'undefined' && window.NL_MODE === 'window';
}

async function showWindow() {
  await nativeWindow.show();
  await nativeWindow.unminimize();
  await nativeWindow.focus();
}

async function hideWindow() {
  if (trayAvailable) await nativeWindow.hide();
}

async function configureTray() {
  try {
    await os.setTray({
      icon: '/dist/icons/locus-32.png',
      menuItems: [
        { id: 'show', text: 'Open Locus' },
        { text: '-' },
        { id: 'quit', text: 'Quit Locus' }
      ]
    });
    trayAvailable = true;
  } catch (error) {
    console.warn('Locus could not create a system tray icon', error);
  }
}

export function isNewerVersion(candidate, current) {
  const candidateParts = candidate.split('-')[0].split('.').map(Number);
  const currentParts = current.split('-')[0].split('.').map(Number);
  const length = Math.max(candidateParts.length, currentParts.length);

  for (let index = 0; index < length; index += 1) {
    const candidatePart = candidateParts[index] || 0;
    const currentPart = currentParts[index] || 0;
    if (candidatePart !== currentPart) return candidatePart > currentPart;
  }
  return false;
}

async function installAvailableUpdate() {
  if (!updateManifestUrl) return;

  try {
    const manifest = await updater.checkForUpdates(updateManifestUrl);
    if (!isNewerVersion(manifest.version, window.NL_APPVERSION)) return;

    await updater.install();
    const quitNow = window.confirm(
      `Locus ${manifest.version} was installed. Quit now to finish the update?`
    );
    if (quitNow) await app.exit();
  } catch (error) {
    console.warn('Locus could not check for or install updates', error);
  }
}

async function handleWindowClose() {
  if (trayAvailable) {
    await hideWindow();
    return;
  }
  await app.exit();
}

async function handleTrayMenu(event) {
  if (event.detail.id === 'show') await showWindow();
  if (event.detail.id === 'quit') await app.exit();
}

export function initializeDesktop() {
  if (!isNeutralinoWindow()) return;

  init();
  events.on('ready', configureTray);
  events.on('ready', installAvailableUpdate);
  events.on('windowClose', handleWindowClose);
  events.on('windowMinimize', hideWindow);
  events.on('trayMenuItemClicked', handleTrayMenu);
}
