import { describe, expect, it } from "vitest";
import { pageFromHash, pageHash, pageAfterHashChange } from "./navigation";

describe("workspace deep links", () => {
  it("restores all explicit page identities on reload", () => {
    for (const page of ["conversations", "orders", "changes", "extensions", "runtime", "legacy"] as const) {
      expect(pageFromHash(pageHash(page))).toBe(page);
    }
  });
  it("unknown and malformed routes safely choose native conversations", () => {
    for (const hash of ["", "#unknown", "#https://attacker.invalid", "#legacy?secret=bad", "#changes?run=secret", "#extensions?server=secret", "#%6cegacy"]) {
      expect(pageFromHash(hash)).toBe("conversations");
    }
  });
  it("ordinary focus anchors do not switch the selected page", () => {
    expect(pageAfterHashChange("#main-content", "legacy")).toBe("legacy");
    expect(pageAfterHashChange("#runtime", "legacy")).toBe("runtime");
  });
});
