// MyPM 데스크톱 셸.
// 백엔드(uvicorn:8000)와 프론트(next:3000)가 꺼져 있으면 직접 띄우고,
// 앱을 종료하면 "이 앱이 띄운" 프로세스만 정리한다(원래 떠 있던 서버는 건드리지 않음).
const { app, BrowserWindow, Menu, dialog, shell } = require("electron");
const { spawn } = require("child_process");
const http = require("http");
const fs = require("fs");
const path = require("path");

// 패키징된 .app은 저장소 밖에서 실행되므로 저장소 경로를 알아야 한다.
// MYPM_ROOT 환경변수 > 개발 실행 시 상대 경로 > 고정 경로 순으로 결정.
const CANDIDATE_ROOTS = [
  process.env.MYPM_ROOT,
  path.resolve(__dirname, "..", ".."),
  path.join(process.env.HOME || "", "test", "MyPM"),
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
  if (!ROOT) return { ok: false, reason: "MyPM 저장소를 찾을 수 없습니다. MYPM_ROOT 환경변수를 설정해 주세요." };

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
  return { ok: false, reason: "서버가 90초 안에 준비되지 않았습니다. /tmp/mypm-*.log 또는 터미널에서 직접 실행해 확인해 주세요." };
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
<!doctype html><html><head><meta charset="utf-8"><title>MyPM</title></head>
<body style="margin:0;display:flex;align-items:center;justify-content:center;height:100vh;background:#f4f7f5;font-family:-apple-system,sans-serif;color:#166a58">
<div style="text-align:center"><div style="font-size:40px;font-weight:700">MyPM</div>
<div style="margin-top:12px;color:#5b6b64">서버 시작 중…</div></div></body></html>`)}`;

// 앱 헤더(높이 64px)를 그대로 타이틀바로 쓴다. 신호등 버튼을 헤더 세로 가운데에 맞추면
// 헤더 어디를 잡아도 창이 끌리고(웹 쪽 .drag-region), 버튼과 로고가 겹치지 않는다.
const TITLE_BAR_HEIGHT = 64;

async function createWindow() {
  const window = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 960,
    minHeight: 640,
    title: "MyPM",
    titleBarStyle: "hiddenInset",
    trafficLightPosition: { x: 18, y: Math.round(TITLE_BAR_HEIGHT / 2) - 8 },
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  window.loadURL(LOADING_PAGE);

  const status = await ensureServers();
  if (window.isDestroyed()) return;
  if (status.ok) {
    window.loadURL(WEB_URL);
  } else {
    dialog.showErrorBox("MyPM 시작 실패", status.reason);
    app.quit();
  }
}

// 기본 메뉴는 영어이고 창 정렬 항목이 없다. 새로고침과 창 위치 맞추기를 단축키로 쓸 수 있게 직접 구성한다.
function buildMenu() {
  const focused = () => BrowserWindow.getFocusedWindow();
  const template = [
    { role: "appMenu" },
    {
      label: "보기",
      submenu: [
        {
          // preload가 없어 IPC를 못 받으므로, 웹 쪽이 듣고 있는 이벤트를 직접 발생시킨다.
          // 데이터만 다시 불러오므로 스크롤과 입력 중이던 값이 유지된다.
          label: "새로고침",
          accelerator: "CmdOrCtrl+R",
          click: () => {
            focused()
              ?.webContents.executeJavaScript('window.dispatchEvent(new CustomEvent("mypm:refresh"))')
              .catch(() => focused()?.webContents.reload());
          },
        },
        {
          label: "강제 새로고침 (페이지 다시 불러오기)",
          accelerator: "Shift+CmdOrCtrl+R",
          click: () => focused()?.webContents.reloadIgnoringCache(),
        },
        { type: "separator" },
        { role: "resetZoom", label: "실제 크기" },
        { role: "zoomIn", label: "확대" },
        { role: "zoomOut", label: "축소" },
        { type: "separator" },
        { role: "togglefullscreen", label: "전체 화면" },
        { role: "toggleDevTools", label: "개발자 도구" },
      ],
    },
    {
      label: "창",
      submenu: [
        { role: "minimize", label: "최소화" },
        { role: "zoom", label: "확대/축소" },
        { type: "separator" },
        {
          label: "화면 가운데로",
          accelerator: "CmdOrCtrl+Alt+C",
          click: () => focused()?.center(),
        },
        {
          label: "기본 크기로 되돌리기",
          accelerator: "CmdOrCtrl+Alt+0",
          click: () => {
            const window = focused();
            if (!window) return;
            window.unmaximize();
            window.setSize(1440, 920);
            window.center();
          },
        },
        { type: "separator" },
        { role: "close", label: "닫기" },
      ],
    },
    { role: "editMenu", label: "편집" },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

app.whenReady().then(() => {
  buildMenu();
  return createWindow();
});

// 외부 링크는 앱 창 대신 기본 브라우저에서 연다(앱이 낯선 페이지로 이동해 갇히지 않도록).
app.on("web-contents-created", (_event, contents) => {
  contents.setWindowOpenHandler(({ url }) => {
    if (!url.startsWith(WEB_URL)) {
      void shell.openExternal(url);
      return { action: "deny" };
    }
    return { action: "allow" };
  });
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

app.on("window-all-closed", () => {
  app.quit();
});

app.on("before-quit", killChildren);
process.on("exit", killChildren);
