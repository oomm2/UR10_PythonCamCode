/**
 * Light/dark theme management.
 *
 * The dashboard ships dark by default (best on a monitor) but a projector in a
 * bright room reads much better in light mode, so the choice is persisted and
 * applied by toggling a ``data-theme`` attribute that the CSS variables react to.
 */
export const THEMES = ['dark', 'light'];
export const STORAGE_KEY = 'ur-monitor-theme';

export function isTheme(value) {
  return THEMES.includes(value);
}

/** Read the stored theme, falling back when storage is unavailable. */
export function readStoredTheme(storage) {
  try {
    const value = storage?.getItem(STORAGE_KEY);
    return isTheme(value) ? value : null;
  } catch {
    // Private browsing modes can throw on access; the default still applies.
    return null;
  }
}

/** Persist the theme, ignoring quota or disabled-storage errors. */
export function storeTheme(storage, theme) {
  try {
    storage?.setItem(STORAGE_KEY, theme);
    return true;
  } catch {
    return false;
  }
}

export function nextTheme(theme) {
  return theme === 'light' ? 'dark' : 'light';
}

/**
 * Apply a theme to the document and report it to the caller.
 * ``document`` is injected so this stays testable outside a browser.
 */
export function applyTheme(doc, theme) {
  const chosen = isTheme(theme) ? theme : 'dark';
  doc.documentElement.setAttribute('data-theme', chosen);
  doc.documentElement.style.colorScheme = chosen;
  return chosen;
}

/** Wire a toggle button and keyboard shortcut to the theme state. */
export function createThemeController({ doc = document, storage = globalThis.localStorage } = {}) {
  let current = applyTheme(doc, readStoredTheme(storage) || 'dark');
  const listeners = new Set();
  const notify = () => listeners.forEach((listener) => listener(current));
  return {
    get theme() {
      return current;
    },
    set(theme) {
      current = applyTheme(doc, theme);
      storeTheme(storage, current);
      notify();
      return current;
    },
    toggle() {
      return this.set(nextTheme(current));
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}
