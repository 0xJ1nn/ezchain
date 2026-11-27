# Published contract snapshots

The API document, the registered MCP tools and the plugin SDK interfaces are the promises
ezCHAIN makes to clients. Snapshots of all three live here. The gate compares the live
surfaces with the snapshots on every test run and reports the exact field paths that moved.

A change to a surface requires a version bump. The version comes from the API package's
`__version__`; there is no second version constant for contracts. After reviewing the
change, refresh the snapshots with the check script's update mode and commit the snapshots
and the version bump together.

A field that is removed or renamed needs one whole minor version of deprecation. During
that window the old field still works and its schema advertises the replacement. Only the
next minor version may drop it. The deprecation ledger records the surface, the field path,
the two versions and the warning text; the gate refuses a field that disappears early or
without an entry.

Run the check directly with the repository's Python environment:

```
python tools/check_contracts.py --check
python tools/check_contracts.py --update
```
