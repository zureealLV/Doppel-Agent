/** Static composer seeds only: no provider calls, commands, or permission grants. */
export const codingTemplates = [
  { id: "implement", title: "实现功能", prompt: "目标功能：\n\n先阅读项目结构、现有实现与约束，再给出最小改动计划、涉及文件和验收标准。不要猜测未读取的代码；写入和命令仍需明确授权。" },
  { id: "repair", title: "修复问题", prompt: "问题现象与复现步骤：\n\n先定位根因和证据，区分已确认与待验证假设；提出最小修复和回归测试。不要在没有证据时批量重写，也不要未经授权执行命令。" },
  { id: "tests", title: "补充测试", prompt: "需要保护的行为：\n\n阅读现有测试框架和相关实现，列出边界、失败路径与必要测试，避免只测试 Mock 本身。编写与执行是不同阶段；未执行的测试不得报告通过。" },
  { id: "understand", title: "理解项目", prompt: "阅读项目入口和相关模块，说明架构、数据流、运行方式及关键约束，引用实际文件。先只读分析，不修改文件、不执行命令、不把推测当事实。" },
] as const;
