// PWA Install Prompt
let deferredPrompt;
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredPrompt = e;
  setTimeout(() => {
    if (!localStorage.getItem('installPromptDismissed')) {
      document.getElementById('installPrompt').style.display = 'block';
    }
  }, 3000);
});

function installPWA() {
  if (deferredPrompt) {
    deferredPrompt.prompt();
    deferredPrompt.userChoice.then(() => {
      deferredPrompt = null;
      document.getElementById('installPrompt').style.display = 'none';
    });
  }
}

function dismissInstallPrompt() {
  document.getElementById('installPrompt').style.display = 'none';
  localStorage.setItem('installPromptDismissed', 'true');
}

// Service Worker
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    // Detect if we are in Edo version (based on manifest link or URL, but manifest title is easiest)
    const isEdo = document.querySelector('link[href*="manifest_edo.json"]') !== null;
    const swFile = isEdo ? '/static/sw_edo.js' : '/static/sw.js';

    navigator.serviceWorker.register(swFile)
      .then(reg => console.log(`SW registered (${swFile}):`, reg.scope))
      .catch(err => console.log('SW failed:', err));
  });
}
