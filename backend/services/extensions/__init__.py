"""Community extensions platform — services layer (P4-EXT phase-1).

Sub-modules:
  manifest.py  — validate_extension_manifest() + per-type contribution checks
  registry.py  — ExtensionRegistry: discover + load installed packs;
                 get_data_pack() resolver with user-pack > shipped precedence
  install.py   — install_extension() + uninstall_extension(); zip-slip + SSRF
                 guarded; returns install-time permission review payload

SECURITY-CRITICAL: this platform installs untrusted content. The /security-review
gate MUST pass before merge. Phase-1 restricts to data_pack only (interpret-only,
no code execution). compute_plugin / ui_contribution are REFUSED with a clear
"not yet supported" error until the gated phases ship.
"""
