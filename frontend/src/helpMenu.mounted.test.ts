// Compiled Vue / synthetic host only. Real browser focus/layout is checked separately.
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { toRaw } from 'vue';
import HelpMenu from './components/HelpMenu.vue';
import { button, click, descendants, flushVue, mountVirtualProps, VirtualNode } from './testSupport/virtualHost';

let focused: VirtualNode | undefined;
const listeners = new Map<string, EventListener>();
const mounts: ReturnType<typeof mountVirtualProps>[] = [];
beforeEach(() => {
  vi.stubGlobal('document', {
    addEventListener: vi.fn((name: string, handler: EventListener) => listeners.set(name, handler)),
    removeEventListener: vi.fn((name: string) => listeners.delete(name)),
  });
  Object.defineProperties(VirtualNode.prototype, {
    focus: { configurable: true, value() { focused = this; } },
    contains: { configurable: true, value(node: VirtualNode) { return node === this || descendants(this).includes(node); } },
    querySelectorAll: { configurable: true, value() { return descendants(this).filter(node => node.props.role === 'menuitem'); } },
    showModal: { configurable: true, value: vi.fn(function(this: VirtualNode & {open: boolean}) { this.open = true; }) },
    close: { configurable: true, value: vi.fn(function(this: VirtualNode & {open: boolean}) { this.open = false; }) },
  });
});
afterEach(() => {
  mounts.splice(0).forEach(m => m.unmount());
  for (const name of ['focus', 'contains', 'querySelectorAll', 'showModal', 'close']) Reflect.deleteProperty(VirtualNode.prototype, name);
  focused = undefined; listeners.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks();
});
function fixture() { const m = mountVirtualProps(HelpMenu, { blocked: false }); mounts.push(m); return m; }
function key(node: VirtualNode, value: string) {
  const preventDefault = vi.fn();
  (node.props.onKeydown as (e: unknown) => void)({ key: value, preventDefault, target: focused ?? node });
  return preventDefault;
}

it('opens the real menu and wraps arrow navigation; Escape returns focus to Help', async () => {
  const m = fixture(); await click(button(m.root, '帮助')); await flushVue();
  const items = descendants(m.root).filter(n => n.props.role === 'menuitem');
  expect(items.map(n => n.textContent)).toEqual(['使用文档', '快捷键', '故障排除', '问题反馈', '关于 Doppel-Agent']);
  expect(toRaw(focused) === toRaw(items[0])).toBe(true);
  const menu = descendants(m.root).find(n => n.props.role === 'menu')!;
  expect(key(menu, 'ArrowUp')).toHaveBeenCalled(); expect(toRaw(focused) === toRaw(items[4])).toBe(true);
  key(menu, 'Home'); expect(toRaw(focused) === toRaw(items[0])).toBe(true);
  key(menu, 'End'); expect(toRaw(focused) === toRaw(items[4])).toBe(true);
  key(menu, 'ArrowDown'); expect(toRaw(focused) === toRaw(items[0])).toBe(true);
  key(menu, 'Escape'); await flushVue();
  expect(descendants(m.root).some(n => n.props.role === 'menu')).toBe(false);
  expect(toRaw(focused) === toRaw(button(m.root, '帮助'))).toBe(true);
});

it('closes on outside pointer/focus and Tab without trapping tab navigation', async () => {
  const m = fixture();
  for (const name of ['pointerdown', 'focusin']) {
    await click(button(m.root, '帮助')); await flushVue();
    listeners.get(name)!({ target: new VirtualNode('outside') } as unknown as Event); await flushVue();
    expect(descendants(m.root).some(n => n.props.role === 'menu')).toBe(false);
  }
  await click(button(m.root, '帮助')); await flushVue();
  const menu = descendants(m.root).find(n => n.props.role === 'menu')!;
  expect(key(menu, 'Tab')).not.toHaveBeenCalled(); await flushVue();
  expect(descendants(m.root).some(n => n.props.role === 'menu')).toBe(false);
});

it('uses only fixed GitHub links, retains native activation, and never uploads data', async () => {
  const m = fixture(); await click(button(m.root, '帮助')); await flushVue();
  const links = descendants(m.root).filter(n => n.tag === 'a' && n.props.role === 'menuitem');
  expect(links.map(n => n.props.href)).toEqual([
    'https://github.com/zureealLV/Doppel-Agent/blob/main/docs/USER_GUIDE_CN.md',
    'https://github.com/zureealLV/Doppel-Agent/blob/main/docs/TROUBLESHOOTING_CN.md',
    'https://github.com/zureealLV/Doppel-Agent/issues',
  ]);
  for (const link of links) { expect(link.props.target).toBe('_blank'); expect(link.props.rel).toBe('noopener noreferrer'); }
  const menu = descendants(m.root).find(n => n.props.role === 'menu')!;
  expect(key(menu, 'Enter')).not.toHaveBeenCalled();
  await click(links[0]!); await flushVue();
  expect(descendants(m.root).some(n => n.props.role === 'menu')).toBe(false);
});

it('opens shortcuts/about, closes while host is blocked, and removes listeners', async () => {
  const m = fixture();
  await click(button(m.root, '帮助')); await flushVue(); await click(button(m.root, '快捷键')); await flushVue();
  expect(Reflect.get(VirtualNode.prototype, 'showModal')).toHaveBeenCalledTimes(1);
  expect(m.root.textContent).toContain('输入法组字时不发送');
  await click(button(m.root, '关闭')); await flushVue();
  expect(Reflect.get(VirtualNode.prototype, 'close')).toHaveBeenCalledTimes(1);
  await click(button(m.root, '帮助')); await flushVue(); await click(button(m.root, '关于 Doppel-Agent')); await flushVue();
  expect(m.root.textContent).toContain('预发布版');
  expect(m.root.textContent).not.toContain('检查更新');
  await click(button(m.root, '帮助')); await flushVue(); m.props.blocked = true; await flushVue();
  expect(descendants(m.root).some(n => n.props.role === 'menu')).toBe(false);
  expect(button(m.root, '帮助').props.disabled).toBe(true);
  expect(Reflect.get(VirtualNode.prototype, 'close')).toHaveBeenCalledTimes(2);
  m.unmount(); expect(listeners.size).toBe(0);
});

it('opens at the last item with ArrowUp, and blocked opening cannot focus late', async () => {
  const m = fixture(); key(button(m.root, '帮助'), 'ArrowUp'); await flushVue();
  expect(focused?.textContent).toBe('关于 Doppel-Agent');
  key(descendants(m.root).find(n => n.props.role === 'menu')!, 'Escape'); await flushVue();
  focused = undefined; key(button(m.root, '帮助'), 'ArrowDown'); m.props.blocked = true; await flushVue();
  expect(focused).toBeUndefined();
  expect(descendants(m.root).some(n => n.props.role === 'menu')).toBe(false);
});
