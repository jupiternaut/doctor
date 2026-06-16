# Doctor Research Scorecard

Snapshot date: 2026-06-16.

Scores are subjective but grounded in current product shape, public project
positioning, local inspection, and GitHub API snapshots where available. The
goal is not to crown a winner; it is to position Doctor against adjacent
systems.

## Evaluation Axes

- User-perceived power: how strong the product feels to an end user.
- Foundation power: how strong it is as infrastructure for agents, knowledge,
  metadata, execution, permissions, and extensibility.
- Maturity/adoption: how ready and widely usable it appears today.

## Score Table

| Project | User-perceived power | Foundation power | Maturity/adoption | Current stars | Main category |
| --- | ---: | ---: | ---: | ---: | --- |
| Claude Code | 8.8 for developers / 5.5 for general users | 7.8 | 9.0 | closed source | repo-bound coding agent |
| OpenClaw | 8.5 | 8.3 | 7.8 | 378,968 | always-on assistant runtime |
| ChatGPT Projects | 8.3 | 5.8 | 9.0 | closed source | manual project container |
| Claude Projects | 8.2 | 5.7 | 8.5 | closed source | manual project knowledge space |
| Hermes Agent | 8.0 | 8.0 | 7.0 | 194,899 | growing personal agent |
| WorkBuddy | 8.0 | 6.3 | 7.5 | closed source | business AI workspace |
| OpenMetadata | 6.8 | 9.2 | 8.3 | 14,210 | enterprise context/metadata platform |
| DataHub | 6.5 | 9.0 | 8.5 | 12,100 | enterprise metadata graph |
| Doctor current | 5.8 | 8.1 | 3.5 | new project | personal macOS context runtime |
| Doctor target | 8.0-8.6 | 9.0-9.4 | unknown | new project | personal context virtualization layer |

## Ranking By User Perception

```text
Claude Code
> OpenClaw
> ChatGPT Projects / Claude Projects
> Hermes / WorkBuddy
> Doctor target
> OpenMetadata / DataHub
> Doctor current
```

Doctor current scores low here because it lacks a polished, default user
workflow. The current system is powerful once invoked, but the product surface
does not yet feel like one coherent app.

## Ranking By Foundation Power

```text
Doctor target
≈ OpenMetadata
≈ DataHub
> OpenClaw
≈ Doctor current
≈ Hermes
> Claude Code
> WorkBuddy
> ChatGPT Projects / Claude Projects
```

Doctor current already has provider discovery, cold indexes, semantic background
refresh, hot packs, MCP tools, Codex++ integration, access policy, runtime
health, and feedback replay. It is therefore stronger as a foundation than as a
finished product.

## Key Comparisons

### ChatGPT Projects And Claude Projects

These feel strong because users get a clean project container, uploaded files,
project instructions, and conversation continuity. Their weakness is that the
user must manually put context into the project. They are closer to:

```text
project folder + uploaded docs + project instructions + RAG
```

Doctor aims to remove that manual context preparation by discovering local
providers and mounting task-specific hot packs.

### Claude Code

Claude Code is strong inside a code repository. It reads local files, follows
project memory, edits code, and runs commands. Its boundary is the current repo
or coding workspace.

Doctor is not a coding agent. Doctor should supply a broader local context and
execution boundary that coding agents can consume.

### OpenClaw And Hermes

OpenClaw and Hermes are closest to the always-on assistant/runtime direction.
They solve message channels, sessions, memory, tools, skills, and long-running
assistant behavior.

Doctor's complementary role is local context virtualization: what exists on the
machine, where to find evidence, what to mount for this task, and what local
actions are permitted.

### DataHub And OpenMetadata

DataHub and OpenMetadata are the best enterprise analogy. They are not chat
products; they are context and metadata foundations. They track assets, owners,
lineage, semantics, quality, policy, and discovery.

Doctor is the personal macOS equivalent:

```text
DataHub/OpenMetadata for enterprise data assets
Doctor for local files, projects, sessions, workflows, and executions
```

## Product Thesis

Doctor should not describe itself as "local RAG." The stronger category is:

> Personal context virtualization runtime for macOS agents.

The winning product path is:

```text
OpenMetadata/DataHub-style context catalog
+ OpenClaw/Hermes-style assistant runtime integration
+ Claude Code/Codex++/Warp-style execution clients
```
