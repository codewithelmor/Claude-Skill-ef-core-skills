---
name: ef-migration-remove
description: Remove the most recent EF Core migration (dotnet ef migrations remove). Checks whether that migration is already applied to the database and warns before using --force. Shows the exact command and runs it only after explicit confirmation. Invoke manually with /ef-migration-remove.
disable-model-invocation: true
---

# Remove the last EF Core migration

Helper script: `scripts/ef_tool.py` in this skill's folder. It needs Python 3.8+ and nothing else.
Run it with whichever of `python3`, `python` or `py -3` exists on this machine. Use the absolute path to the script.
Every call ends with one line `EF_TOOL_RESULT: {json}`; read that line. Run from the repository root.

`dotnet ef migrations remove` always removes only the **last** migration, deletes its files and reverts the
model snapshot. Without `--force` it does not touch the database and refuses if the migration is applied.
With `--force` it connects to the database and, if the migration is applied, **reverts it (runs its `Down()`)
first**, then removes the files. `Down()` can drop tables or columns, so this can destroy data.

## Steps

1. **Discover.** `ef_tool.py discover --root <repo root>`
   - `dotnetInstalled` false: the .NET SDK is required; stop and tell the user.
   - `efTool.installed` false: ask, then run `dotnet tool install --global dotnet-ef`.
2. **Choose the DbContext** (ask if several; pass `nameArg` to `--context`) and the **startup project**
   (ignore `isTestProject: true`; prefer the one that references the DbContext project; ask if still ambiguous).
3. **List migrations.** `ef_tool.py migrations --context <C> --project <ctx csproj> --startup-project <startup csproj>`
   If there are none, say there is nothing to remove and stop. Show the list and name the last one
   (`latest`) as the one that will be removed.
   If the command fails because the database is unreachable, you cannot tell whether it was applied: ask the user.
4. **Check if it was applied.**
   - Last migration is `pending`: safe to remove. Go to step 5 without `--force`.
   - Last migration is `applied`: plain `remove` will fail. Offer the two routes and let the user choose:
     1. **Explicit (recommended for anything but a throwaway database):** run `/ef-database-update` with the
        previous migration as target, so the user sees the revert, then remove without `--force`.
        If the migration is the only one, the target is `0`.
     2. **One step:** `--force`. It reverts the migration in the database (its `Down()` runs) and removes the files.
        Tell the user which database it will touch (see step 5 of `/ef-database-update` for how to find out),
        and that an empty or destructive `Down()` means a failed or lossy revert. Use it only after the user
        explicitly picks it.
5. **Dry run.** `ef_tool.py ef remove --context <C> --project <P> --startup-project <S> [--force]`
   without `--execute`. Show the command and any `WARNING` lines. Ask for explicit confirmation.
6. **Execute** with the same command plus `--execute`. Afterwards check the working tree
   (`git status`, if the repo uses git) and confirm the migration files are gone and the model snapshot changed.
   To remove several migrations, repeat the whole procedure; each run removes one.

## Rules
- Never run `--execute` before the user has seen the command and confirmed it in this conversation.
- Never add `--force` on your own. It reverts the migration in the database and needs the user's explicit decision.
- Never print or ask for passwords or full connection strings.
- On errors, open `references/troubleshooting.md` at the matching section.
