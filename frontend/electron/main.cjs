const { app, BrowserWindow, dialog } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");

let backendProcess = null;
let mainWindow = null;

function getBackendPath() {
  const executableName = process.platform === "win32" ? "linkedin-scan-api.exe" : "linkedin-scan-api";
  if (app.isPackaged) {
    return path.join(process.resourcesPath, "backend", executableName);
  }
  return null;
}

function getDataDir() {
  return path.join(app.getPath("userData"), "data");
}

function waitForPort(port, timeoutMs = 20000) {
  const startedAt = Date.now();

  return new Promise((resolve, reject) => {
    function check() {
      const socket = net.createConnection({ host: "127.0.0.1", port }, () => {
        socket.destroy();
        resolve();
      });

      socket.on("error", () => {
        socket.destroy();
        if (Date.now() - startedAt > timeoutMs) {
          reject(new Error(`Backend did not start on port ${port}.`));
          return;
        }
        setTimeout(check, 300);
      });
    }

    check();
  });
}

async function startBackend() {
  const port = 8000;
  const dataDir = getDataDir();
  fs.mkdirSync(dataDir, { recursive: true });

  const env = {
    ...process.env,
    PORT: String(port),
    LINKEDIN_SCAN_DATA_DIR: dataDir,
  };

  if (app.isPackaged) {
    const backendPath = getBackendPath();
    backendProcess = spawn(backendPath, [], { env, stdio: "ignore" });
  } else {
    backendProcess = spawn("python", ["-m", "uvicorn", "backend.api:app", "--host", "127.0.0.1", "--port", String(port)], {
      cwd: path.join(__dirname, "..", ".."),
      env,
      stdio: "inherit",
    });
  }

  backendProcess.on("exit", () => {
    backendProcess = null;
  });

  await waitForPort(port);
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1180,
    height: 820,
    minWidth: 900,
    minHeight: 640,
    title: "Monetize360 LinkedIn Scan",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  if (app.isPackaged) {
    mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  } else {
    mainWindow.loadURL("http://127.0.0.1:5173");
  }
}

app.whenReady().then(async () => {
  try {
    await startBackend();
    createWindow();
  } catch (error) {
    dialog.showErrorBox("Unable to start backend", error.message);
    app.quit();
  }
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0 && mainWindow === null) {
    createWindow();
  }
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", () => {
  if (backendProcess) {
    backendProcess.kill();
  }
});
