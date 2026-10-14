# EF Core troubleshooting

Read the section that matches the error. Do not read this whole file unless needed.

## Contents
- dotnet-ef is missing
- "Unable to create a 'DbContext'" / DI or configuration errors at design time
- Windows: "Application Control policy has blocked this file" (0x800711C7)
- Scaffold fails
- Several DbContexts / "More than one DbContext was found"
- `migrations list` or `database update` cannot connect

## dotnet-ef is missing
`dotnet ef --version` fails. Install the global tool (ask the user first):

    dotnet tool install --global dotnet-ef

If it says the tool is already installed, run `dotnet tool update --global dotnet-ef`.
A repo may instead use a local tool manifest (`.config/dotnet-tools.json`); then
`dotnet tool restore` installs it. If `dotnet` itself is missing, the .NET SDK must be installed first.

## "Unable to create a 'DbContext'" / design-time errors
EF boots the **startup project's** host to build `DbContextOptions`. It fails when the host
needs configuration or services that are not available at design time (missing connection
string, secrets, Azure resources, etc.). Checks:
1. Is the right `--startup-project` selected (a Web/Exe project that registers the DbContext)?
2. Is the connection string available to that project (appsettings, user-secrets, environment variables)?
3. Longer-term fix: add an `IDesignTimeDbContextFactory<TContext>` to the project that contains the
   DbContext. EF then builds the options directly and skips the app host.

```csharp
public class ApplicationDbContextFactory : IDesignTimeDbContextFactory<ApplicationDbContext>
{
    public ApplicationDbContext CreateDbContext(string[] args)
    {
        var config = new ConfigurationBuilder()
            .SetBasePath(Directory.GetCurrentDirectory())
            .AddJsonFile("appsettings.json", optional: true)
            .AddJsonFile("appsettings.Development.json", optional: true)
            .Build();

        var connectionString = config.GetConnectionString("DefaultConnection");

        var optionsBuilder = new DbContextOptionsBuilder<ApplicationDbContext>();
        optionsBuilder.UseSqlServer(connectionString); // or the provider in use

        return new ApplicationDbContext(optionsBuilder.Options);
    }
}
```

## Windows: "Application Control policy has blocked this file" (0x800711C7)
Windows only. The build succeeds, then EF fails while loading the freshly built
`bin\Debug\netX.0\<Project>.dll`. `0x800711C7` is the Code Integrity error
(`ERROR_DENIED_BY_POLICY_ENGINE`). Typical causes:
- **Windows Smart App Control** (Windows Security > App & browser control) on personal machines. Check this first.
- An enterprise **WDAC** policy on managed machines. Only an IT admin can grant an exception.
- Third-party endpoint security flagging a new unsigned DLL (it usually shows its own notification).

Steps: (1) check Smart App Control; (2) check the antivirus block/quarantine log; (3) consider excluding the
repo folder from the antivirus; (4) Event Viewer > Applications and Services Logs > Microsoft > Windows >
CodeIntegrity > Operational (event IDs 3076/3077) names the blocked file and the rule; (5) use an
`IDesignTimeDbContextFactory` (see above) to avoid loading the whole app host.

## Scaffold fails
Most common causes:
- The target project or startup project does not reference `Microsoft.EntityFrameworkCore.Design`
  and the provider package (e.g. `Microsoft.EntityFrameworkCore.SqlServer`).
  Add with: `dotnet add <project.csproj> package Microsoft.EntityFrameworkCore.Design` (ask first).
- Wrong connection string, or the database is not reachable from this machine.
- The project does not build. Run `dotnet build <project.csproj>` and fix the errors first.

## Several DbContexts / "More than one DbContext was found"
Always pass `--context`. If two contexts have the same class name in different namespaces, use the
fully-qualified name (`discover` returns it as `nameArg`). When several contexts share one project,
give each its own `--output-dir` (for example `Migrations/<ContextName>`).

## `migrations list` or `database update` cannot connect
`migrations list` also reads the database to mark applied migrations. If the database is unreachable
it fails. Check the connection string the startup project resolves (environment name, user-secrets,
environment variables such as `ConnectionStrings__DefaultConnection`) and that the server is running.
