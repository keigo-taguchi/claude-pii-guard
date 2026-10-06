"""pii-guard: local masking daemon used by the Claude Code mod, support-intake, and nightly scans.

Nothing leaves this process. Dictionary and vault live on disk under
~/.config/safe-data/ (0600). Responses describe kinds and counts, never values.
"""

__version__ = "0.1.0"
