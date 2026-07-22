import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

function vueFiles(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(directory, entry.name);
    return entry.isDirectory() ? vueFiles(full) : entry.name.endsWith(".vue") ? [full] : [];
  });
}

describe("用户界面中文验收", () => {
  it("does not render internal English enum values or Select as literal copy", () => {
    const root = path.resolve(import.meta.dirname, "..");
    const failures = [];
    for (const file of vueFiles(root)) {
      const template = fs.readFileSync(file, "utf8").split(/<script\b/i)[0];
      if (/>(\s*)(self|Select|pending|unfulfilled|awaiting_report|fulfilled|invalidated|cancelled)(\s*)</i.test(template)
        || /(?:label|title|placeholder)=["'](?:self|Select|pending|unfulfilled|fulfilled)["']/i.test(template)) {
        failures.push(path.relative(root, file));
      }
    }
    expect(failures).toEqual([]);
  });
});
