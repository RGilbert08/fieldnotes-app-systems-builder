import { useEffect } from "react";

export default function GlobalWorkspaceAccessGate({ onUnauthorizedAccess }) {
  useEffect(() => {
    onUnauthorizedAccess?.();
  }, [onUnauthorizedAccess]);

  return (
    <main className="workspace-security-alert" role="alert">
      <strong>403 Unauthorized Access</strong>
      <span>
        This workspace is restricted. Returning you to your assigned school
        workspace.
      </span>
    </main>
  );
}