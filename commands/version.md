---
description: Which commit of dear-guide this machine is running
allowed-tools: Bash(dg --version:*)
---

!`dg --version`

That is the commit the installed `dg` was built from — beta has no version
number, on purpose, since one that has to be bumped per change is one that gets
skipped. A trailing `-dirty` means the checkout it was installed from has
uncommitted changes to tracked files, so the hash names the last commit rather
than the code actually running.

To tell whether it is current, compare it with the checkout it came from:
`git -C /path/to/dear-guide log -1 --format=%h`. An editable install (`pip
install -e`) is that checkout, so the two differ only when the repository has
been pulled and the shell still holds an old process.

$ARGUMENTS
