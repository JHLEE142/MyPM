// PacePM 데스크톱 셸.
// 백엔드(uvicorn:8000)와 프론트(next:3000)가 꺼져 있으면 직접 띄우고,
// 앱을 종료하면 "이 앱이 띄운" 프로세스만 정리한다(원래 떠 있던 서버는 건드리지 않음).
const { app, BrowserWindow, dialog } = require("electron");
const { spawn } = require("child_process");
const http = require("http");
const fs = require("fs");
const path = require("path");

// 패키징된 .app은 저장소 밖에서 실행되므로 저장소 경로를 알아야 한다.
// PACEPM_ROOT 환경변수 > 개발 실행 시 상대 경로 > 고정 경로 순으로 결정.
const CANDIDATE_ROOTS = [
  process.env.PACEPM_ROOT,
  path.resolve(__dirname, "..", ".."),
  path.join(process.env.HOME || "", "docenty", "pace-pm"),
].filter(Boolean);
const ROOT = CANDIDATE_ROOTS.find((root) => fs.existsSync(path.join(root, "apps", "api", "app", "main.py")));

const WEB_URL = "http://localhost:3000";
const API_HEALTH = "http://localhost:8000/health";
// Finder에서 실행하면 PATH가 최소값이라 node/npm을 찾도록 보강한다.
const EXTRA_PATH = ["/opt/homebrew/bin", "/usr/local/bin"];
const ENV = {
  ...process.env,
  PATH: [...EXTRA_PATH, process.env.PATH || "/usr/bin:/bin:/usr/sbin:/sbin"].join(":"),
};

const children = [];

function ping(url) {
  return new Promise((resolve) => {
    const request = http.get(url, (response) => {
      response.resume();
      resolve(response.statusCode !== undefined && response.statusCode < 500);
    });
    request.on("error", () => resolve(false));
    request.setTimeout(2000, () => {
      request.destroy();
      resolve(false);
    });
  });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function spawnDetached(command, args, cwd) {
  const child = spawn(command, args, {
    cwd,
    env: ENV,
    detached: true, // 자체 프로세스 그룹 → 종료 시 그룹 전체를 정리할 수 있다
    stdio: "ignore",
  });
  child.on("error", () => {});
  children.push(child);
  return child;
}

async function ensureServers() {
  if (!ROOT) return { ok: false, reason: "PacePM 저장소를 찾을 수 없습니다. PACEPM_ROOT 환경변수를 설정해 주세요." };

  if (!(await ping(API_HEALTH))) {
    spawnDetached(
      path.join(ROOT, "apps", "api", ".venv", "bin", "python"),
      ["-m", "uvicorn", "app.main:app", "--port", "8000"],
      path.join(ROOT, "apps", "api"),
    );
  }
  if (!(await ping(WEB_URL))) {
    const nextBin = path.join(ROOT, "apps", "web", "node_modules", ".bin", "next");
    spawnDetached(nextBin, ["dev"], path.join(ROOT, "apps", "web"));
  }
  for (let attempt = 0; attempt < 90; attempt += 1) {
    if ((await ping(WEB_URL)) && (await ping(API_HEALTH))) return { ok: true };
    await sleep(1000);
  }
  return { ok: false, reason: "서버가 90초 안에 준비되지 않았습니다. /tmp/pacepm-*.log 또는 터미널에서 직접 실행해 확인해 주세요." };
}

function killChildren() {
  for (const child of children) {
    if (child.pid) {
      try {
        process.kill(-child.pid, "SIGTERM"); // 프로세스 그룹째 종료 (next dev의 하위 프로세스 포함)
      } catch {
        try {
          child.kill("SIGTERM");
        } catch {}
      }
    }
  }
  children.length = 0;
}

const LOADING_PAGE = `data:text/html;charset=utf-8,${encodeURIComponent(`
<!doctype html><html><head><meta charset="utf-8"><title>PacePM</title></head>
<body style="margin:0;display:flex;align-items:center;justify-content:center;height:100vh;background:#f4f7f5;font-family:-apple-system,sans-serif;color:#166a58">
<div style="text-align:center"><div style="font-size:40px;font-weight:700">PacePM</div>
<div style="margin-top:12px;color:#5b6b64">서버 시작 중…</div></div></body></html>`)}`;

async function createWindow() {
  const window = new BrowserWindow({
    width: 1440,
    height: 920,
    title: "PacePM",
    titleBarStyle: "hiddenInset",
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  window.loadURL(LOADING_PAGE);

  const status = await ensureServers();
  if (window.isDestroyed()) return;
  if (status.ok) {
    window.loadURL(WEB_URL);
  } else {
    dialog.showErrorBox("PacePM 시작 실패", status.reason);
    app.quit();
  }
}

app.whenReady().then(createWindow);

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

app.on("window-all-closed", () => {
  app.quit();
});

app.on("before-quit", killChildren);
process.on("exit", killChildren);
