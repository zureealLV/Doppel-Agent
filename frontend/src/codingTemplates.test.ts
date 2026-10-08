import { describe, expect, it } from "vitest";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";
import CodingTemplates from "./components/CodingTemplates.vue";
import { codingTemplates } from "./codingTemplates";

describe("composer-only coding templates", () => {
  it("contains fixed bounded prompts without changing runtime grants", () => {
    expect(codingTemplates.map(t => t.id)).toEqual(["implement", "repair", "tests", "understand"]);
    for (const template of codingTemplates) {
      expect(template.prompt.length).toBeLessThan(1000);
      expect(template).not.toHaveProperty("permissions");
      expect(template).not.toHaveProperty("run_id");
    }
  });
  it.each([{ blocked: true, hasDraft: false }, { blocked: false, hasDraft: true }])(
    "disables every seed while busy or protecting an existing draft", async props => {
      const html = await renderToString(createSSRApp(CodingTemplates, props));
      expect(html.match(/disabled/g)).toHaveLength(4);
      expect(html).toContain("仅填入提示词");
      expect(html).not.toContain('type="submit"');
    });
});
