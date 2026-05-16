# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Layout

This is a single-context repo. Skills should treat the repository root as the single domain boundary.

Expected locations:

- `CONTEXT.md` at the repo root for project vocabulary, domain concepts, and terminology.
- `docs/adr/` for architectural decision records that apply to the whole project.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root, if it exists.
- **`docs/adr/`** for ADRs that touch the area about to be changed, if the directory exists.

If any of these files do not exist, proceed silently. Do not flag their absence and do not suggest creating them upfront. The producer skill (`/grill-with-docs`) creates them lazily when terms or decisions actually get resolved.

## File structure

```text
/
├── CONTEXT.md
├── docs/adr/
│   ├── 0001-example-decision.md
│   └── 0002-example-decision.md
└── app/
```

## Use the glossary's vocabulary

When output names a domain concept in an issue title, refactor proposal, hypothesis, or test name, use the term as defined in `CONTEXT.md`. Do not drift to synonyms the glossary explicitly avoids.

If the concept needed is not in the glossary yet, that is a signal: either the skill is inventing language the project does not use, or there is a real documentation gap to note for `/grill-with-docs`.

## Flag ADR conflicts

If output contradicts an existing ADR, surface it explicitly rather than silently overriding it.
