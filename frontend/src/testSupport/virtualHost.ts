// Test-only synthetic Vue renderer. No jsdom/browser/native dependency or OS IO.
// Exercises compiled SFC setup/watch/lifecycle/props and explicitly invoked model
// handlers. Not HTML parsing/DOM dispatch/layout/focus/accessibility/escaping proof.
import { createRenderer, defineComponent, h, nextTick, shallowReactive, shallowRef, type Component } from "vue";

export class VirtualNode {
  parent: VirtualNode | null = null;
  children: VirtualNode[] = [];
  props: Record<string, unknown> = {};
  style: Record<string, string> = { display: "" };
  value: unknown = "";
  defaultValue = "";
  checked = false;
  selected = false;
  selectedIndex = -1;
  composing = false;
  private listeners = new Map<string, Set<EventListener>>();
  constructor(readonly tag: string, public text = "") {}
  get tagName(): string { return this.tag.toUpperCase(); }
  get type(): unknown { return this.props.type; }
  get multiple(): boolean { return this.props.multiple === true || this.props.multiple === ""; }
  get parentNode(): VirtualNode | null { return this.parent; }
  get options(): VirtualNode[] { return descendants(this).filter(node => node.tag === "option"); }
  get textContent(): string { return this.text + this.children.map(node => node.textContent).join(""); }
  getRootNode(): VirtualNode { let root: VirtualNode = this; while (root.parent) root = root.parent; return root; }
  getAttribute(name: string): string | null { const value = this.props[name]; return value == null ? null : String(value); }
  setAttribute(name: string, value: string): void { this.props[name] = value; }
  removeAttribute(name: string): void { delete this.props[name]; }
  addEventListener(name: string, listener: EventListener): void {
    if (!this.listeners.has(name)) this.listeners.set(name, new Set()); this.listeners.get(name)!.add(listener);
  }
  removeEventListener(name: string, listener: EventListener): void { this.listeners.get(name)?.delete(listener); }
  // Installed Vue runtime-dom directives may register listeners. Tests do NOT
  // dispatch fake native events; model handlers below explicitly change inputs.
}
function detach(node: VirtualNode): void {
  if (node.parent) { const at = node.parent.children.indexOf(node); if (at >= 0) node.parent.children.splice(at, 1); }
  node.parent = null;
}
function insert(node: VirtualNode, parent: VirtualNode, anchor: VirtualNode | null = null): void {
  detach(node); const at = anchor ? parent.children.indexOf(anchor) : -1;
  parent.children.splice(at >= 0 ? at : parent.children.length, 0, node); node.parent = parent;
}
const renderer = createRenderer<VirtualNode, VirtualNode>({
  patchProp(node, key, _old, value) {
    node.props[key] = value;
    if (key === "value") node.value = value;
    if (key === "checked") node.checked = Boolean(value);
    if (key === "selected") node.selected = Boolean(value);
    if (key === "style" && value && typeof value === "object") Object.assign(node.style, value);
  },
  insert, remove: detach,
  createElement: tag => new VirtualNode(tag), createText: text => new VirtualNode("#text", text),
  createComment: text => new VirtualNode("#comment", text), setText: (node, text) => { node.text = text; },
  setElementText(node, text) { for (const child of node.children) child.parent = null; node.children = []; node.text = text; },
  parentNode: node => node.parent,
  nextSibling(node) { if (!node.parent) return null; return node.parent.children[node.parent.children.indexOf(node) + 1] ?? null; },
  setScopeId(node, id) { node.props[id] = ""; },
  insertStaticContent(content, parent, anchor) {
    // Opaque compiler static text, deliberately not an HTML/DOM parser.
    const node = new VirtualNode("#static", content); insert(node, parent, anchor); return [node, node];
  },
});
export function mountVirtual<T>(component: Component, initial: { active: boolean; blocked: boolean; sourceRunId: string }) {
  const mounted = mountVirtualProps(component, initial);
  return { ...mounted, exposed: () => mounted.exposed<T>() };
}
export function mountVirtualProps<P extends object>(component: Component, initial: P, configure?: (app: ReturnType<typeof renderer.createApp>) => void) {
  // Keep externally root-owned class/controller identity, not a deep proxy of
  // its private fields/Map/API. Existing primitive-prop mounts remain supported.
  const root = new VirtualNode("#root"), props = shallowReactive({ ...initial }), instance = shallowRef<unknown>(null);
  const wrapper = defineComponent({ setup: () => () => h(component, { ...props, ref: instance }) });
  const app = renderer.createApp(wrapper); configure?.(app); app.mount(root); let mounted = true;
  return { root, props, exposed<T>(): T { if (!instance.value) throw new Error("virtual component not mounted"); return instance.value as T; },
    unmount() { if (mounted) { mounted = false; app.unmount(); } } };
}
export function descendants(root: VirtualNode): VirtualNode[] {
  return root.children.flatMap(child => [child, ...descendants(child)]);
}
export function button(root: VirtualNode, phrase: string): VirtualNode {
  const matches = descendants(root).filter(node => node.tag === "button" && node.textContent.includes(phrase));
  if (matches.length !== 1) throw new Error(`virtual button match count ${matches.length}: ${phrase}`); return matches[0]!;
}
function enabled(node: VirtualNode): boolean {
  for (let current: VirtualNode | null = node; current; current = current.parent) {
    if (current.props.inert === true || current.props.inert === "") return false;
    if ((current === node || current.tag === "fieldset") && (current.props.disabled === true || current.props.disabled === "")) return false;
  }
  return true;
}
export function click(node: VirtualNode): unknown {
  if (!enabled(node)) throw new Error("virtual disabled/inert input cannot be invoked");
  const handler = node.props.onClick;
  if (typeof handler !== "function") throw new Error("virtual input has no compiler click handler");
  return handler({ preventDefault() {}, stopPropagation() {}, target: node, currentTarget: node });
}
export async function checkLabel(root: VirtualNode, phrase: string, value = true): Promise<void> {
  const labels = descendants(root).filter(node => node.tag === "label" && node.textContent.includes(phrase));
  if (labels.length !== 1) throw new Error(`virtual label match count ${labels.length}: ${phrase}`);
  const input = descendants(labels[0]!).find(node => node.tag === "input" && node.type === "checkbox");
  if (!input || !enabled(input)) throw new Error("virtual checkbox unavailable");
  const assign = input.props["onUpdate:modelValue"];
  if (typeof assign !== "function") throw new Error("virtual checkbox has no compiler model handler");
  assign(value); await flushVue();
}
export async function flushVue(): Promise<void> {
  // Bounded in-memory promise/watch continuations, no OS timers/polling.
  for (let step = 0; step < 24; step++) { await Promise.resolve(); await nextTick(); }
}
