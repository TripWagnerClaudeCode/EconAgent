# vendor/

Third-party MCP servers are **not** committed. `scripts/install_vendors.sh` clones each one at the exact commit listed in
`scripts/vendors.txt` (all MIT-licensed), builds it, and applies the patches in `../patches/`. Everything here except this file
is git-ignored.
