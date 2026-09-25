// WHY: backend-independent slice — every shell value is mock until M10.4.x
// backend contracts freeze; browser never talks to the API in this slice.
export const mockUser = { role: "Viewer", authed: false };

export const mockSurfaces = ["public research", "workspace", "admin controls"];
