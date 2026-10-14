---
name: ef-database-update
description: Apply EF Core migrations to a database (dotnet ef database update), or roll back to an earlier migration or to zero. Lists applied and pending migrations, lets the user choose the target, shows the exact command, and runs it only after explicit confirmation. Invoke manually with /ef-database-update.
disable-model-invocation: true
argument-hint: "[target-migration | 0]"
---

# Update the database (apply or roll back migrations)

Helper script: `scripts/ef_tool.py` in this skill's folder. It needs Python 3.8+ and nothing else.
Run it with whichever of `python3`, `python` or `py -3` exists on this machine. Use the absolute path to the script.
Every call ends with one line `EF_TOOL_RESULT: {json}`; read that line. Run from the repository root.
Arguments passed with the slash command: `$ARGUMENTS` (normally a target migration, or `0`; if empty, use latest after asking).

This changes a real database. Be slow and explicit about what will happen and where.

## Steps

1. **Discover.** `ef_tool.py discover --root <repo root>`
   - `dotnetInstalled` false: the .NET SDK is required; stop and tell the user.
   - `efTool.installed` false: ask, then run `dotnet tool install --global dotnet-ef`.
2. **Choose the DbContext** (ask if several; pass `nameArg` to `--context`) and the **startup project**
   (ignore `isTestProject: true`; prefer the one that references the DbContext project; ask if still ambiguous).
3. **List migrations.** `ef_tool.py migrations --context <C> --project <ctx csproj> --startup-project <startup csproj>`
   Show them numbered with `(applied)` / `(pending)`. If the command fails (usually the database is not reachable),
   read `references/troubleshooting.md` and tell the user; you can still continue without a list if they insist.
4. **Choose the target.**
   - Default: latest (omit `--target`).
   - A specific migration: `--target <id>`. If it is not the latest, later migrations will be rolled back.
   - Revert everything: `--target 0`. Warn clearly that this drops the schema changes of every migration for this
     context and that data in those tables or columns can be lost.
   Rolling back runs each migration's `Down()`. Mention that `Down()` can be empty or destructive.
5. **Confirm which database.** The target comes from the startup project's configuration (appsettings files,
   user-secrets, environment variables such as `ConnectionStrings__DefaultConnection`, `ASPNETCORE_ENVIRONMENT`).
   If you can tell which server and database it will use, say so (never print passwords) and ask the user to
   confirm it is the intended one, especially when the host is not local.
6. **Dry run.** `ef_tool.py ef update --context <C> --project <P> --startup-project <S> [--target <T>]`
   without `--execute`. Show the command and any `WARNING` lines. Ask for explicit confirmation.
   For `--target 0` or a rollback, ask for a separate, clear "yes".
7. **Execute** with the same command plus `--execute`. Report success or the error. For errors, open
   `references/troubleshooting.md` at the matching section.

## Rules
- Never run `--execute` before the user has seen the command and confirmed it in this conversation.
- Never print or ask for passwords or full connection strings.
- For production databases, suggest generating a script instead (`dotnet ef migrations script --idempotent`)
  so it can be reviewed and applied through the team's normal deployment process.
