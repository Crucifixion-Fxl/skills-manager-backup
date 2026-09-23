# Security and source-removal policy

The local Skill performs only source generation, source updates, validation,
diff/plan preparation, and an explicitly requested source removal.

It must not read, request, retain, or log Grafana credentials; call Grafana;
set an API Grafana URL; create folders; deploy; or change users, data sources,
permissions, contact points, or notification policies. The sole exception is
rendering an output-only public Dashboard URI from the fixed approved template
and stable UID. It never requests or accepts a URL from the user, follows the
link, or includes credentials in it.

Removing a Dashboard is a two-stage operation:

1. The user explicitly requests source removal. The local helper requires
   `--confirm-source-removal` and removes only a managed JSON file.
2. After protected-main merge, the Dashboard repository CI verifies UID,
   management tag, and source-derived folder before it performs any remote
   deletion. It never deletes Grafana folders.

If a Dashboard lacks `managed-by-grafana-skill`, stop rather than overwrite or
remove it.
