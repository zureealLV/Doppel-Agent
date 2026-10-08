import { describe, expect, it, vi } from "vitest";
import { DesktopController } from "./desktopBridge";

describe("desktop bridge (not native-window acceptance)", () => {
  it("awaits close preparation before the host action and gates duplicate clicks", async () => {
    let finish!: () => void;
    const prepare = vi.fn(() => new Promise<void>(resolve => { finish = resolve; }));
    const after = vi.fn();
    const action = vi.fn().mockResolvedValue(true);
    const controller = new DesktopController({ beforeClose: prepare, afterClose: after });
    controller.attach({ window_action: action });
    const close = controller.act("close");
    await vi.waitFor(() => expect(prepare).toHaveBeenCalledTimes(1));
    expect(action).not.toHaveBeenCalled();
    expect(controller.state.preparingClose).toBe(true);
    expect(await controller.act("close")).toBe(false);
    finish();
    expect(await close).toBe(true);
    expect(action.mock.calls).toEqual([["close"]]);
    expect(after).toHaveBeenCalledWith(true);
    expect(controller.state.busy).toBe(false);
  });
  it("keeps the window open on a failed flush, hides raw errors and permits retry", async () => {
    const prepare = vi.fn().mockRejectedValueOnce(new Error("private preference detail")).mockResolvedValue(undefined);
    const after = vi.fn();
    const action = vi.fn().mockResolvedValue(true);
    const controller = new DesktopController({ beforeClose: prepare, afterClose: after });
    controller.attach({ window_action: action });
    expect(await controller.act("close")).toBe(false);
    expect(action).not.toHaveBeenCalled();
    expect(controller.state.error).toContain("准备尚未完成");
    expect(controller.state.error).toContain("子任务");
    expect(controller.state.error).not.toContain("private");
    expect(after).toHaveBeenCalledWith(false);
    expect(await controller.act("close")).toBe(true);
    expect(controller.state.error).toBe("");
  });
  it("bounds close preparation and never closes later when a timed-out flush finishes", async () => {
    vi.useFakeTimers();
    try {
      let finish!: () => void;
      const after = vi.fn(), action = vi.fn().mockResolvedValue(true);
      const controller = new DesktopController({
        beforeClose: () => new Promise<void>(resolve => { finish = resolve; }), afterClose: after,
      });
      controller.attach({ window_action: action });
      const close = controller.act("close");
      await vi.advanceTimersByTimeAsync(5000);
      expect(await close).toBe(false);
      expect(after).toHaveBeenCalledWith(false);
      expect(controller.state.busy).toBe(false);
      expect(controller.state.preparingClose).toBe(false);
      finish(); await Promise.resolve();
      expect(action).not.toHaveBeenCalled();
      expect(vi.getTimerCount()).toBe(0);
    } finally { vi.useRealTimers(); }
  });
  it("does not invoke a detached or replacement host after close preparation", async () => {
    let finish!: () => void;
    const prepare = vi.fn(() => new Promise<void>(resolve => { finish = resolve; }));
    const oldAction = vi.fn(), newAction = vi.fn();
    const controller = new DesktopController({ beforeClose: prepare });
    controller.attach({ window_action: oldAction });
    const close = controller.act("close");
    await vi.waitFor(() => expect(prepare).toHaveBeenCalled());
    controller.attach({ window_action: newAction });
    finish();
    expect(await close).toBe(false);
    expect(oldAction).not.toHaveBeenCalled();
    expect(newAction).not.toHaveBeenCalled();
    expect(controller.state.error).toContain("连接已变化");
  });
  it("does not flush navigation for minimize, maximize or restore", async () => {
    const prepare = vi.fn(), after = vi.fn(), action = vi.fn().mockResolvedValue(true);
    const controller = new DesktopController({ beforeClose: prepare, afterClose: after });
    controller.attach({ window_action: action });
    for (const name of ["minimize", "maximize", "restore"] as const) expect(await controller.act(name)).toBe(true);
    expect(prepare).not.toHaveBeenCalled();
    expect(after).not.toHaveBeenCalled();
  });
  it("does not act without a ready host bridge", async () => {
    const controller = new DesktopController();
    expect(await controller.act("close")).toBe(false);
    expect(controller.state.ready).toBe(false);
  });
  it("uses only the host action API, with duplicate actions gated", async () => {
    let finish!: (value: boolean) => void;
    const action = vi.fn(() => new Promise<boolean>(resolve => { finish = resolve; }));
    const controller = new DesktopController();
    controller.attach({ window_action: action });
    const first = controller.act("maximize");
    expect(await controller.act("close")).toBe(false);
    finish(true);
    expect(await first).toBe(true);
    expect(action.mock.calls).toEqual([["maximize"]]);
    expect(controller.state.busy).toBe(false);
  });
  it("reports host failures and permits retry without fabricating success", async () => {
    const action = vi.fn().mockRejectedValueOnce(new Error("host unavailable")).mockResolvedValue(true);
    const controller = new DesktopController();
    controller.attach({ window_action: action });
    expect(await controller.act("minimize")).toBe(false);
    expect(controller.state.error).toBe("host unavailable");
    expect(await controller.act("minimize")).toBe(true);
    expect(controller.state.error).toBe("");
  });
  it("rejects unknown runtime action values", async () => {
    const action = vi.fn();
    const controller = new DesktopController();
    controller.attach({ window_action: action });
    expect(await controller.act("delete-files" as "close")).toBe(false);
    expect(action).not.toHaveBeenCalled();
  });
});
