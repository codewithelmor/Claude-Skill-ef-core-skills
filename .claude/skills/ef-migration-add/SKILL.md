---
name: ef-migration-add
description: Create a new EF Core migration (dotnet ef migrations add) in any .NET solution layout - single web project, Clean Architecture, CQRS, modular monolith, or several DbContexts. Finds the DbContext and startup project automatically, shows the exact command, and runs it after confirmation. Also fills an empty migration with custom SQL (views, stored procedures, functions, triggers, data fixes). Use whenever the user wants to add or create a migration, capture model changes, or put SQL into a migration, even if they never mention dotnet ef.
argument-hint: "[migration-name]"
---

# Add an EF Core migration

Helper script: `scripts/ef_tool.py` in this skill's folder. It needs Python 3.8+ and nothing else.
Run it with whichever of `python3`, `python` or `py -3` exists on this machine (Windows usually has `python` or `py`).
Use the absolute path to the script. Every call ends with one line `EF_TOOL_RESULT: {json}`; read that line.
Run commands from the repository root.
Arguments passed with the slash command: `$ARGUMENTS` (normally the migration name; if empty, ask).

## Steps

1. **Discover.** `ef_tool.py discover --root <repo root>`
   - `efTool.dotnetInstalled` false: tell the user the .NET SDK is required and stop.
   - `efTool.installed` false: ask, then run `dotnet tool install --global dotnet-ef`.
   - No `dbContexts`: retry with `--deep`; if still none, ask the user for the class name and project.
   - A context with `confirmed: false` was matched by file name only. Say so when you offer it.
2. **Choose the DbContext.** One context: use it. Several: ask the user which one (show name and project).
   Pass `nameArg` (not `name`) to `--context`; it is fully qualified when two contexts share a short name.
3. **Choose the startup project** from `startupProjects`. Ignore `isTestProject: true`. If more than one remains,
   prefer the one whose `.csproj` references the DbContext project (check `ProjectReference` entries, directly or
   through other projects). Ask the user only if it is still ambiguous.
4. **Migration name.** Take it from the user's arguments or ask. Use PascalCase with no spaces and describe the
   change (`AddOrderStatus`).
5. **Output directory.** Only when `otherContextsInSameProject > 0`: suggest `--output-dir Migrations/<ContextName>`
   so each context keeps its own migrations. Otherwise omit it.
6. **Dry run.** Run `ef_tool.py ef add --name <N> --context <C> --project <ctx csproj> --startup-project <startup csproj> [--output-dir <D>]`
   without `--execute`. Show the printed command to the user and ask for confirmation.
7. **Execute.** After the user confirms, run the same command with `--execute`.
   The first run can be slow because `dotnet ef` builds the project. If it fails, read the error and open
   `references/troubleshooting.md` at the matching section.
8. **Check the result.** The result has `migrationFile` and `upIsEmpty`.
   - `upIsEmpty` false: done. Mention the file path.
   - `upIsEmpty` true: EF found no model changes. Ask whether the user wanted an empty migration for custom SQL.

## Custom SQL in an empty migration

If the user wants SQL (view, stored procedure, function, trigger, data fix):
1. Get the **UP** SQL: pasted in chat, or an existing `.sql` file in the repo (pass its path directly).
   Also ask for the **DOWN** (rollback) SQL, for example `DROP VIEW ...`. Without it the migration cannot be rolled back.
2. Save pasted SQL to temporary `.sql` files **outside the repo** (the OS temp folder). Do not leave them behind.
3. Run `ef_tool.py add-sql --migration-file <migrationFile> --up-sql-file <up.sql> [--down-sql-file <down.sql>]`.
   Lines containing only `GO` split the script into separate `migrationBuilder.Sql()` calls, which
   `CREATE VIEW` and `CREATE PROCEDURE` need. The script refuses to touch a migration whose `Up()` already has code.
4. Delete the temporary files and tell the user the migration file path.

If the user did not intend an empty migration, explain that no model change was detected (is the entity or
configuration change saved and does the project build?). They can keep it, or remove it with `/ef-migration-remove`.

## Rules
- Never print or ask for passwords. Do not echo connection strings.
- This skill never applies migrations to a database. Point the user to `/ef-database-update` for that.
- Do not run `--execute` before the user has seen the command and confirmed it.
