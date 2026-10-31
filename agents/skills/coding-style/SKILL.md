---
name: coding-style
description: Add purposeful detailed comments to newly created code and update the relevant project Markdown documentation under /doc when implementation changes affect behavior, interfaces, structure, or operations.
---

# Coding Style

Apply this skill when creating or materially changing project code.

## Requirements

1. Add detailed comments to code you create or substantially rewrite.
   Comments should explain intent, important assumptions, data flow,
   invariants, side effects, failure handling, and non-obvious trade-offs.
   Document public functions, worker entry points, configuration boundaries,
   and security or path-confinement checks when their purpose is not obvious
   from the code alone.
2. Keep comments accurate and close to the logic they explain. Update or
   remove comments when the implementation changes. Do not add repetitive
   comments that merely restate a variable name or each individual line.
3. Before editing, inspect the relevant Markdown files under `/doc` so the
   documentation update matches the project's existing terminology and
   structure.
4. After changing behavior, interfaces, worker names, configuration,
   architecture, setup, or operational procedures, update every affected
   `/doc/*.md` file in the same change. Keep examples, paths, commands, and
   public names synchronized with the implementation.
5. If a code change has no meaningful documentation impact, verify that
   conclusion against the `/doc` files and mention it in the handoff rather
   than adding unrelated documentation.

## Completion check

Before handing off a coding task, verify that:

- newly created or substantially changed code contains useful explanatory
  comments;
- comments describe current behavior rather than planned behavior;
- affected `/doc` Markdown reflects the implementation;
- appropriate tests, linters, type checks, or syntax checks pass;
- the final summary names the code and documentation files changed.
