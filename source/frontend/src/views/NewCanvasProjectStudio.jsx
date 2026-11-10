import { useEffect, useState } from "react";
import { ActiveCanvasMeshProfile, CanvasSubTabArea } from "../components/DynamicMeshContainer";
import {
  CANVAS_STUDIO_SLOT_PREFIX,
  CANVAS_STUDIO_VIEWPORT,
} from "../components/canvasStudioConfig";
import StudioThemeMatrix from "../components/StudioThemeMatrix";
import GlobalWorkspaceAccessGate from "../components/GlobalWorkspaceAccessGate";
import { canAccessGlobalWorkspace } from "../security/globalWorkspaceAccess";
import { downloadStandaloneBundle } from "../utils/standaloneExport";
import {
  DEFAULT_STUDIO_THEME_ID,
  applyStudioThemeToCanvasLayout,
  getStudioTheme,
} from "../utils/studioThemes";
import "./NewCanvasProjectStudio.css";
import "./NewCanvasProjectStudioThemes.css";

const VISIBILITY = {
  master_admin: true,
  principal: true,
  grade_hod: true,
  educator: true,
  parent: true,
  scholar: true,
};

export const CANVAS_STUDIO_TEMPLATES = [
  {
    id: "balanced-dual-column",
    layoutLabel: "Showcase",
    title: "Balanced Dual-Column Split Layout",
    description: "Two equal work areas for personnel and scholar information.",
    diagram: "balanced",
    gridColumns: 2,
    gridRows: 1,
    gap: 20,
    slots: [
      { gridColumn: 1, gridColumnSpan: 1, gridRow: 1 },
      { gridColumn: 2, gridColumnSpan: 1, gridRow: 1 },
    ],
  },
  {
    id: "three-rail-command",
    layoutLabel: "Hub",
    title: "Three-Rail Executive Command Deck",
    description: "A 20% navigation rail, 60% workspace, and 20% telemetry rail.",
    diagram: "rails",
    gridColumns: 10,
    gridRows: 1,
    gap: 16,
    slots: [
      { gridColumn: 1, gridColumnSpan: 2, gridRow: 1 },
      { gridColumn: 3, gridColumnSpan: 6, gridRow: 1 },
      { gridColumn: 9, gridColumnSpan: 2, gridRow: 1 },
    ],
  },
  {
    id: "asymmetric-analytics",
    layoutLabel: "Grid",
    title: "Asymmetric Analytical Dashboard",
    description: "A full-width media or chart header above three equal data slots.",
    diagram: "asymmetric",
    gridColumns: 12,
    gridRows: 2,
    gap: 16,
    slots: [
      { gridColumn: 1, gridColumnSpan: 12, gridRow: 1, gridRowSpan: 1 },
      { gridColumn: 1, gridColumnSpan: 4, gridRow: 2, gridRowSpan: 1 },
      { gridColumn: 5, gridColumnSpan: 4, gridRow: 2, gridRowSpan: 1 },
      { gridColumn: 9, gridColumnSpan: 4, gridRow: 2, gridRowSpan: 1 },
    ],
  },
];

function normalizeExistingTabs(layout) {
  const sharedSettings = {
    layoutMode: layout?.layoutMode === "absolute" ? "absolute" : "grid",
    gridColumns: layout?.gridColumns ?? 12,
    gridRows: layout?.gridRows ?? 1,
    gap: layout?.gap ?? 32,
    canvasHeight: layout?.canvasHeight ?? 480,
  };
  const tabs = Array.isArray(layout?.subTabs)
    ? layout.subTabs.map((tab, index) => ({
        ...tab,
        displayOrderWeight: index,
        layoutMode: tab.layoutMode || sharedSettings.layoutMode,
        gridColumns: tab.gridColumns ?? sharedSettings.gridColumns,
        gridRows: tab.gridRows ?? sharedSettings.gridRows,
        gap: tab.gap ?? sharedSettings.gap,
        canvasHeight: tab.canvasHeight ?? sharedSettings.canvasHeight,
        canvasLayout: Array.isArray(tab.canvasLayout) ? tab.canvasLayout : [],
      }))
    : [];

  if (!tabs.length && Array.isArray(layout?.canvasLayout) && layout.canvasLayout.length) {
    tabs.push({
      tabId: "studio-existing-layout",
      tabLabelDisplayName: "Existing Canvas Project",
      displayOrderWeight: 0,
      status: "active",
      ...sharedSettings,
      canvasLayout: layout.canvasLayout,
    });
  }
  return tabs;
}

