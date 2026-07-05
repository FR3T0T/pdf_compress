import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { connectQWebChannel } from './bridge/qwebchannel-connect';
import './styles/theme.css';

const container = document.getElementById('root');
if (!container) throw new Error('#root element not found');

// Establish the real bridge (if running inside the PySide6 app) before the
// first render, so bridgeApi's window.BridgeAPI check is accurate from the
// very first mount instead of racing with pages that call it on mount.
connectQWebChannel().then(() => {
  createRoot(container).render(
    <StrictMode>
      <App />
    </StrictMode>
  );
});
