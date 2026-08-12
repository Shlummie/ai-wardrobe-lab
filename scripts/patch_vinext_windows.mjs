import { readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

if (process.platform !== "win32") {
  process.exit(0);
}

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const target = resolve(
  projectRoot,
  "node_modules/vinext/dist/server/static-file-cache.js",
);
const original = "relativePath: path.relative(base, batch[j]),";
const normalized =
  'relativePath: path.relative(base, batch[j]).split(path.sep).join("/"),';
const source = await readFile(target, "utf8");

if (source.includes(normalized)) {
  console.log("Vinext Windows static-asset compatibility patch is already applied.");
  process.exit(0);
}

if (!source.includes(original)) {
  throw new Error(
    "Vinext static-file-cache.js changed; review the Windows path compatibility patch before continuing.",
  );
}

await writeFile(target, source.replace(original, normalized), "utf8");
console.log("Applied Vinext Windows static-asset compatibility patch.");
