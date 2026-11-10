export const MASTER_OVERVIEW_CONTAINER_TYPE = "master_overview_container";
export const MAX_CANVAS_COMPONENT_TREE_DEPTH = 4;
export const MAX_CANVAS_COMPONENT_TREE_NODES = 80;
export const MAX_CANVAS_CONTAINER_CHILDREN = 20;

export function flattenCanvasComponentTree(components = []) {
  return components.flatMap((component) => [
    component,
    ...flattenCanvasComponentTree(component.children || []),
  ]);
}

export function findCanvasComponentLocation(
  components,
  componentId,
  parentId = null,
  ancestors = [],
) {
  for (let index = 0; index < components.length; index += 1) {
    const component = components[index];
    if (component.id === componentId) {
      return { component, parentId, siblings: components, index, ancestors };
    }
    const nested = findCanvasComponentLocation(
      component.children || [],
      componentId,
      component.id,
      [...ancestors, component.id],
    );
    if (nested) return nested;
  }
  return null;
}

export function updateCanvasComponentTree(components, componentId, update) {
  let changed = false;
  const next = components.map((component) => {
    if (component.id === componentId) {
      changed = true;
      return update(component);
    }
    if (!component.children?.length) return component;
    const children = updateCanvasComponentTree(
      component.children,
      componentId,
      update,
    );
    if (children === component.children) return component;
    changed = true;
    return { ...component, children };
  });
  return changed ? next : components;
}

export function removeCanvasComponentTree(components, componentId) {
  let removed = null;
  const next = [];
  for (const component of components) {
    if (component.id === componentId) {
      removed = component;
      continue;
    }
    if (component.children?.length) {
      const nested = removeCanvasComponentTree(component.children, componentId);
      if (nested.removed) {
        removed = nested.removed;
        next.push({ ...component, children: nested.components });
        continue;
      }
    }
    next.push(component);
  }
  return { components: removed ? next : components, removed };
}

export function insertCanvasComponentTree(
  components,
  parentId,
  component,
  index = null,
) {
  if (!parentId) {
    const next = [...components];
    const insertionIndex = Number.isInteger(index)
      ? Math.max(0, Math.min(index, next.length))
      : next.length;
    next.splice(insertionIndex, 0, component);
    return { components: next, inserted: true };
  }

  const parentLocation = findCanvasComponentLocation(components, parentId);
  if (
    !parentLocation ||
    parentLocation.component.type !== MASTER_OVERVIEW_CONTAINER_TYPE
  ) {
    return { components, inserted: false };
  }
  if (
    (parentLocation.component.children || []).length >=
    MAX_CANVAS_CONTAINER_CHILDREN
  ) {
    return { components, inserted: false };
  }

  let inserted = false;
  const next = updateCanvasComponentTree(components, parentId, (parent) => {
    const children = [...(parent.children || [])];
    const insertionIndex = Number.isInteger(index)
      ? Math.max(0, Math.min(index, children.length))
      : children.length;
    children.splice(insertionIndex, 0, component);
    inserted = true;
    return { ...parent, children };
  });
  return { components: next, inserted };
}

export function canvasTreeStats(components = []) {
  let nodeCount = 0;
  let maxDepth = 0;
  const visit = (items, depth) => {
    for (const component of items) {
      nodeCount += 1;
      maxDepth = Math.max(maxDepth, depth);
      visit(component.children || [], depth + 1);
    }
  };
  visit(components, 1);
  return { nodeCount, maxDepth };
}

export function canInsertCanvasComponentTree(
  components,
  parentId,
  component,
) {
  const parentLocation = parentId
    ? findCanvasComponentLocation(components, parentId)
    : null;
  if (
    parentId &&
    (!parentLocation ||
      parentLocation.component.type !== MASTER_OVERVIEW_CONTAINER_TYPE)
  ) {
    return false;
  }
  if (
    parentId &&
    (parentLocation.component.children || []).length >=
      MAX_CANVAS_CONTAINER_CHILDREN
  ) {
    return false;
  }
  const { nodeCount, maxDepth } = canvasTreeStats(components);
  const newTree = canvasTreeStats([component]);
  const newDepth = parentLocation
    ? parentLocation.ancestors.length + 1 + newTree.maxDepth
    : newTree.maxDepth;
  return (
    nodeCount + newTree.nodeCount <= MAX_CANVAS_COMPONENT_TREE_NODES &&
    newDepth <= MAX_CANVAS_COMPONENT_TREE_DEPTH
  );
}

export function moveCanvasComponentTree(
  components,
  {
    sourceId,
    parentId = null,
    targetId = null,
    layoutMode = "grid",
    position = null,
  },
) {
  const sourceLocation = findCanvasComponentLocation(components, sourceId);
  if (!sourceLocation) return { components, moved: false, reason: "missing-source" };

  const targetParent = parentId
    ? findCanvasComponentLocation(components, parentId)
    : null;
  if (
    parentId === sourceId ||
    targetParent?.ancestors.includes(sourceId)
  ) {
    return { components, moved: false, reason: "cycle" };
  }
  if (
    parentId &&
    (!targetParent ||
      targetParent.component.type !== MASTER_OVERVIEW_CONTAINER_TYPE)
  ) {
    return { components, moved: false, reason: "invalid-parent" };
  }

  const removed = removeCanvasComponentTree(components, sourceId);
  if (!removed.removed) return { components, moved: false, reason: "missing-source" };
  const destination = parentId
    ? findCanvasComponentLocation(removed.components, parentId)
    : null;
  if (parentId && !destination) {
    return { components, moved: false, reason: "invalid-parent" };
  }
  if (
    parentId &&
    (destination.component.children || []).length >=
      MAX_CANVAS_CONTAINER_CHILDREN
  ) {
    return { components, moved: false, reason: "parent-full" };
  }

  const siblings = parentId
    ? destination.component.children || []
    : removed.components;
  const ordered = siblings
    .map((component, sourceIndex) => ({
      component,
      sourceIndex,
      order: Number.isInteger(component.gridOrderIndex)
        ? component.gridOrderIndex
        : sourceIndex,
    }))
    .sort((left, right) => left.order - right.order || left.sourceIndex - right.sourceIndex)
    .map(({ component }) => component);
  const insertionIndex = targetId
    ? ordered.findIndex((component) => component.id === targetId)
    : -1;
  const nextComponent = position
    ? { ...removed.removed, ...position }
    : removed.removed;
  ordered.splice(
    insertionIndex >= 0 ? insertionIndex : ordered.length,
    0,
    nextComponent,
  );
  const normalizedSiblings =
    layoutMode === "absolute"
      ? ordered
      : ordered.map((component, index) => ({
          ...component,
          gridOrderIndex: index,
        }));

  if (!parentId) {
    return { components: normalizedSiblings, moved: true };
  }

  let inserted = false;
  const next = updateCanvasComponentTree(
    removed.components,
    parentId,
    (parent) => {
      inserted = true;
      return { ...parent, children: normalizedSiblings };
    },
  );
  return inserted
    ? { components: next, moved: true }
    : { components, moved: false, reason: "invalid-parent" };
}

export function filterCanvasComponentTreeForRole(
  components,
  roleKey,
  canSeeHidden = false,
) {
  const visible = [];
  for (const component of components) {
    if (!canSeeHidden && component.visibility?.[roleKey] === false) continue;
    const next = { ...component };
    if (component.children) {
      next.children = filterCanvasComponentTreeForRole(
        component.children,
        roleKey,
        canSeeHidden,
      );
    }
    visible.push(next);
  }
  return visible;
}