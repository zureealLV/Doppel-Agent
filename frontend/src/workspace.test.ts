import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { workspaceApi } from "./workspaceApi";
import { WorkspaceController, profileConfiguration, safeMarkdown, searchIndex } from "./workspace";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const conversation = (id: string) => ({ id, title: id, archived: 0, group_id: null, profile_id: null,
  group_name: null, updated_at: "2026-10-01", created_at: "2026-10-01", messages: [] });

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

describe("same-origin persistent workspace API", () => {
  it("uses real conversation endpoints and JSON UI headers, never v1 run substitutions", async () => {
    const fetcher = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(conversation("draft"))));
    vi.stubGlobal("fetch", fetcher);
    await workspaceApi.draft("review");
    expect(fetcher.mock.calls[0]).toMatchObject(["/api/conversations/review-draft", {
      method: "POST", body: "{}", headers: { "Content-Type": "application/json", "X-Doppel-UI": "1" },
    }]);
    await workspaceApi.search("标题 / archived?");
    expect(fetcher.mock.calls[1]?.[0]).toBe("/api/conversations/search?q=" + encodeURIComponent("标题 / archived?"));
  });

  it("sends per-conversation profile and explicit grants to the existing persistent-chat service", async () => {
    const fetcher = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) => new Response('{"run_id":"run","conversation_id":"conv"}'));
    vi.stubGlobal("fetch", fetcher);
    await workspaceApi.start({ prompt: "read source", mode: "review", conversation_id: "conv", config: { profile_id: "model" },
      effort: "quick", allow_write: false, allow_command: false, allow_mcp: false, allow_delegate: false });
    expect(fetcher.mock.calls[0]?.[0]).toBe("/api/runs");
    expect(JSON.parse((fetcher.mock.calls[0]?.[1] as RequestInit).body as string).config).toEqual({ profile_id: "model" });
  });
});