function createDropSlot(tabId, template, slot, index, themeId = null) {
  const theme = getStudioTheme(themeId);
  return {
    id: `studio-slot-${tabId}-${index + 1}`,
    type: "text_observation_block",
    label: "➕ Drop Component Box Here",
    groupId: `${CANVAS_STUDIO_SLOT_PREFIX}${tabId}-${index + 1}`,
    content: "",
    visibility: VISIBILITY,
    gridColumn: slot.gridColumn,
    gridRow: slot.gridRow,
    gridOrderIndex: index,
    gridColumnSpan: slot.gridColumnSpan,
    gridRowSpan: slot.gridRowSpan || 1,
    styles: {
      padding: 18,
      margin: 0,
      fontSize: 13,
      fontWeight: 700,
      textAlign: "center",
      color: theme?.tokens.text || "#72513D",
      backgroundColor: theme?.tokens.surface || "#F8F5EF",
      borderColor: theme?.tokens.border || "#D8D0C5",
      fontFamily: theme?.fontKey || "system",
      borderRadius: 12,
    },
  };
}

export function buildCanvasStudioBlueprint(
  existingBlueprint,
  template,
  idSeed = Date.now(),
  themeId = existingBlueprint?.layout?.activeThemeId || null,
) {
  const existingLayout = existingBlueprint?.layout || {};
  const subTabs = normalizeExistingTabs(existingLayout);
  if (subTabs.length >= 16) {
    throw new Error("This studio has reached its 16-project limit.");
  }
  const projectNumber =
    subTabs.filter((tab) => tab.tabLabelDisplayName.startsWith(template.title)).length + 1;
  const tabLabelDisplayName =
    projectNumber === 1 ? template.title : `${template.title} ${projectNumber}`;
  const tabId = `studio-${idSeed}-${Math.random().toString(36).slice(2, 8)}`;
  const canvasLayout = template.slots.map((slot, index) =>
    createDropSlot(tabId, template, slot, index, themeId),
  );
  const projectTab = {
    tabId,
    tabLabelDisplayName,
    displayOrderWeight: subTabs.length,
    status: "active",
    layoutMode: "grid",
    gridColumns: template.gridColumns,
    gridRows: template.gridRows,
    gap: template.gap,
    canvasHeight: 480,
    canvasLayout,
  };
  const nextTabs = [...subTabs, projectTab];

  return {
    editingTargetView: CANVAS_STUDIO_VIEWPORT,
    projectTab,
    layout: {
      schemaVersion: 1,
      activeThemeId: themeId,
      layoutMode: projectTab.layoutMode,
      gridColumns: projectTab.gridColumns,
      gridRows: projectTab.gridRows,
      gap: projectTab.gap,
      canvasHeight: projectTab.canvasHeight,
      canvasLayout,
      nativeProfileElements: Array.isArray(existingLayout.nativeProfileElements)
        ? existingLayout.nativeProfileElements
        : [],
      subTabs: nextTabs,
      activeSubTabId: projectTab.tabId,
    },
  };
}

function getActiveProjectTab(layout) {
  const tabs = normalizeExistingTabs(layout);
  return tabs.find((tab) => tab.tabId === layout?.activeSubTabId) || tabs.at(-1) || null;
}

function inferBaseLayoutType(layout) {
  const activeTab = getActiveProjectTab(layout);
  if (
    activeTab?.gridColumns === 2 &&
    activeTab?.gridRows === 1 &&
    activeTab?.canvasLayout?.length === 2
  ) {
    return "balanced-dual-column";
  }
  if (
    activeTab?.gridColumns === 10 &&
    activeTab?.canvasLayout?.length === 3
  ) {
    return "three-rail-command";
  }
  if (
    activeTab?.gridColumns === 12 &&
    activeTab?.gridRows === 2 &&
    activeTab?.canvasLayout?.length === 4
  ) {
    return "asymmetric-analytics";
  }
  return "custom";
}

