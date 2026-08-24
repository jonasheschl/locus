import { mount } from 'svelte';
import App from './App.svelte';
import { initializeDesktop } from './desktop.js';
import './styles.css';

mount(App, { target: document.getElementById('app') });
initializeDesktop();

if (import.meta.env.MODE !== 'desktop' && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js').catch((error) => {
      console.warn('Locus service worker registration failed', error);
    });
  });
}
