import { describe, expect, it } from "vitest";
import {
  MASTER_OVERVIEW_CONTAINER_TYPE,
  canInsertCanvasComponentTree,
  filterCanvasComponentTreeForRole,
  findCanvasComponentLocation,
  insertCanvasComponentTree,
  moveCanvasComponentTree,
  removeCanvasComponentTree,
  updateCanvasComponentTree,
} from "./canvasComponentTree";

const container = (id, children = []) => ({
  id,
  type: MASTER_OVERVIEW_CONTAINER_TYPE,
  children,
});

describe("canvas component trees", () => {
  it("updates and removes nested blocks immutably", () => {
    const original = [container("outer", [{ id: "inner", label: "Before" }])];
    const updated = updateCanvasComponentTree(original, "inner", (item) => ({
      ...item,
      label: "After",
    }));
    const removed = removeCanvasComponentTree(updated, "inner");

    expect(updated[0].children[0].label).toBe("After");
    expect(original[0].children[0].label).toBe("Before");
    expect(removed.removed.id).toBe("inner");
    expect(removed.components[0].children).toEqual([]);
  });

  it("moves blocks into nested grid siblings and rejects ancestor cycles", () => {
    const original = [
      container("outer", [{ id: "first", gridOrderIndex: 0 }]),
      { id: "second", type: "rich_text_block", gridOrderIndex: 1 },
    ];
    const moved = moveCanvasComponentTree(original, {
      sourceId: "second",
      parentId: "outer",
      targetId: "first",
    });
    const cycle = moveCanvasComponentTree(moved.components, {
      sourceId: "outer",
      parentId: "first",
    });

    expect(moved.moved).toBe(true);
    expect(moved.components[0].children.map((item) => item.id)).toEqual([
      "second",
      "first",
    ]);
    expect(cycle).toMatchObject({ moved: false, reason: "cycle" });
  });

  it("stores absolute drops in coordinates relative to the destination shell", () => {
    const original = [container("outer", [{ id: "child", x: 4, y: 6 }])];
    const moved = moveCanvasComponentTree(original, {
      sourceId: "child",
      parentId: "outer",
      layoutMode: "absolute",
      position: { x: 72, y: 38 },
    });

    expect(moved.moved).toBe(true);
    expect(moved.components[0].children[0]).toMatchObject({
      id: "child",
      x: 72,
      y: 38,
    });
  });

  it("finds ancestry and only displays role-visible descendants", () => {
    const tree = [
      container("outer", [
        { id: "visible", visibility: { principal: true } },
        { id: "hidden", visibility: { principal: false } },
      ]),
    ];

    expect(findCanvasComponentLocation(tree, "visible").ancestors).toEqual([
      "outer",
    ]);
    expect(
      filterCanvasComponentTreeForRole(tree, "principal")[0].children.map(
        (item) => item.id,
      ),
    ).toEqual(["visible"]);
    expect(
      filterCanvasComponentTreeForRole(tree, "principal", true)[0].children,
    ).toHaveLength(2);
  });

  it("enforces depth and node-count limits for insertions", () => {
    const nested = container(
      "level-1",
      [container("level-2", [container("level-3", [])])],
    );
    const tree = [nested];

    expect(canInsertCanvasComponentTree(tree, "level-3", { id: "leaf" })).toBe(
      true,
    );
    expect(
      canInsertCanvasComponentTree(
        tree,
        "level-3",
        container("too-deep", [container("child", [])]),
      ),
    ).toBe(false);
  });

  it("enforces the server's direct-child limit for nested containers", () => {
    const fullContainer = container(
      "full",
      Array.from({ length: 20 }, (_, index) => ({ id: `child-${index}` })),
    );
    const tree = [fullContainer, { id: "source", type: "rich_text_block" }];

    expect(
      canInsertCanvasComponentTree(tree, "full", {
        id: "new-child",
        type: "rich_text_block",
      }),
    ).toBe(false);
    expect(
      insertCanvasComponentTree(tree, "full", {
        id: "new-child",
        type: "rich_text_block",
      }).inserted,
    ).toBe(false);
    expect(
      moveCanvasComponentTree(tree, {
        sourceId: "source",
        parentId: "full",
      }),
    ).toMatchObject({ moved: false, reason: "parent-full" });
  });
});