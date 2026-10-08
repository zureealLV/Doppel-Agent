// Static root/entry guards only. Changes child virtual-host mounting is defined
// separately; root browser navigation/timeout/backend/native drain remains S9.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
const source = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
describe("Changes entry and transition source construction", () => {
  it("retains all engine entries, mounts Changes once, forwards native selection explicitly and never converts Legacy", () => {
    const app = source("./App.vue"), conversation = source("./components/ConversationWorkspace.vue"), orders = source("./components/WorkOrdersWorkspace.vue");
    expect(app).toContain('v-show="page === \'changes\'"'); expect(app).toContain(':source-run-id="changesSourceRun"');
    expect(app).toContain('v-if="page === \'legacy\'"'); expect(app).toContain('v-if="page === \'runtime\'"');
    expect(app.match(/@open-changes="openChanges"/g)).toHaveLength(2);
    expect(conversation).toContain("emit('open-changes', state.selectedRunId)"); expect(orders).toContain("emit('open-changes', state.runId)");
    expect(source("./components/LegacyConversationWorkspace.vue")).not.toContain("open-changes");
  });
  it("pins timeout preparation generations after each awaited step and includes Changes in both barriers", () => {
    const app = source("./App.vue");
    expect(app.match(/await changesWorkspace.value\?\.prepareClose\(\); assertTransition\(epoch\);/g)).toHaveLength(2);
    expect(app).toContain("const epoch = ++transitionEpoch"); expect(app).toContain("transitionEpoch++");
    expect(app).toContain("if (closing.value || switching.value)");
    expect(app).toContain("changesWorkspace.value?.finishClose()");
  });
  it("project switching uses the validated host URL for a fresh page, not a keyed remount that forgets pending reviews", () => {
    const app = source("./App.vue"), home = source("./components/ProjectHome.vue"), project = source("./projectController.ts");
    const changesTag = app.match(/<ChangesWorkspace\b[^>]+>/)?.[0] ?? "";
    expect(changesTag).toContain(':blocked="closing || switching"'); expect(changesTag).not.toContain(":key=");
    expect(home).toContain("navigate: url => window.location.assign(url)");
    expect(project).toContain("const url = desktopUrl(result.url)");
    expect(project).toContain("this.hooks.navigate(url)");
    expect(project).toContain('url.hostname !== "127.0.0.1"');
    expect(project).toContain("url.username || url.password"); expect(project).toContain("url.search || url.hash");
    // These strings are not proof that browser navigation or host cleanup occurred.
  });
});
