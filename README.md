# Fieldnotes App / Systems Builder

**Bundle type:** uploadable source handoff kit. **This is not yet a runnable standalone application.**

## Included

- The current builder UI and its imported local components, styles, theme data, and utilities.
- Relevant current Fieldnotes API route source and imported backend source dependencies for reference.
- Selected existing tests and schema migration source where available.
- A file manifest, API/schema map, and independent-backend port checklist.

## Independent backend and data

This kit includes **no database records, database URLs, Replit Secrets, `.env` files, or production/staging uploads**. The copied backend modules still reference shared Fieldnotes authentication, tenant context, and central model definitions. They are included as migration context, not as a direction to connect to or initialize the original database. Do not run copied migrations against any existing Fieldnotes database.

To make this an independent app, the new project still needs its own backend entry point and API, dedicated authentication/session handling, a project-specific database schema, project-owned secrets, tests, and any separate media/domain services. Port the required functionality selectively instead of launching the copied modules unchanged.

## Upload/use

1. Create or open the new Replit project and upload/extract this ZIP.
2. Ask Agent to continue the independent port using `PORTING_GUIDE.md` and `reference/MANIFEST.json`.
3. Provision a **new** database and new project-specific credentials using Replit Secrets. Never copy credentials from the Fieldnotes project.
4. Do not publish until the porting checklist and tests pass.

## Exclusions

No live records, uploads, website content, session data, environment secrets, deployment credentials, `node_modules`, compiled output, or generated build artifacts are included.
