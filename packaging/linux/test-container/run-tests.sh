#!/bin/bash
# Inside the container: unpack the archived tree, run the backend tests, write JUnit and a summary to /out.
set -u
mkdir -p /work/tree && tar -x -C /work/tree < /dev/stdin
cd /work/tree && git init -q && git add -A && git -c user.email=test@localhost -c user.name=test commit -qm tree
cd /work/tree/services && ln -s /opt/venv .venv   # a repository of the archived tree: some tests ask git for the root
export MICROMIND_HOME=/work/mm-home MICROMIND_KEYCHAIN=off PYTHONDONTWRITEBYTECODE=1 CAMERA_NO_PHONE=1
mkdir -p "$MICROMIND_HOME"
uname -srm > /out/platform.txt; . /etc/os-release; echo "$PRETTY_NAME" >> /out/platform.txt
/opt/venv/bin/python -c "import sys, platform; print(sys.version.split()[0], platform.machine())" >> /out/platform.txt
/opt/venv/bin/python -m pytest -q -m "not timing" -p no:cacheprovider -n "${JOBS:-4}" -rfEs \
  --junitxml=/out/junit.xml ${PYTEST_ARGS:-} > /out/pytest.txt 2>&1
rc=$?
echo "pytest exit $rc" >> /out/pytest.txt
tail -3 /out/pytest.txt
exit $rc
