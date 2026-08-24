import { mount } from 'svelte';
import App from './App.svelte';
import './styles.css';

mount(App, { target: document.getElementById('app') });

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js').catch((error) => {
      console.warn('Locus service worker registration failed', error);
    });
  });
}
