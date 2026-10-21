import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// CSS regression contracts, supplemented by the real-browser geometry receipts.
// A string contract alone does not prove layout in the desktop renderer.
describe("conversation viewport boundaries", () => {
  const css = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
  it("retains a viewport-height fallback for engines without dynamic units", () => {
    expect(css).toMatch(/\.classic-shell\s*\{[^}]*height: 100vh;\s*height: 100dvh;/);
    expect(css).toMatch(/html, body, #app\s*\{[^}]*height: 100%;[^}]*overflow: hidden;/);
  });
  it("keeps scroll ownership in messages rather than allowing auto grid rows to grow the page", () => {
    expect(css).toMatch(/\.codex-body\s*\{[^}]*overflow: hidden;[^}]*grid-template-rows: minmax\(0, 1fr\)/);
    expect(css).toMatch(/\.codex-shell \.codex-content > :is\(\.native-workspace, \.legacy-workspace\)\s*\{[^}]*grid-template-rows: minmax\(0, 1fr\)/);
    expect(css).toMatch(/\.codex-shell \.conversation-messages\s*\{[^}]*overflow-y: auto;[^}]*overscroll-behavior-y: contain;/);
  });
  it("overlays the narrow inspector instead of allocating a second row underneath the composer", () => {
    expect(css).toMatch(/@media \(max-width: 1050px\)\s*\{[^\n]*\.codex-shell \.native-inspector\s*\{[^}]*position: absolute;[^}]*inset: 0 0 0 auto;/);
  });
});
