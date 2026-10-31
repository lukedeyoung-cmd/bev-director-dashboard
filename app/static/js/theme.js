// =============================================================================
// THEME TOGGLE
// Supports: system preference, manual toggle, localStorage persistence
// =============================================================================

;(() => {
  const STORAGE_KEY = 'obvious-theme'

  // Get stored preference or system preference
  function getPreferredTheme() {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) {
      return stored
    }

    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  }

  // Apply theme to document
  function setTheme(theme) {
    const root = document.documentElement

    if (theme === 'dark') {
      root.classList.add('dark')
      root.classList.remove('light')
    } else {
      root.classList.add('light')
      root.classList.remove('dark')
    }

    localStorage.setItem(STORAGE_KEY, theme)
  }

  // Toggle between light and dark
  function toggleTheme() {
    const current = document.documentElement.classList.contains('dark') ? 'dark' : 'light'
    const next = current === 'dark' ? 'light' : 'dark'
    setTheme(next)
  }

  // Initialize on load
  setTheme(getPreferredTheme())

  // Listen for system preference changes
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
    // Only auto-switch if user hasn't manually set a preference
    if (!localStorage.getItem(STORAGE_KEY)) {
      setTheme(e.matches ? 'dark' : 'light')
    }
  })

  // Expose toggle function globally
  window.toggleTheme = toggleTheme

  // Auto-bind to .theme-toggle buttons
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.theme-toggle').forEach((btn) => {
      btn.addEventListener('click', toggleTheme)
    })
  })
})()
