# Team roles and skills

Each role owns a clear set of outputs, starts by discovering the skills that help it, and works with the cheapest model that can do the job (see `product/06-delivery/agent-cost-playbook.md`).

## Skill discovery (every role, before the first task)
1. List what is already enabled (`ListSkills`, filtered by role keywords) and read the matching `SKILL.md` with the `Skill` tool.
2. If a gap remains, `SearchSkills` / `SearchPlugins` with 3-6 keywords from the task. Use a result only if it fits; otherwise continue without it and do not mention the search.
3. Never install or enable anything that needs credentials, and never run a downloaded script that was not read first.
4. Record the skills you used in the PR description (one line).

## Roles

| Role | Owns | Model tier | Skills to load first (if present) |
|---|---|---|---|
| Product owner | Scope, priorities, acceptance criteria, release gate | Strong, rarely | `design:research-synthesis`, `deep-research` |
| Project manager | Sprint plan, risks, status, cost tracking | Mid | `productivity:task-management`, `engineering:standup` |
| Tech lead | ADRs, interfaces, review of risky PRs | Strong | `engineering:architecture`, `engineering:system-design`, `engineering:code-review`, `engineering:tech-debt` |
| Backend engineer | Jobs, storage, gateway, resume, CLI | Mid | `engineering:debug`, `engineering:testing-strategy`, `engineering:documentation` |
| AI engineer (translation) | Prompts, glossary use, QA rules, eval set | Mid | `engineering:testing-strategy`, `deep-research` for provider/model research |
| AI engineer (layout and labels) | PDF rendering, fonts, overlay, label merge | Mid | `pdf`, `pdf-reading` |
| Frontend engineer | Review app, accessibility, copy | Mid | `frontend-design`, `design:accessibility-review`, `design:ux-copy`, `design:design-handoff` |
| QA | Test plan, protected-code suite, release gate, terminology review support | Mid | `engineering:testing-strategy`, `engineering:code-review` |

## Rules that apply to everyone
- Follow `docs/AGENT-CONTEXT.md`. Small PRs, one task each.
- A reviewer is never the author. Review the diff only.
- Skills guide the work; they never override the rules in `AGENT-CONTEXT.md` (protected codes, no customer data outside the machine, no paid calls outside the gateway).
