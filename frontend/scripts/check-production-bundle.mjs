import { readdirSync, readFileSync } from "node:fs";
import { extname, join } from "node:path";
import { stdout } from "node:process";

const forbidden = ["X-Admin-API-Key", "EVIDENCE_V2_ADMIN_API_KEY"];
const assets = readdirSync("dist/assets", { withFileTypes: true })
  .filter((entry) => entry.isFile() && [".js", ".css"].includes(extname(entry.name)))
  .map((entry) => join("dist/assets", entry.name));

for (const asset of assets) {
  const source = readFileSync(asset, "utf8");
  for (const token of forbidden) {
    if (source.includes(token)) {
      throw new Error(`production_bundle_contains_forbidden_admin_secret_marker:${asset}`);
    }
  }
}

stdout.write(`production_bundle_admin_secret_gate=PASS assets=${assets.length}\n`);
