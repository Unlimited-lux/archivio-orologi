"""Riepilogo di un file JUnit di pytest: totali e test non riusciti (anche nel sommario di GitHub)."""

import os
import sys
import xml.etree.ElementTree as ET

root = ET.parse(sys.argv[1]).getroot()
suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
tot = {k: sum(int(s.get(k, 0)) for s in suites) for k in ("tests", "failures", "errors", "skipped")}
passed = tot["tests"] - tot["failures"] - tot["errors"] - tot["skipped"]
lines = [f"**{passed} verdi**, {tot['failures']} falliti, {tot['errors']} errori, {tot['skipped']} saltati "
         f"(su {tot['tests']})", ""]
for case in root.iter("testcase"):
    for kind in ("failure", "error"):
        el = case.find(kind)
        if el is not None:
            msg = (el.get("message") or "").splitlines()[0][:200] if el.get("message") else ""
            lines.append(f"- {kind}: `{case.get('classname')}::{case.get('name')}` {msg}")
text = "\n".join(lines)
print(text)
summary = os.environ.get("GITHUB_STEP_SUMMARY")
if summary:
    with open(summary, "a", encoding="utf-8") as fh:
        fh.write(text + "\n")
