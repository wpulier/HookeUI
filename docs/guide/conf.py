"""Build the public handbook without importing or installing HookeUI."""
project = "HookeUI"
author = "HookeUI contributors"
copyright = "2026, HookeUI contributors"
extensions = ["myst_parser"]
source_suffix = {".md": "markdown"}
root_doc = "index"
exclude_patterns = ["_build", "README.md"]
html_theme = "furo"
html_title = "HookeUI documentation"
html_theme_options = {
    "source_repository": "https://github.com/wpulier/HookeUI/",
    "source_branch": "dev",
    "source_directory": "docs/guide/",
    "light_css_variables": {"color-brand-primary": "#72551c", "color-brand-content": "#72551c"},
    "dark_css_variables": {"color-brand-primary": "#e8c679", "color-brand-content": "#e8c679"},
}
html_show_sphinx = False
