"""safe-data: a compute-to-data MCP server.

Claude never reads raw personal data. It asks this server to describe schemas,
run SQL against PII-free views, or execute a script inside a network-less
container; the server returns aggregates, schemas, and synthetic samples only.
"""

__version__ = "0.1.0"
