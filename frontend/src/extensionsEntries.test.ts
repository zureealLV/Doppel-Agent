// Static root guards only; actual native project/close/OS drain remains S9.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
const source = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
describe("extension center entry/owner lifecycle construction", () => {
  it("mounts one unkeyed center and includes its original promise barrier in both host transitions", () => {
    const app = source("./App.vue"), tag = app.match(/<ExtensionCenter\b[^>]+>/)?.[0] ?? "";
    expect(app.match(/<ExtensionCenter\b/g)).toHaveLength(1);
    expect(tag).toContain('v-show="page === \'extensions\'"'); expect(tag).toContain(':blocked="closing || switching"');
    expect(tag).not.toContain(":key="); expect(tag).not.toContain("v-if=");
    expect(app.match(/await extensionCenter.value\?\.prepareClose\(\); assertTransition\(epoch\);/g)).toHaveLength(2);
    expect(app.match(/extensionCenter.value\?\.finishClose\(\)/g)).toHaveLength(2);
    expect(source("./components/ProjectHome.vue")).toContain("navigate: url => window.location.assign(url)");
  });
  it("remote data remains plain text, never an install/execute/provider/navigation action", () => {
    const center = source("./components/ExtensionCenter.vue"), controller = source("./extensions.ts");
    expect(center).not.toContain("v-html"); expect(center).not.toContain("v-text-html");
    expect(controller).not.toContain("localStorage"); expect(controller).not.toContain("sessionStorage");
    expect(controller).not.toContain("AbortController"); expect(controller).not.toContain("setInterval");
    expect(center).not.toContain("href="); expect(center).not.toContain("runtimeApi");
  });
});