describe("persistent workspace ownership", () => {
  beforeEach(() => {
    vi.spyOn(workspaceApi, "selection").mockResolvedValue({ saved: false, conversation_id: null });
    vi.spyOn(workspaceApi, "saveSelection").mockImplementation(async conversation_id => ({ saved: true, conversation_id }));
  });

  it("restores the workspace-owned choice instead of a browser-origin ID", async () => {
    vi.spyOn(workspaceApi, "health").mockResolvedValue({ status: "ok", workspace: "fixture" });
    vi.spyOn(workspaceApi, "settings").mockResolvedValue({ profiles: [], active_profile_id: "", key_protection: "none" });
    vi.spyOn(workspaceApi, "conversations").mockResolvedValue([]);
    vi.spyOn(workspaceApi, "groups").mockResolvedValue([]);
    vi.mocked(workspaceApi.selection).mockResolvedValue({ saved: true, conversation_id: "owned" });
    const open = vi.spyOn(workspaceApi, "conversation").mockResolvedValue(conversation("owned"));
    const controller = new WorkspaceController({ getItem: key => key === "doppel-conversation" ? "foreign" : null,
      setItem: () => undefined, removeItem: () => undefined });
    await controller.initialize();
    await controller.flushSelection();
    expect(open).toHaveBeenCalledTimes(1);
    expect(open).toHaveBeenCalledWith("owned");
    expect(controller.state.current?.id).toBe("owned");
  });

  it("serializes preference writes before draining a project transition", async () => {
    const first = deferred<{ saved: boolean; conversation_id: string | null }>();
    const save = vi.mocked(workspaceApi.saveSelection).mockReturnValueOnce(first.promise);
    vi.spyOn(workspaceApi, "conversation").mockImplementation(async id => conversation(id));
    const controller = new WorkspaceController();
    await controller.open("first");
    await controller.open("second");
    const flush = controller.flushSelection();
    expect(save.mock.calls.map(call => call[0])).toEqual(["first"]);
    first.resolve({ saved: true, conversation_id: "first" });
    await flush;
    expect(save.mock.calls.map(call => call[0])).toEqual(["first", "second"]);
  });

  it("blocks transition when the preference retry still fails", async () => {
    vi.mocked(workspaceApi.saveSelection).mockRejectedValue(new Error("PRIVATE_DATA"));
    const controller = new WorkspaceController();
    controller.clearSelection();
    await expect(controller.flushSelection()).rejects.toThrow("未切换项目");
    expect(controller.state.error).not.toContain("PRIVATE_DATA");
    expect(workspaceApi.saveSelection).toHaveBeenCalledTimes(2);
  });

  it("rejects close preparation while a Legacy selection request is still pending", async () => {
    const pending = deferred<ReturnType<typeof conversation>>();
    vi.spyOn(workspaceApi, "conversation").mockReturnValue(pending.promise);
    const controller = new WorkspaceController();
    const open = controller.open("pending");
    await expect(controller.prepareClose()).rejects.toThrow("仍在进行");
    expect(controller.busy).toBe(true);
    pending.resolve(conversation("pending"));
    await open;
    await controller.prepareClose();
    expect(controller.busy).toBe(false);
  });
  it("preserves the selected profile when first submission creates a Legacy draft", async () => {
    const controller = new WorkspaceController();
    controller.updateSettings({ active_profile_id: "default", key_protection: "none", profiles:
      ["default", "chosen"].map(id => ({ id, name: id, provider: "mock" as const, preset: "mock", model: "mock",
        base_url: "", input_price: 0, output_price: 0, api_key_saved: false })) });
    await controller.chooseProfile("chosen");
    vi.spyOn(workspaceApi, "draft").mockResolvedValue(conversation("draft"));
    const bind = vi.spyOn(workspaceApi, "setProfile").mockResolvedValue({ ...conversation("draft"), profile_id: "chosen" });
    const start = vi.spyOn(workspaceApi, "start").mockResolvedValue({ run_id: "accepted", conversation_id: "draft" });
    await controller.submit({ prompt: "read evidence.txt", effort: "quick", permissions: {
      workspace_write: false, command_execute: false, mcp_execute: false, delegate: false } });
    expect(start.mock.calls[0]?.[0].config).toEqual({ profile_id: "chosen" });
    expect(bind).toHaveBeenCalledWith("draft", "chosen");
    expect(controller.state.current?.profile_id).toBe("chosen");
    expect(controller.state.profileId).toBe("chosen");
  });

  it.each(["agent", "review"] as const)("binds the selected profile before preparing a %s Legacy draft", async mode => {
    const controller = new WorkspaceController();
    controller.updateSettings({ active_profile_id: "default", key_protection: "none", profiles:
      ["default", "chosen"].map(id => ({ id, name: id, provider: "mock" as const, preset: "mock", model: "mock",
        base_url: "", input_price: 0, output_price: 0, api_key_saved: false })) });
    await controller.chooseProfile("chosen");
    vi.spyOn(workspaceApi, "draft").mockResolvedValue(conversation("draft"));
    const bind = vi.spyOn(workspaceApi, "setProfile").mockResolvedValue({ ...conversation("draft"), profile_id: "chosen" });
    vi.spyOn(workspaceApi, "conversations").mockResolvedValue([]);
    vi.spyOn(workspaceApi, "groups").mockResolvedValue([]);
    const start = vi.spyOn(workspaceApi, "start");
    await controller.prepareDraft(mode);
    expect(controller.state.profileId).toBe("chosen");
    expect(controller.state.current?.profile_id).toBe("chosen");
    expect(bind).toHaveBeenCalledWith("draft", "chosen");
    expect(controller.state.nextMode).toBe(mode);
    expect(start).not.toHaveBeenCalled();
  });

  it("keeps accepted run ownership even when browser storage fails", async () => {
    vi.spyOn(workspaceApi, "start").mockResolvedValue({ run_id: "accepted", conversation_id: "origin" });
    const storage = { getItem: () => null, setItem: () => { throw new Error("storage disabled"); }, removeItem: () => undefined };
    const controller = new WorkspaceController(storage);
    controller.state.current = conversation("origin");
    controller.state.profileId = "model";
    await controller.submit({ prompt: "task", effort: "quick", permissions: {
      workspace_write: false, command_execute: false, mcp_execute: false, delegate: false } });
    expect(controller.state.activeRun?.run_id).toBe("accepted");
    expect(controller.busy).toBe(true);
  });

  it("does not let a terminal run switch the user to a different conversation", async () => {
    vi.spyOn(workspaceApi, "run").mockResolvedValue({ run_id: "run", conversation_id: "origin", status: "completed" });
    vi.spyOn(workspaceApi, "events").mockResolvedValue([]);
    vi.spyOn(workspaceApi, "tasks").mockResolvedValue([]);
    vi.spyOn(workspaceApi, "approvals").mockResolvedValue([]);
    vi.spyOn(workspaceApi, "conversations").mockResolvedValue([]);
    vi.spyOn(workspaceApi, "groups").mockResolvedValue([]);
    const open = vi.spyOn(workspaceApi, "conversation");
    const controller = new WorkspaceController();
    controller.state.current = conversation("elsewhere");
    controller.state.activeRun = { run_id: "run", conversation_id: "origin", status: "running" };
    await controller.refreshRun();
    expect(controller.state.activeRun).toBe(null);
    expect(controller.state.current.id).toBe("elsewhere");
    expect(open).not.toHaveBeenCalled();
  });

  it("does not update a disposed workspace when a pending open completes", async () => {
    const old = deferred<ReturnType<typeof conversation>>();
    vi.spyOn(workspaceApi, "conversation").mockReturnValue(old.promise);
    const controller = new WorkspaceController();
    const first = controller.open("old");
    controller.dispose();
    old.resolve(conversation("old"));
    await first;
    expect(controller.state.current).toBe(null);
  });

  it("preparing a review draft does not start a model run", async () => {
    const draft = vi.spyOn(workspaceApi, "draft").mockResolvedValue(conversation("review"));
    vi.spyOn(workspaceApi, "conversations").mockResolvedValue([]);
    vi.spyOn(workspaceApi, "groups").mockResolvedValue([]);
    const start = vi.spyOn(workspaceApi, "start");
    const controller = new WorkspaceController();
    await controller.prepareDraft("review");
    expect(draft).toHaveBeenCalledWith("review");
    expect(start).not.toHaveBeenCalled();
    expect(controller.state.nextMode).toBe("review");
    vi.restoreAllMocks();
  });

  it("ignores an old selection response after a newer conversation has opened", async () => {
    const old = deferred<ReturnType<typeof conversation>>();
    vi.spyOn(workspaceApi, "conversation").mockImplementation((id) => id === "old" ? old.promise : Promise.resolve(conversation("new")));
    const controller = new WorkspaceController();
    const first = controller.open("old");
    await controller.open("new");
    old.resolve(conversation("old"));
    await first;
    expect(controller.state.current?.id).toBe("new");
    vi.restoreAllMocks();
  });

  it("invalidates pending search when the query is cleared", async () => {
    const old = deferred<never[]>();
    vi.spyOn(workspaceApi, "search").mockReturnValue(old.promise);
    const controller = new WorkspaceController();
    const first = controller.search("previous");
    await controller.search("");
    old.resolve([]);
    await first;
    expect(controller.state.searchResults).toEqual([]);
    expect(controller.state.searching).toBe(false);
    vi.restoreAllMocks();
  });

  it("allows one submitted run and preserves the originating conversation during selection changes", async () => {
    const pending = deferred<{ run_id: string; conversation_id: string }>();
    const start = vi.spyOn(workspaceApi, "start").mockReturnValue(pending.promise);
    const controller = new WorkspaceController();
    controller.state.current = conversation("origin");
    controller.state.profileId = "model";
    const request = { prompt: "actual task", effort: "balanced" as const, permissions: {
      workspace_write: false, command_execute: false, mcp_execute: false, delegate: false } };
    const first = controller.submit(request);
    await controller.submit(request);
    controller.state.current = conversation("elsewhere");
    pending.resolve({ run_id: "run", conversation_id: "origin" });
    await first;
    expect(start).toHaveBeenCalledTimes(1);
    expect(start.mock.calls[0]?.[0].conversation_id).toBe("origin");
    expect(controller.state.activeRun?.conversation_id).toBe("origin");
    expect(controller.state.current.id).toBe("elsewhere");
    vi.restoreAllMocks();
  });
});

describe("workspace presentation and secrets", () => {
  it("escapes model HTML and fenced code before adding limited Markdown markup", () => {
    const html = safeMarkdown('<img src=x onerror=alert(1)>\n\n```html\n<script>bad()</script>\n```\n\n**safe**');
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
    expect(html).toContain("<strong>safe</strong>");
  });

  it("keeps keys request-scoped and rejects non-finite or negative prices", () => {
    const form = { name: "local", preset: "openai", model: "fixture", base_url: "http://127.0.0.1:9999/v1",
      input_price: 0, output_price: 0, api_key: "secret" };
    expect(profileConfiguration(form).api_key).toBe("secret");
    for (const price of [Infinity, NaN, -1]) {
      expect(() => profileConfiguration({ ...form, input_price: price })).toThrow();
    }
    expect(() => profileConfiguration({ ...form, base_url: "https://secret@example.com" })).toThrow();
  });

  it("supports keyboard wrap without a negative index for empty search results", () => {
    expect(searchIndex(0, -1, 0)).toBe(0);
    expect(searchIndex(0, -1, 3)).toBe(2);
    expect(searchIndex(2, 1, 3)).toBe(0);
  });
});
