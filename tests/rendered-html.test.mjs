import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render(pathname = "/") {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request(`http://localhost${pathname}`, { headers: { accept: "text/html" } }),
    {
      ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) },
    },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the AI Wardrobe Lab product shell", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>AI Wardrobe Lab/);
  assert.match(html, /Dress for the room/);
  assert.match(html, /Wardrobe checklist/);
  assert.match(html, /Audience brief/);
  assert.match(html, /Six-lens every outfit/);
  assert.match(html, /Stored locally/);
  assert.doesNotMatch(html, /codex-preview|Your site is taking shape|react-loading-skeleton/);
});

test("server-renders the private iPhone intake route", async () => {
  const response = await render("/mobile");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Opening private intake/);
  assert.match(html, /mobile-intake/);
  assert.doesNotMatch(html, /OPENAI_API_KEY|sk-[A-Za-z0-9_-]{10,}/);
});

test("keeps the browser API key out of the frontend source", async () => {
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(page, /sk-[A-Za-z0-9_-]{10,}/);
  assert.doesNotMatch(page, /OPENAI_API_KEY/);
  assert.match(page, /127\.0\.0\.1:8765/);
  assert.match(page, /panel_size: panelSize/);
  assert.match(page, /Audience size/);
  assert.match(page, /beginNameEdit/);
  assert.match(page, /api\/tournaments/);
  assert.match(page, /Retest with new brief/);
  assert.match(page, /Previous completed runs remain saved locally/);
});

test("uses the production LAN server and tolerates extension root attributes", async () => {
  const launcher = await readFile(new URL("../scripts/start.ps1", import.meta.url), "utf8");
  const layout = await readFile(new URL("../app/layout.tsx", import.meta.url), "utf8");
  const packageJson = JSON.parse(
    await readFile(new URL("../package.json", import.meta.url), "utf8"),
  );
  const compatibilityPatch = await readFile(
    new URL("../scripts/patch_vinext_windows.mjs", import.meta.url),
    "utf8",
  );
  assert.match(launcher, /run start:lan/);
  assert.match(launcher, /Test-AIWardrobeFrontend/);
  assert.match(launcher, /\$webPort = 3001/);
  assert.doesNotMatch(launcher, /run dev:lan/);
  assert.match(layout, /<html lang="en" suppressHydrationWarning>/);
  assert.equal(
    packageJson.scripts.prebuild,
    "node scripts/patch_vinext_windows.mjs",
  );
  assert.match(compatibilityPatch, /split\(path\.sep\)\.join\("\/"\)/);
});
