# Specs

Feature specifications live here. Human defines the problem, agents figure out the solution.

## Workflow

```
main                              branch (worktree)
────                              ─────────────────
specs/{feature}/requirements.md → agent picks up
                                  agent designs, plans, implements
                                  agent opens PR when done
```

## Writing a Spec

Create a folder with a `requirements.md`:

```bash
mkdir specs/{feature-name}
# write specs/{feature-name}/requirements.md
```

The requirements should define:
- **What** are we building?
- **Why** — what problem does it solve?
- **Success criteria** — how do we know it's done?
- **Out of scope** — what are we NOT doing?
- **Open questions** — things to figure out

See `_template/requirements.md` for a starting point.

## Assigning to an Agent

Point the agent at the spec:

> Implement the spec in `specs/{feature-name}/`

The agent will:
1. Read the requirements
2. Create a worktree: `git worktree add .worktrees/{agent}-{feature} -b {agent}/{feature}`
3. Design and implement (their approach, their structure)
4. Open PR when done

## For Agents

When assigned a spec:

1. Read `specs/{feature}/requirements.md` carefully
2. Create a worktree and branch for your work
3. Design your approach (document it however makes sense)
4. Implement, committing as you go
5. When done, update `HANDOFF.md` and open a PR

You own the solution. The spec defines the problem, not how to solve it.
