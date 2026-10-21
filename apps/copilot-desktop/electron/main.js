import { app, BrowserWindow } from "electron";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const UI_URL = process.env.SENSEI_UI_URL ?? "http://127.0.0.1:5180";

function createWindow() {
  const win = new BrowserWindow({
    width: 980,
    height: 720,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false
    }
  });

  load(win);
}

// The Vite dev server may still be starting; retry until it answers.
function load(win) {
  win.loadURL(UI_URL).catch(() => {
    if (!win.isDestroyed()) setTimeout(() => load(win), 500);
  });
}

app.whenReady().then(createWindow);
