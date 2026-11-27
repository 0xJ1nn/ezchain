# @ezchain/cli

Launcher for the ezCHAIN workstation. The package installs a single `ezchain`
command and declares one optional binary package per host; npm selects the
matching one. There is no install script: the command resolves the staged
native binary in that package and runs it.

Version 0.1.0 is self-hosted and unsigned. The binary packages published to a
registry carry the native executable staged from the GitHub Release artifact
for the tag. This source checkout ships the packaging skeleton only — no native
binary is committed, so a locally packed package fails closed until a binary is
staged. See `docs/FIRST-OPEN.md` in the repository for install paths, the
paper-mode warning and the data/log locations.
