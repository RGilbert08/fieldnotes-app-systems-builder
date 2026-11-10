import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import NewCanvasProjectStudio, {
  buildCanvasStudioBlueprint,
  buildUserProjectSnapshot,
  CANVAS_STUDIO_TEMPLATES,
} from "./NewCanvasProjectStudio";
import { restrictedWorkspaceFromPath } from "../security/globalWorkspaceAccess";
import { STUDIO_THEMES } from "../utils/studioThemes";

vi.mock("../components/DynamicMeshContainer", () => ({
  ActiveCanvasMeshProfile: ({ children }) => (
    <div data-testid="active-canvas-profile">{children}</div>
  ),
  CanvasSubTabArea: () => <div>Project canvas preview</div>,
}));

const masterAdmin = {
  role: "MASTER_ADMIN",
  can_access_global_project_hub: true,
  display_name: "Naledi",
};

function jsonResponse(body, ok = true) {
  return {
    ok,
    json: async () => body,
  };
}

function chooseCanvasLayoutAndTheme(layout = "Showcase") {
  fireEvent.click(
    screen.getByRole("button", { name: new RegExp(`Choose ${layout} layout`) }),
  );
  fireEvent.click(screen.getByText(/Executive Navy/));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("NewCanvasProjectStudio", () => {
  it("routes the launcher path to the studio wizard", () => {
    expect(restrictedWorkspaceFromPath("/studio/new-project")).toBe("studio");
  });

  it("creates the balanced, three-rail, and asymmetric responsive geometries", () => {
    const [balancedTemplate, railsTemplate, analyticsTemplate] =
      CANVAS_STUDIO_TEMPLATES;
    const balanced = buildCanvasStudioBlueprint(null, balancedTemplate, 100);
    const rails = buildCanvasStudioBlueprint(null, railsTemplate, 200);
    const analytics = buildCanvasStudioBlueprint(null, analyticsTemplate, 300);

    expect(balanced.layout).toMatchObject({ gridColumns: 2, gridRows: 1 });
    expect(balanced.layout.canvasLayout).toHaveLength(2);
    expect(rails.layout).toMatchObject({ gridColumns: 10, gridRows: 1 });
    expect(rails.layout.canvasLayout.map((slot) => slot.gridColumnSpan))
      .toEqual([2, 6, 2]);
    expect(analytics.layout).toMatchObject({ gridColumns: 12, gridRows: 2 });
    expect(analytics.layout.canvasLayout).toHaveLength(4);
    expect(analytics.layout.canvasLayout.map((slot) => slot.gridColumnSpan))
      .toEqual([12, 4, 4, 4]);
  });

  it("routes Showcase, Hub, and Grid selections through the theme step", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(null)));
    render(
      <NewCanvasProjectStudio
        onReturnToHub={vi.fn()}
        onSignOut={vi.fn()}
        onUnauthorizedAccess={vi.fn()}
        user={masterAdmin}
      />,
    );

    await screen.findByRole("button", { name: /Choose Showcase layout/ });
    for (const layout of ["Showcase", "Hub", "Grid"]) {
      fireEvent.click(
        screen.getByRole("button", { name: new RegExp(`Choose ${layout} layout`) }),
      );
      expect(
        screen.getByRole("heading", {
          name: /Choose a theme for/,
        }),
      ).toBeInTheDocument();
      for (const theme of STUDIO_THEMES) {
        expect(screen.getByText(theme.name)).toBeInTheDocument();
      }
      expect(screen.getByText(/Executive Navy/)).toBeInTheDocument();
      expect(screen.getByText(/Academic Emerald/)).toBeInTheDocument();
      expect(screen.getByText(/Obsidian Minimalist/)).toBeInTheDocument();
      expect(document.querySelectorAll(".studio-theme-card__swatches")).toHaveLength(15);
      fireEvent.click(screen.getByRole("button", { name: "Back to layouts" }));
    }
  });

  it("waits for saved projects before choosing the initial studio step", async () => {
    const existingProject = buildCanvasStudioBlueprint(
      null,
      CANVAS_STUDIO_TEMPLATES[0],
      350,
    );
    const savedBlueprint = {
      id: 9,
      dashboard_viewport: "Canvas Project Studio",
      layout: existingProject.layout,
    };
    let resolveLoad;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise((resolve) => {
            resolveLoad = resolve;
          }),
      ),
    );

    render(
      <NewCanvasProjectStudio
        onReturnToHub={vi.fn()}
        onSignOut={vi.fn()}
        onUnauthorizedAccess={vi.fn()}
        user={masterAdmin}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "Opening your canvas studio" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Loading saved canvas projects…")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Choose .* layout/ }),
    ).not.toBeInTheDocument();

    await act(async () => {
      resolveLoad(jsonResponse(savedBlueprint));
    });

    await screen.findByRole("heading", { name: "Start another canvas project" });
    expect(
      screen.getByRole("button", { name: /Choose Showcase layout/ }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Theme the current project")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: /Choose a theme for/ }),
    ).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "Return to current canvas" }),
    );
    await screen.findByRole("heading", { name: "Your canvas workspace" });
    expect(screen.getByText("Theme the current project")).toBeInTheDocument();
  });

  it("packages layout geometry, colors, design bindings, and allowlist tokens", () => {
    const blueprint = buildCanvasStudioBlueprint(
      null,
      CANVAS_STUDIO_TEMPLATES[0],
      400,
    );
    const snapshot = buildUserProjectSnapshot(
      "Grade 5 workspace",
      CANVAS_STUDIO_TEMPLATES[0].id,
      blueprint,
    );

    expect(snapshot.projectTitle).toBe("Grade 5 workspace");
    expect(snapshot.selectedBaseLayoutType).toBe("balanced-dual-column");
    expect(snapshot.canvasBlueprintData.coordinateMatrix).toHaveLength(2);
    expect(snapshot.canvasBlueprintData.coordinateMatrix[0]).toMatchObject({
      gridColumn: 1,
      gridRow: 1,
      gridColumnSpan: 1,
    });
    expect(snapshot.canvasBlueprintData.colorWheelValues[0]).toMatchObject({
      foreground: "#72513D",
      background: "#F8F5EF",
    });
    expect(snapshot.canvasBlueprintData.allowlistTokens).toMatchObject({
      componentTypes: ["text_observation_block"],
      actionBindings: [],
      variableBindings: [],
    });
  });

  it("saves a selected baseline as a tenant studio blueprint with drop zones", async () => {
    const savedBlueprint = {
      id: 9,
      dashboard_viewport: "Canvas Project Studio",
      layout: {
        layoutMode: "grid",
        gridColumns: 2,
        subTabs: [
          {
            tabId: "studio-created",
            tabLabelDisplayName: "Balanced Dual-Column Split Layout",
            canvasLayout: [],
          },
        ],
        activeSubTabId: "studio-created",
      },
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(null))
      .mockResolvedValueOnce(jsonResponse(null))
      .mockResolvedValueOnce(jsonResponse(savedBlueprint));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <NewCanvasProjectStudio
        onReturnToHub={vi.fn()}
        onSignOut={vi.fn()}
        onUnauthorizedAccess={vi.fn()}
        user={masterAdmin}
      />,
    );

    await screen.findByRole("button", { name: /Choose Showcase layout/ });
    expect(screen.queryByText("Project canvas preview")).not.toBeInTheDocument();
    chooseCanvasLayoutAndTheme();

    await screen.findByText("Project canvas preview");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    const [saveUrl, saveOptions] = fetchMock.mock.calls[2];
    const savedRequest = JSON.parse(saveOptions.body);

    expect(saveUrl).toBe("/api/admin/canvas/save-blueprint");
    expect(savedRequest.editingTargetView).toBe("Canvas Project Studio");
    expect(savedRequest.layout).toMatchObject({
      gridColumns: 2,
      gridRows: 1,
      activeSubTabId: expect.any(String),
      activeThemeId: "executive-navy-ivory",
    });
    expect(savedRequest.layout.canvasLayout).toHaveLength(2);
    expect(savedRequest.layout.canvasLayout[0]).toMatchObject({
      type: "text_observation_block",
      label: "➕ Drop Component Box Here",
      gridColumn: 1,
      gridColumnSpan: 1,
    });
    expect(savedRequest.layout.subTabs[0].canvasLayout).toEqual(
      savedRequest.layout.canvasLayout,
    );
  });

  it("applies a theme immediately when one is selected for the current canvas", async () => {
    const initialLayout = buildCanvasStudioBlueprint(
      null,
      CANVAS_STUDIO_TEMPLATES[0],
      680,
      "warm-walnut-bone",
    ).layout;
    const savedBlueprint = {
      id: 20,
      dashboard_viewport: "Canvas Project Studio",
      layout: initialLayout,
    };
    const selectedTheme = STUDIO_THEMES.find(
      (theme) => theme.id === "executive-navy-ivory",
    );
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(savedBlueprint))
      .mockImplementationOnce(async (_url, options) => {
        const requestBody = JSON.parse(options.body);
        return jsonResponse({ ...savedBlueprint, layout: requestBody.layout });
      });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <NewCanvasProjectStudio
        onReturnToHub={vi.fn()}
        onSignOut={vi.fn()}
        onUnauthorizedAccess={vi.fn()}
        user={masterAdmin}
      />,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Return to current canvas" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: /Executive Navy/ }),
    );

    expect(
      await screen.findByText(
        `${selectedTheme.name} applied to every canvas view.`,
      ),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [saveUrl, saveOptions] = fetchMock.mock.calls[1];
    const requestBody = JSON.parse(saveOptions.body);
    expect(saveUrl).toBe("/api/admin/canvas/save-blueprint");
    expect(requestBody.layout.activeThemeId).toBe(selectedTheme.id);
    expect(requestBody.layout.canvasLayout[0].styles).toMatchObject({
      color: selectedTheme.tokens.text,
      backgroundColor: selectedTheme.tokens.surface,
      borderColor: selectedTheme.tokens.border,
      fontFamily: selectedTheme.fontKey,
    });
    expect(
      screen.queryByRole("button", { name: "Apply theme to every view" }),
    ).not.toBeInTheDocument();
  });

  it("saves the active project snapshot from the header and returns to the hub", async () => {
    const blueprint = buildCanvasStudioBlueprint(
      null,
      CANVAS_STUDIO_TEMPLATES[0],
      500,
    );
    const savedBlueprint = {
      id: 11,
      dashboard_viewport: "Canvas Project Studio",
      layout: blueprint.layout,
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(savedBlueprint))
      .mockImplementationOnce(async (_url, options) =>
        jsonResponse({
          ...savedBlueprint,
          layout: JSON.parse(options.body).layout,
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ projectId: "snapshot-500" }));
    vi.stubGlobal("fetch", fetchMock);
    const onReturnToHub = vi.fn();

    render(
      <NewCanvasProjectStudio
        onReturnToHub={onReturnToHub}
        onSignOut={vi.fn()}
        onUnauthorizedAccess={vi.fn()}
        user={masterAdmin}
      />,
    );

    await screen.findByRole("button", { name: "Return to current canvas" });
    fireEvent.click(
      screen.getByRole("button", { name: "Return to current canvas" }),
    );

    const titleInput = await screen.findByRole("textbox", {
      name: "Project title",
    });
    fireEvent.change(titleInput, { target: { value: "Grade 5 workspace" } });
    const activeComponent = savedBlueprint.layout.canvasLayout[0];
    const updatedComponents = savedBlueprint.layout.canvasLayout.map((item) =>
      item.id === activeComponent.id ? { ...item, x: 12 } : item,
    );
    const updatedLayout = {
      ...savedBlueprint.layout,
      canvasLayout: updatedComponents,
      subTabs: savedBlueprint.layout.subTabs.map((tab) => ({
        ...tab,
        canvasLayout: updatedComponents,
      })),
    };
    act(() => {
      window.dispatchEvent(
        new CustomEvent("fieldnotes:canvas-studio-state-updated", {
          detail: {
            dashboardViewport: "Canvas Project Studio",
            layout: updatedLayout,
          },
        }),
      );
    });
    fireEvent.click(screen.getByRole("button", { name: "Save project" }));

    await waitFor(() => {
      expect(onReturnToHub).toHaveBeenCalledWith({
        collection: "canvas-projects",
        snapshot: { projectId: "snapshot-500" },
      });
    });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    const [saveLayoutUrl, saveLayoutOptions] = fetchMock.mock.calls[1];
    const savedLayoutRequest = JSON.parse(saveLayoutOptions.body);
    expect(saveLayoutUrl).toBe("/api/admin/canvas/save-blueprint");
    expect(savedLayoutRequest.layout.canvasLayout[0].x).toBe(12);
    const [saveUrl, saveOptions] = fetchMock.mock.calls[2];
    const requestBody = JSON.parse(saveOptions.body);
    expect(saveUrl).toBe("/api/studio/projects/create");
    expect(requestBody).toMatchObject({
      projectTitle: "Grade 5 workspace",
      selectedBaseLayoutType: "balanced-dual-column",
      canvasBlueprintData: {
        coordinateMatrix: expect.any(Array),
        colorWheelValues: expect.any(Array),
        allowlistTokens: expect.any(Object),
      },
    });
    expect(requestBody.canvasBlueprintData.coordinateMatrix[0].x).toBe(12);
  });
});