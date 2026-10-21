import { app, BrowserWindow, ipcMain, net, protocol } from "electron";
import path from "path";
import { fileURLToPath, pathToFileURL } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DIST = path.join(__dirname, "..", "dist");

// `--dev` loads the Vite dev server. Otherwise the built UI is served from dist/ over sensei://,
// a privileged scheme, instead of file:// (whose pages send `Origin: null` to the service).
const DEV = process.argv.includes("--dev");
const UI_URL = process.env.SENSEI_UI_URL ?? (DEV ? "http://127.0.0.1:5180" : "sensei://app/index.html");

protocol.registerSchemesAsPrivileged([
  { scheme: "sensei", privileges: { standard: true, secure: true, supportFetchAPI: true } }
]);

function serveDist() {
  protocol.handle("sensei", (request) => {
    const file = path.normalize(path.join(DIST, decodeURIComponent(new URL(request.url).pathname)));
    if (!file.startsWith(DIST + path.sep)) return new Response("Not found", { status: 404 });
    return net.fetch(pathToFileURL(file).toString());
  });
}

function isUiUrl(url) {
  // compare scheme + host: URL.origin is "null" for every custom scheme
  const target = new URL(url);
  const ui = new URL(UI_URL);
  return target.protocol === ui.protocol && target.host === ui.host;
}

function createWindow() {
  const win = new BrowserWindow({
    width: 980,
    height: 720,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  });

  // The window only ever shows the SENSEI UI: no popups, no navigating elsewhere.
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  win.webContents.on("will-navigate", (event, url) => {
    if (!isUiUrl(url)) event.preventDefault();
  });

  load(win);
}

// The Vite dev server may still be starting; retry until it answers.
function load(win) {
  win.loadURL(UI_URL).catch(() => {
    if (!win.isDestroyed()) setTimeout(() => load(win), 500);
  });
}

// The page gets the optional service token over IPC, so it is never baked into the bundle or a URL.
ipcMain.handle("sensei:config", (event) => {
  if (!isUiUrl(event.senderFrame.url)) throw new Error("sensei:config is only available to the SENSEI UI");
  return { token: process.env.SENSEI_TOKEN || null };
});

app.whenReady().then(() => {
  serveDist();
  createWindow();
});