export function buildUserProjectSnapshot(
  projectTitle,
  selectedBaseLayoutType,
  blueprint,
) {
  const layout = blueprint?.layout;
  if (!layout || typeof layout !== "object" || Array.isArray(layout)) {
    throw new Error("Create or load a canvas layout before saving a project.");
  }
  const tabs = Array.isArray(layout.subTabs) ? layout.subTabs : [];
  const componentRows = tabs.length
    ? tabs.flatMap((tab) =>
        (Array.isArray(tab.canvasLayout) ? tab.canvasLayout : []).map((component) => ({
          tabId: tab.tabId,
          component,
        })),
      )
    : (Array.isArray(layout.canvasLayout) ? layout.canvasLayout : []).map(
        (component) => ({ tabId: null, component }),
      );
  const uniqueValues = (key) =>
    [...new Set(
      componentRows
        .map(({ component }) => component?.[key])
        .filter((value) => typeof value === "string" && value.trim()),
    )].sort();

  return {
    projectTitle: String(projectTitle || "").trim(),
    selectedBaseLayoutType,
    canvasBlueprintData: {
      layout,
      designVariables: componentRows.map(({ tabId, component }) => ({
        tabId,
        componentId: component.id,
        dataBinding: component.dataBinding || "",
        variableBinding: component.variableBinding || "",
        functionBinding: component.functionBinding || "",
        actionTrigger: component.actionTrigger || "",
      })),
      coordinateMatrix: componentRows.map(({ tabId, component }) => ({
        tabId,
        componentId: component.id,
        gridColumn: component.gridColumn ?? null,
        gridRow: component.gridRow ?? null,
        gridOrderIndex: component.gridOrderIndex ?? null,
        gridColumnSpan: component.gridColumnSpan ?? 1,
        gridRowSpan: component.gridRowSpan ?? 1,
        x: component.x ?? 0,
        y: component.y ?? 0,
        width: component.width ?? 50,
        height: component.height ?? 24,
      })),
      colorWheelValues: componentRows.map(({ tabId, component }) => ({
        tabId,
        componentId: component.id,
        foreground: component.styles?.color || "#302B27",
        background: component.styles?.backgroundColor || "#FFFFFF",
      })),
      allowlistTokens: {
        componentTypes: uniqueValues("type"),
        actionBindings: uniqueValues("functionBinding"),
        variableBindings: uniqueValues("variableBinding"),
        actionTriggers: uniqueValues("actionTrigger"),
        visibilityRoles: [
          ...new Set(
            componentRows.flatMap(({ component }) =>
              Object.keys(component.visibility || {}),
            ),
          ),
        ].sort(),
      },
    },
  };
}

async function fetchStudioBlueprint() {
  const response = await fetch(
    `/api/admin/canvas/load-blueprint?dashboard_viewport=${encodeURIComponent(CANVAS_STUDIO_VIEWPORT)}&include_hidden=true`,
    {
      credentials: "include",
      headers: { Accept: "application/json" },
    },
  );
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(
      typeof payload.detail === "string"
        ? payload.detail
        : "The canvas studio layout could not be loaded.",
    );
  }
  return response.json();
}

