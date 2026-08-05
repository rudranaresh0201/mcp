FROM python:3.11-slim

# devmcp shells out to real git for every write_file/git_commit/git_branch
# call (see devmcp/src/devmcp/git_ops.py) -- python:3.11-slim doesn't
# include it by default.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir verimcp devmcp-server

# Mount a real local git repo at /repo (docker run -v /host/path:/repo)
# -- verimcp fronts devmcp, which operates on whatever's mounted here.
WORKDIR /repo
ENTRYPOINT ["verimcp", "--", "devmcp", "--repo-path", "/repo"]
