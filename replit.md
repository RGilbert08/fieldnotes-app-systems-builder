# Replit project context

This project begins as a source handoff for Fieldnotes App / Systems Builder, not as a working standalone app. Keep its backend and database independent from the Fieldnotes core project. Never copy production/staging data or secrets. Before implementing, read `README.md` and `PORTING_GUIDE.md`; replace shared Fieldnotes auth, tenant, API, and model dependencies with project-owned boundaries. Do not run copied migrations against Fieldnotes databases.
