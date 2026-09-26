export function resolveFilteredActiveId<T extends { id: string }>(
  filtered: readonly T[],
  requestedId: string,
) {
  if (filtered.some((item) => item.id === requestedId)) return requestedId;
  return filtered[0]?.id ?? "";
}
