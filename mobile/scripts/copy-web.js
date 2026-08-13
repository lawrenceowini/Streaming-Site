// Copies the canonical web frontend into this project's www/ folder.
// There is deliberately only one index.html for both the website (Vercel)
// and the native app -- this script is how the native side stays in sync
// with it, rather than maintaining two copies that can quietly drift apart.
// Run automatically by `npm run sync` (see package.json), or run it by
// itself with `node scripts/copy-web.js` any time you've edited
// ../frontend/index.html and want the native projects to pick it up.

const fs = require("fs");
const path = require("path");

const FRONTEND_DIR = path.join(__dirname, "..", "..", "frontend");
const WWW_DIR = path.join(__dirname, "..", "www");

const files = ["index.html", "sw.js"];

for (const file of files) {
  const src = path.join(FRONTEND_DIR, file);
  const dest = path.join(WWW_DIR, file);
  if (!fs.existsSync(src)) {
    console.warn(`Skipping ${file}: not found at ${src}`);
    continue;
  }
  fs.copyFileSync(src, dest);
  console.log(`Copied ${file} -> www/${file}`);
}
