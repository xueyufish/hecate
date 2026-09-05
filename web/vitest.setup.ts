import "@testing-library/jest-dom/vitest";

// jsdom does not implement scrollIntoView; ChatSurface scrolls on mount.
Element.prototype.scrollIntoView = () => {};