export default function NewCanvasProjectStudio({
  user,
  onReturnToHub,
  onSignOut,
  onUnauthorizedAccess,
}) {
  const [studioBlueprint, setStudioBlueprint] = useState(null);
  const [studioStep, setStudioStep] = useState("LOADING");
  const [loadState, setLoadState] = useState("loading");
  const [savingTemplateId, setSavingTemplateId] = useState("");
  const [savingSnapshot, setSavingSnapshot] = useState(false);
  const [pendingTemplate, setPendingTemplate] = useState(null);
  const [selectedThemeId, setSelectedThemeId] = useState(DEFAULT_STUDIO_THEME_ID);
  const [savedProjectId, setSavedProjectId] = useState("");
  const [compiling, setCompiling] = useState(false);
  const [projectTitle, setProjectTitle] = useState("");
  const [selectedBaseLayoutType, setSelectedBaseLayoutType] = useState("custom");
  const [errorMessage, setErrorMessage] = useState("");
  const [notice, setNotice] = useState("");
  const mayLoadStudio =
    canAccessGlobalWorkspace(user) && user?.role === "MASTER_ADMIN";

  useEffect(() => {
    if (!mayLoadStudio) return undefined;
    let active = true;
    fetchStudioBlueprint()
      .then((blueprint) => {
        if (!active) return;
        setStudioBlueprint(blueprint);
        const activeTab = getActiveProjectTab(blueprint?.layout);
        setSelectedThemeId(
          blueprint?.layout?.activeThemeId || DEFAULT_STUDIO_THEME_ID,
        );
        setProjectTitle(activeTab?.tabLabelDisplayName || "");
        setSelectedBaseLayoutType(inferBaseLayoutType(blueprint?.layout));
        setStudioStep("LAYOUT_SELECTION");
        setLoadState("ready");
      })
      .catch((error) => {
        if (!active) return;
        setErrorMessage(
          error instanceof Error
            ? error.message
            : "The canvas studio layout could not be loaded.",
        );
        setLoadState("error");
      });
    return () => {
      active = false;
    };
  }, [mayLoadStudio]);

  useEffect(() => {
    function handleStudioBlueprintUpdate(event) {
      const payload = event.detail?.blueprint;
      if (
        event.detail?.dashboardViewport !== CANVAS_STUDIO_VIEWPORT ||
        !payload?.layout
      ) {
        return;
      }
      setStudioBlueprint(payload);
      setSelectedThemeId(
        payload.layout.activeThemeId || DEFAULT_STUDIO_THEME_ID,
      );
      const activeTab = getActiveProjectTab(payload.layout);
      setProjectTitle((current) => current || activeTab?.tabLabelDisplayName || "");
      setSelectedBaseLayoutType(inferBaseLayoutType(payload.layout));
    }
    function handleStudioStateUpdate(event) {
      const { dashboardViewport, layout } = event.detail || {};
      if (dashboardViewport !== CANVAS_STUDIO_VIEWPORT || !layout) return;
      setStudioBlueprint((current) => ({
        ...(current || {}),
        dashboard_viewport: CANVAS_STUDIO_VIEWPORT,
        layout,
      }));
      setSelectedBaseLayoutType(inferBaseLayoutType(layout));
    }
    window.addEventListener(
      "fieldnotes:canvas-blueprint-updated",
      handleStudioBlueprintUpdate,
    );
    window.addEventListener(
      "fieldnotes:canvas-studio-state-updated",
      handleStudioStateUpdate,
    );
    return () => {
      window.removeEventListener(
        "fieldnotes:canvas-blueprint-updated",
        handleStudioBlueprintUpdate,
      );
      window.removeEventListener(
        "fieldnotes:canvas-studio-state-updated",
        handleStudioStateUpdate,
      );
    };
  }, []);

  if (!mayLoadStudio) {
    return (
      <GlobalWorkspaceAccessGate
        onUnauthorizedAccess={onUnauthorizedAccess}
      />
    );
  }

  const hasProjects = normalizeExistingTabs(studioBlueprint?.layout).length > 0;

  async function createProject(template, themeId = selectedThemeId) {
    if (savingTemplateId || !template) return;
    setSavingTemplateId(template.id);
    setErrorMessage("");
    setNotice("");
    try {
      const latestBlueprint = await fetchStudioBlueprint();
      const requestBody = buildCanvasStudioBlueprint(
        latestBlueprint,
        template,
        Date.now(),
        themeId,
      );
      const response = await fetch("/api/admin/canvas/save-blueprint", {
        method: "POST",
        credentials: "include",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          editingTargetView: requestBody.editingTargetView,
          layout: requestBody.layout,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          typeof payload.detail === "string"
            ? payload.detail
            : "The new canvas project could not be saved.",
        );
      }
      setStudioBlueprint(payload);
      setSelectedThemeId(themeId);
      setPendingTemplate(null);
      setLoadState("ready");
      setProjectTitle(requestBody.projectTab.tabLabelDisplayName);
      setSelectedBaseLayoutType(template.id);
      setStudioStep("CANVAS_ACTIVE");
      setNotice(`${requestBody.projectTab.tabLabelDisplayName} is ready to edit.`);
      window.dispatchEvent(
        new CustomEvent("fieldnotes:canvas-blueprint-updated", {
          detail: {
            dashboardViewport: CANVAS_STUDIO_VIEWPORT,
            blueprint: payload,
          },
        }),
      );
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : "The new canvas project could not be saved.",
      );
    } finally {
      setSavingTemplateId("");
    }
  }

  async function applyThemeToExistingProjects(themeId = selectedThemeId) {
    if (
      !studioBlueprint?.layout ||
      !getStudioTheme(themeId) ||
      savingTemplateId ||
      themeId === studioBlueprint.layout.activeThemeId
    ) {
      return;
    }
    setSavingTemplateId("theme");
    setErrorMessage("");
    setNotice("");
    try {
      const layout = applyStudioThemeToCanvasLayout(
        studioBlueprint.layout,
        themeId,
      );
      const response = await fetch("/api/admin/canvas/save-blueprint", {
        method: "POST",
        credentials: "include",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          editingTargetView: CANVAS_STUDIO_VIEWPORT,
          layout,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          typeof payload.detail === "string"
            ? payload.detail
            : "The canvas theme could not be saved.",
        );
      }
      setStudioBlueprint(payload);
      setSelectedThemeId(payload.layout.activeThemeId || themeId);
      setNotice(
        `${getStudioTheme(themeId).name} applied to every canvas view.`,
      );
      window.dispatchEvent(
        new CustomEvent("fieldnotes:canvas-blueprint-updated", {
          detail: {
            dashboardViewport: CANVAS_STUDIO_VIEWPORT,
            blueprint: payload,
          },
        }),
      );
    } catch (error) {
      setSelectedThemeId(
        studioBlueprint.layout.activeThemeId || DEFAULT_STUDIO_THEME_ID,
      );
      setErrorMessage(
        error instanceof Error
          ? error.message
          : "The canvas theme could not be saved.",
      );
    } finally {
      setSavingTemplateId("");
    }
  }

  async function saveProjectSnapshot({ returnToHub = true } = {}) {
    if (savingSnapshot || !hasProjects) return null;
    setSavingSnapshot(true);
    setErrorMessage("");
    setNotice("");
    try {
      if (!studioBlueprint?.layout) {
        throw new Error("The active canvas layout could not be loaded for saving.");
      }
      const saveLayoutResponse = await fetch("/api/admin/canvas/save-blueprint", {
        method: "POST",
        credentials: "include",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          editingTargetView: CANVAS_STUDIO_VIEWPORT,
          layout: studioBlueprint.layout,
        }),
      });
      const savedBlueprint = await saveLayoutResponse.json().catch(() => ({}));
      if (!saveLayoutResponse.ok || !savedBlueprint?.layout) {
        throw new Error(
          typeof savedBlueprint.detail === "string"
            ? savedBlueprint.detail
            : "The active canvas layout could not be saved.",
        );
      }
      setStudioBlueprint(savedBlueprint);
      window.dispatchEvent(
        new CustomEvent("fieldnotes:canvas-blueprint-updated", {
          detail: {
            dashboardViewport: CANVAS_STUDIO_VIEWPORT,
            blueprint: savedBlueprint,
          },
        }),
      );
      const requestBody = buildUserProjectSnapshot(
        projectTitle,
        selectedBaseLayoutType,
        savedBlueprint,
      );
      if (!requestBody.projectTitle) {
        throw new Error("Enter a title for this project before saving.");
      }
      const response = await fetch(
        savedProjectId
          ? `/api/studio/projects/update/${encodeURIComponent(savedProjectId)}`
          : "/api/studio/projects/create",
        {
          method: savedProjectId ? "PUT" : "POST",
          credentials: "include",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
          },
          body: JSON.stringify(requestBody),
        },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || typeof payload.projectId !== "string") {
        throw new Error(
          typeof payload.detail === "string"
            ? payload.detail
            : "The project snapshot could not be saved.",
        );
      }
      setSavedProjectId(payload.projectId);
      if (returnToHub) {
        onReturnToHub?.({
          collection: "canvas-projects",
          snapshot: payload,
        });
      }
      return payload;
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : "The project snapshot could not be saved.",
      );
      return null;
    } finally {
      setSavingSnapshot(false);
    }
  }

  async function compileStandaloneProject() {
    if (compiling || savingSnapshot || savingTemplateId || !hasProjects) return;
    setCompiling(true);
    setErrorMessage("");
    setNotice("");
    try {
      const snapshot = await saveProjectSnapshot({ returnToHub: false });
      if (!snapshot?.projectId) return;
      await downloadStandaloneBundle("canvas-projects", snapshot.projectId);
      setNotice(`${snapshot.projectTitle} was compiled and downloaded.`);
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : "The independent app bundle could not be downloaded.",
      );
    } finally {
      setCompiling(false);
    }
  }
  return (
    <main className="canvas-studio-page">
      <header className="canvas-studio-header">
        <button
          className="canvas-studio-header__back"
          onClick={onReturnToHub}
          type="button"
        >
          ← Project hub
        </button>
        <div className="canvas-studio-brand">
          <span className="canvas-studio-brand__mark">fn</span>
          <div>
            <strong>Canvas Project Studio</strong>
            <small>Fieldnotes · layout workspace</small>
          </div>
        </div>
        <div className="canvas-studio-header__user">
          <span>{user?.display_name || "Workspace user"}</span>
          <button
            aria-busy={savingSnapshot || compiling}
            className="canvas-studio-header__save"
            disabled={
              !hasProjects
              || studioStep !== "CANVAS_ACTIVE"
              || savingSnapshot
              || compiling
              || Boolean(savingTemplateId)
            }
            onClick={() => void saveProjectSnapshot()}
            type="button"
          >
            {savingSnapshot ? "Saving…" : "Save project"}
          </button>
          <button onClick={onSignOut} type="button">Sign out</button>
        </div>
      </header>

      <section aria-labelledby="canvas-studio-title" className="canvas-studio-main">
        <div className="canvas-studio-intro">
          <span className="canvas-studio-eyebrow">Preview first · Edit when ready</span>
          <h1 id="canvas-studio-title">
            {loadState === "loading"
              ? "Opening your canvas studio"
              : loadState === "error"
                ? "Canvas studio unavailable"
                : studioStep === "CANVAS_ACTIVE"
              ? "Your canvas workspace"
              : studioStep === "THEME_SELECTION"
                ? "Choose a theme"
                : hasProjects
                  ? "Start another canvas project"
                  : "Choose a canvas layout"}
          </h1>
          <p>
            {loadState === "loading"
              ? "Checking your saved projects before opening the right step."
              : loadState === "error"
                ? "The saved canvas state could not be restored."
                : studioStep === "CANVAS_ACTIVE"
              ? "Your saved layout is ready for absolute-coordinate editing and project snapshots."
              : studioStep === "THEME_SELECTION" && pendingTemplate
                ? `${pendingTemplate.title} will open with the selected theme and editable drop zones.`
                : "Select a responsive structure. It is saved to this tenant's canvas studio and opens with component drop zones."}
          </p>
          {studioStep === "CANVAS_ACTIVE" && hasProjects && (
            <button
              className="canvas-studio-theme-actions__primary"
              onClick={() => {
                setPendingTemplate(null);
                setStudioStep("LAYOUT_SELECTION");
              }}
              type="button"
            >
              Start another canvas project
            </button>
          )}
          {loadState !== "loading" && (
            <label className="canvas-studio-title-field">
              <span>Project title</span>
              <input
                aria-label="Project title"
                maxLength={120}
                onChange={(event) => setProjectTitle(event.target.value)}
                placeholder="Name this canvas project"
                value={projectTitle}
              />
            </label>
          )}
        </div>

        {loadState === "loading" && (
          <p aria-live="polite" className="canvas-studio-status" role="status">
            Loading saved canvas projects…
          </p>
        )}
        {loadState === "error" && (
          <p className="canvas-studio-error" role="alert">{errorMessage}</p>
        )}

        {loadState === "ready" && studioStep === "THEME_SELECTION" && pendingTemplate ? (
          <section className="canvas-studio-theme-step">
            <StudioThemeMatrix
              description="New views inherit this palette and font. You can still adjust every component after creation."
              disabled={Boolean(savingTemplateId)}
              onSelect={(themeId) => {
                if (savingTemplateId) return;
                setSelectedThemeId(themeId);
                void createProject(pendingTemplate, themeId);
              }}
              selectedThemeId={selectedThemeId}
              title={`Choose a theme for ${pendingTemplate.title}`}
            />
            <div className="canvas-studio-theme-actions">
              <button
                disabled={Boolean(savingTemplateId)}
                onClick={() => {
                  setPendingTemplate(null);
                  setStudioStep("LAYOUT_SELECTION");
                }}
                type="button"
              >
                Back to layouts
              </button>
            </div>
            {savingTemplateId === pendingTemplate.id && (
              <p aria-live="polite" className="canvas-studio-status" role="status">
                Creating {pendingTemplate.title} with {getStudioTheme(selectedThemeId)?.name}…
              </p>
            )}
          </section>
        ) : loadState === "ready" && studioStep === "LAYOUT_SELECTION" ? (
          <section aria-label="Canvas layout selection">
            {hasProjects && (
              <button
                className="canvas-studio-theme-actions__primary"
                onClick={() => setStudioStep("CANVAS_ACTIVE")}
                type="button"
              >
                Return to current canvas
              </button>
            )}
            <div aria-label="Canvas layout templates" className="canvas-studio-template-grid">
              {CANVAS_STUDIO_TEMPLATES.map((template) => (
                <button
                  aria-label={`Choose ${template.layoutLabel} layout: ${template.title}`}
                  aria-busy={savingTemplateId === template.id}
                  className={`canvas-studio-template canvas-studio-template--${template.diagram}`}
                  disabled={loadState !== "ready" || Boolean(savingTemplateId)}
                  key={template.id}
                  onClick={() => {
                    setPendingTemplate(template);
                    setStudioStep("THEME_SELECTION");
                  }}
                  type="button"
                >
                  <span
                    aria-hidden="true"
                    className={`canvas-studio-diagram canvas-studio-diagram--${template.diagram}`}
                  >
                    {template.slots.map((slot, index) => (
                      <span key={`${template.id}-diagram-${index}`} />
                    ))}
                  </span>
                  <span className="canvas-studio-template__copy">
                    <small>{template.layoutLabel} layout</small>
                    <strong>{template.title}</strong>
                    <small>{template.description}</small>
                  </span>
                  <span className="canvas-studio-template__action">
                    Choose layout
                    <span aria-hidden="true">↗</span>
                  </span>
                </button>
              ))}
            </div>
          </section>
        ) : null}

        {loadState === "ready" && studioStep === "CANVAS_ACTIVE" && hasProjects && (
          <section
            aria-label="Apply a theme to current canvas views"
            className="canvas-studio-current-theme"
          >
            <StudioThemeMatrix
              description="Selecting a preset applies it to every saved canvas view immediately. Individual colors and fonts remain editable."
              disabled={Boolean(savingTemplateId)}
              onSelect={(themeId) => {
                if (
                  savingTemplateId ||
                  themeId === studioBlueprint?.layout?.activeThemeId
                ) {
                  return;
                }
                setSelectedThemeId(themeId);
                void applyThemeToExistingProjects(themeId);
              }}
              selectedThemeId={selectedThemeId}
              title="Theme the current project"
            />
            {savingTemplateId === "theme" && (
              <p aria-live="polite" className="canvas-studio-status" role="status">
                Applying {getStudioTheme(selectedThemeId)?.name || "theme"} to every canvas view…
              </p>
            )}
          </section>
        )}

        {errorMessage && loadState !== "error" && (
          <p className="canvas-studio-error" role="alert">{errorMessage}</p>
        )}
        {notice && (
          <p aria-live="polite" className="canvas-studio-notice" role="status">
            {notice}
          </p>
        )}

        {loadState === "ready" && studioStep === "CANVAS_ACTIVE" && hasProjects ? (
          <section aria-label="Canvas project staging area" className="canvas-studio-workspace">
            <ActiveCanvasMeshProfile
              dashboardViewportOverride={CANVAS_STUDIO_VIEWPORT}
              onStandaloneExport={compileStandaloneProject}
              standaloneExportMessage={
                compiling
                  ? "Saving the project and preparing the ZIP…"
                  : errorMessage || notice
              }
              standaloneExportState={
                compiling
                  ? "loading"
                  : errorMessage
                    ? "error"
                    : notice
                      ? "success"
                      : "idle"
              }
              role="MASTER_ADMIN"
              user={user}
            >
              <CanvasSubTabArea role="MASTER_ADMIN" />
            </ActiveCanvasMeshProfile>
          </section>
        ) : !hasProjects && studioStep === "LAYOUT_SELECTION" && loadState === "ready" ? (
          <div className="canvas-studio-empty">
            <span aria-hidden="true">▦</span>
            <strong>Your staging canvas will appear here.</strong>
            <p>Choose one of the three structures above to create the first project.</p>
          </div>
        ) : null}
      </section>
    </main>
  );
}