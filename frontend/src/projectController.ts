import { reactive } from "vue";

export interface RecentProject { project_id: string; name: string; path: string; opened_at_ns: number; selected_at_ns: number }
export interface ProjectResult { ok: boolean; url?: string; changed?: boolean; cancelled?: boolean; projects?: RecentProject[]; project?: RecentProject; error?: string }
export interface ProjectDesktopApi {
  project_list: () => Promise<ProjectResult>;
  project_choose: () => Promise<ProjectResult>;
  project_switch: (projectId: string, cancelAndDrain: boolean) => Promise<ProjectResult>;
}

function desktopUrl(value: string): string {
  const url = new URL(value);
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || !url.port || Number(url.port) < 1
      || url.username || url.password || url.pathname !== "/" || url.search || url.hash) throw new Error("invalid project URL");
  return url.href;
}

export class ProjectController {
  readonly state = reactive({ ready: false, busy: false, switching: false, projects: [] as RecentProject[], error: "" });
  private api?: ProjectDesktopApi;
  constructor(private readonly hooks: {
    beforeSwitch: () => Promise<void>;
    afterSwitch: (navigating: boolean) => void;
    navigate: (url: string) => void;
  }, private readonly prepareWaitMs = 5000) {}

  attach(api?: Partial<ProjectDesktopApi>): void {
    this.api = api && [api.project_list, api.project_choose, api.project_switch].every(fn => typeof fn === "function")
      ? api as ProjectDesktopApi : undefined;
    this.state.ready = Boolean(this.api);
    if (!this.api) this.state.projects = [];
  }

  async refresh(): Promise<void> {
    if (!this.api || this.state.busy) return;
    const api = this.api;
    this.state.busy = true; this.state.error = "";
    try {
      const result = await api.project_list();
      if (this.api !== api) return;
      if (!result.ok || !Array.isArray(result.projects)) throw new Error("catalog unavailable");
      this.state.projects = result.projects;
    } catch { this.state.error = "无法读取最近项目，请重试。"; }
    finally { this.state.busy = false; }
  }

  async choose(): Promise<void> {
    if (!this.api || this.state.busy) return;
    const api = this.api;
    this.state.busy = true; this.state.error = "";
    try {
      const result = await api.project_choose();
      if (this.api !== api) return;
      if (!result.ok) throw new Error("selection unavailable");
      if (result.cancelled) return;
      if (!result.project) throw new Error("invalid selection");
      this.state.projects = [result.project, ...this.state.projects.filter(p => p.project_id !== result.project!.project_id)].slice(0, 20);
    } catch { this.state.error = "目录选择失败；请选择仍存在的本地项目目录。"; }
    finally { this.state.busy = false; }
  }

  async switchTo(projectId: string, confirmed: boolean): Promise<boolean> {
    if (!this.api || this.state.busy || !confirmed || !/^[0-9a-f]{32}$/.test(projectId)
        || !this.state.projects.some(p => p.project_id === projectId)) return false;
    const api = this.api;
    let navigating = false;
    this.state.busy = true; this.state.switching = true; this.state.error = "";
    try {
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        await Promise.race([
          Promise.resolve().then(() => this.hooks.beforeSwitch()),
          new Promise<never>((_resolve, reject) => { timer = setTimeout(() => reject(new Error("preparation timeout")), this.prepareWaitMs); }),
        ]);
      } finally { if (timer !== undefined) clearTimeout(timer); }
      if (api !== this.api) throw new Error("host replaced");
      // No timeout race around an admitted host cleanup: no fake late switch.
      const result = await api.project_switch(projectId, true);
      if (api !== this.api) throw new Error("host replaced");
      if (result.url) {
        const url = desktopUrl(result.url);
        this.hooks.navigate(url);
        navigating = true;
      }
      if (!result.ok) { this.state.error = "切换未完成；旧项目可能已恢复，请查看当前项目或重试。"; return false; }
      if (!navigating) throw new Error("missing target URL");
      return true;
    } catch { this.state.error = "项目切换准备尚未完成；请检查选择保存、子任务原请求与草稿退出确认，以及原任务和资源清理。"; return false; }
    finally {
      if (!navigating) { this.state.busy = false; this.state.switching = false; }
      this.hooks.afterSwitch(navigating);
    }
  }
}
