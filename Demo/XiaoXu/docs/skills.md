# Skills

Skills 使用开放的 `SKILL.md` 目录格式。启动时仅扫描 frontmatter 的
`name` 与 `description`；匹配目标后才读取正文，引用资源按需读取。
Skill 的脚本不能绕过 XiaoXu 工具权限。

渐进加载分三级：

1. 启动扫描仅读取有上限的 YAML frontmatter。
2. `activate_skill` 读取有上限的 `SKILL.md` 正文。
3. `read_skill_resource` 按需读取 `references/` 或 `assets/` 下的单个
   UTF-8 文本资源。

资源路径拒绝绝对路径、`..` 和符号链接越界；frontmatter、正文和单个资源
分别由 `skill_max_frontmatter_bytes`、`skill_max_instructions_bytes`、
`skill_max_resource_bytes` 限制。
