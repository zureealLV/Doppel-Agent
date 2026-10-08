import { createSSRApp } from 'vue';
import { renderToString } from 'vue/server-renderer';
import { expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import ChangesWorkspace from './components/ChangesWorkspace.vue';
import ExtensionCenter from './components/ExtensionCenter.vue';
import WorkOrdersWorkspace from './components/WorkOrdersWorkspace.vue';
import WorkbenchShell from './components/WorkbenchShell.vue';

it('exposes accessible Help in the shared titlebar, without invented product features', async () => {
  const html = await renderToString(createSSRApp(WorkbenchShell, {
    page: 'conversations', closing: false, project: null, projectError: false,
  }));
  expect(html).toContain('aria-label="帮助"');
  expect(html).toContain('aria-haspopup="menu"');
  expect(html).toContain('aria-expanded="false"');
  expect(html).not.toContain('检查更新');
});

it('folds ordinary page explanation while retaining its authorization meaning', async () => {
  for (const component of [ChangesWorkspace, ExtensionCenter, WorkOrdersWorkspace]) {
    const html = await renderToString(createSSRApp(component, { active: false, blocked: false, profiles: [], sourceRunId: '' }));
    expect(html).toContain('class="page-explanation"');
    expect(html).toMatch(/<details class="page-explanation"[^>]*>/);
    expect(html).not.toMatch(/<details class="page-explanation"[^>]*\bopen\b/);
  }
});

it('ships a synchronized prerelease version in UI, Python metadata and lockfiles', () => {
  const read = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8');
  const jsVersion = JSON.parse(read('../package.json')).version as string;
  expect(jsVersion).toBe('0.15.0-rc.1');
  expect(JSON.parse(read('../package-lock.json')).version).toBe(jsVersion);
  expect(JSON.parse(read('../package-lock.json')).packages[''].version).toBe(jsVersion);
  const pythonVersion = jsVersion.replace('-rc.', 'rc');
  expect(read('../../pyproject.toml')).toContain(`version = "${pythonVersion}"`);
  expect(read('../../src/doppel_agent/__init__.py')).toContain(`__version__ = "${pythonVersion}"`);
  expect(read('../../uv.lock')).toMatch(new RegExp(`name = "doppel-agent"\\r?\\nversion = "${pythonVersion.replaceAll('.', '\\.')}"`));
});
