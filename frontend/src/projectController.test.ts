import { describe, expect, it, vi } from "vitest";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";
import { ProjectController } from "./projectController";
import ProjectHome from "./components/ProjectHome.vue";

const record = { project_id: "a".repeat(32), name: "demo", path: "D:/demo", selected_at_ns: 0, opened_at_ns: 0 };
function setup(beforeSwitch = vi.fn().mockResolvedValue(undefined)) {
  const navigate = vi.fn(), afterSwitch = vi.fn();
  const api = { project_list: vi.fn().mockResolvedValue({ ok: true, projects: [record] }),
    project_choose: vi.fn().mockResolvedValue({ ok: true, cancelled: true }),
    project_switch: vi.fn().mockResolvedValue({ ok: true, url: "http://127.0.0.1:12345/", changed: true }) };
  const controller = new ProjectController({ beforeSwitch, afterSwitch, navigate });
  controller.attach(api);
  return { controller, api, navigate, afterSwitch, beforeSwitch };
}

describe("native project switch construction, not real desktop proof", () => {
  it("requires explicit confirmation and a catalog identity", async () => {
    const { controller, api } = setup();
    await controller.refresh();
    expect(await controller.switchTo(record.project_id, false)).toBe(false);
    expect(await controller.switchTo("b".repeat(32), true)).toBe(false);
    expect(await controller.switchTo("D:/other", true)).toBe(false);
    expect(api.project_switch).not.toHaveBeenCalled();
  });
  it("flushes first and freezes duplicate requests until host navigation", async () => {
    const { controller, api, beforeSwitch, navigate, afterSwitch } = setup();
    await controller.refresh();
    const switched = controller.switchTo(record.project_id, true);
    expect(await controller.switchTo(record.project_id, true)).toBe(false);
    expect(await switched).toBe(true);
    expect(beforeSwitch).toHaveBeenCalledOnce();
    expect(api.project_switch).toHaveBeenCalledWith(record.project_id, true);
    expect(navigate).toHaveBeenCalledWith("http://127.0.0.1:12345/");
    expect(afterSwitch).toHaveBeenCalledWith(true);
    expect(controller.state.busy).toBe(true);
  });
  it("failed preparation never reaches the host or leaks raw errors", async () => {
    const { controller, api, navigate } = setup(vi.fn().mockRejectedValue(new Error("PRIVATE_SELECTION")));
    await controller.refresh();
    expect(await controller.switchTo(record.project_id, true)).toBe(false);
    expect(api.project_switch).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
    expect(controller.state.error).not.toContain("PRIVATE");
    expect(controller.state.busy).toBe(false);
  });
  it("preparation timeout cannot initiate a late switch", async () => {
    vi.useFakeTimers();
    try {
      let finish!: () => void;
      const { controller, api } = setup(vi.fn(() => new Promise<void>(resolve => { finish = resolve; })));
      await controller.refresh();
      const result = controller.switchTo(record.project_id, true);
      await vi.advanceTimersByTimeAsync(5000);
      expect(await result).toBe(false);
      finish(); await Promise.resolve();
      expect(api.project_switch).not.toHaveBeenCalled();
      expect(vi.getTimerCount()).toBe(0);
    } finally { vi.useRealTimers(); }
  });
  it("replacement host after flush cannot receive stale project IDs", async () => {
    let finish!: () => void;
    const { controller, api, beforeSwitch } = setup(vi.fn(() => new Promise<void>(resolve => { finish = resolve; })));
    await controller.refresh();
    const result = controller.switchTo(record.project_id, true);
    await vi.waitFor(() => expect(beforeSwitch).toHaveBeenCalled());
    controller.attach(); finish();
    expect(await result).toBe(false);
    expect(api.project_switch).not.toHaveBeenCalled();
  });
  it("folder cancellation does not switch or erase recent projects", async () => {
    const { controller, api } = setup();
    await controller.refresh(); await controller.choose();
    expect(controller.state.projects).toEqual([record]);
    expect(api.project_switch).not.toHaveBeenCalled();
  });
  it("failed target may navigate only to a validated restored local kernel", async () => {
    const { controller, api, navigate, afterSwitch } = setup();
    await controller.refresh();
    api.project_switch.mockResolvedValue({ ok: false, url: "http://127.0.0.1:12346/", changed: false });
    expect(await controller.switchTo(record.project_id, true)).toBe(false);
    expect(navigate).toHaveBeenCalledWith("http://127.0.0.1:12346/");
    expect(afterSwitch).toHaveBeenCalledWith(true);
  });
  it.each(["https://evil.invalid/", "http://user:secret@127.0.0.1:12345/", "http://127.0.0.1:12345/?key=private", "file:///D:/demo"])("rejects invalid returned navigation %s", async url => {
    const { controller, api, navigate } = setup();
    await controller.refresh(); api.project_switch.mockResolvedValue({ ok: true, url, changed: true });
    expect(await controller.switchTo(record.project_id, true)).toBe(false);
    expect(navigate).not.toHaveBeenCalled();
  });
  it("keeps browser-only project opening unavailable rather than inventing a picker", async () => {
    const html = await renderToString(createSSRApp(ProjectHome, {
      blocked: false, beforeSwitch: async () => {}, afterSwitch: () => {},
    }));
    expect(html).toContain("project-open");
    expect(html).toContain("disabled");
    expect(html).toContain("未发送草稿不会自动保存");
    expect(html).toContain("最近项目");
  });
});
