---
name: ef-migration-remove
description: Remove the most recent EF Core migration (dotnet ef migrations remove). Checks whether that migration is already applied to the database and warns before using --force. Shows the exact command and runs it only after explicit confirmation. Invoke manually with /ef-migration-remove.
disable-model-invocation: true
---

# Remove the last EF Core migration

Helper script: `scripts/ef_tool.py` in this skill's folder. It needs Python 3.8+ and nothing else.
Run it with whichever of `python3`, `python` or `py -3` exists on this machine. Use the absolute path to the script.
Every call ends with one line `EF_TOOL_RESULT: {json}`; read that line. Run from the repository root.

`dotnet ef migrations remove` always removes only the **last** migration and reverts the model snapshot.
It deletes the migration files. It does **not** change the database.

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
   - Last migration is `pending`: safe to remove. Go to step 5.
   - Last migration is `applied`: removing the files leaves the database with a migration the code no longer knows.
     Recommend the safe order: first run `/ef-database-update` with the previous migration as target (this reverts
     the database), then remove. Use `--force` only if the user explicitly says the database state is handled
     (a throwaway local database, or already reverted by hand). `--force` does NOT revert the database.
5. **Dry run.** `ef_tool.py ef remove --context <C> --project <P> --startup-project <S> [--force]`
   without `--execute`. Show the command and any `WARNING` lines. Ask for explicit confirmation.
6. **Execute** with the same command plus `--execute`. Afterwards check the working tree
   (`git status`, if the repo uses git) and confirm the migration files are gone and the model snapshot changed.
   To remove several migrations, repeat the whole procedure; each run removes one.

## Rules
- Never run `--execute` before the user has seen the command and confirmed it in this conversation.
- Never add `--force` on your own. It needs the user's explicit decision.
- On errors, open `references/troubleshooting.md` at the matching section.
