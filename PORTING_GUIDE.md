# Independent port guide — Fieldnotes App / Systems Builder

## Status

This is a source snapshot, not a completed split. The feature is integrated into the Fieldnotes monolith. The export preserves code needed to continue the port but intentionally does not claim standalone runtime support.

## Current source

- UI entry: `source/frontend/src/views/NewCanvasProjectStudio.jsx`
- Backend reference modules: `backend.routes.admin, backend.routes.low_code_engine, backend.routes.elibrary` plus imports recorded in `reference/MANIFEST.json`
- Current API calls: Canvas load/save, studio project create/update/list/export, low-code action execution, and library canvas-binding execution.
- Current model/schema references: TenantCanvasBlueprint, LowCodeUserProjectSnapshot, workspace-folder records, user/session records, and academic/library records used by current action execution. Reference only; design an independent schema.

## Required isolation work

1. Create a new app entry point and wire only this builder UI. Remove imports into Canopy, Compass, Pulse, the Fieldnotes project hub, and the current master-admin session.
2. Define a project-owned account/authorization model. Do not accept Fieldnotes cookies or trust client-provided roles or tenant IDs.
3. Extract only the required builder records into a new project-specific schema. Do not run `Base.metadata.create_all()` over the copied `backend/models.py`, which contains unrelated Fieldnotes schemas.
4. Port the listed endpoints into a new FastAPI service and replace shared tenant/auth helpers with the new project boundary.
5. Provision a new database, new authentication/session secrets, and any required media/object storage through the new project. Secret values are not included.
6. Add account-isolation tests covering reads, writes, assets, domains, and exports.
7. Run the UI build, backend tests, schema checks, and clean upload/restore tests before deployment.

## Current coupling

The canvas editor action paths call `/api/low-code/actions/execute` and `/api/library/canvas-binding/execute`, currently coupled to Fieldnotes academic/library features. The independent port must separate editor CRUD/export from these core-app actions or reimplement them against its own domain model. The UI also uses the global workspace access gate and stored studio session context.

## Existing tests

Copied tests are behavioral references. They may assume the original `api.index:app`, Fieldnotes fixtures, test database, and full-project dependencies. Adapt them to the independent service; a copied test is not proof that the new project is standalone.
