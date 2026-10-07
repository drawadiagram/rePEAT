"""The enzyme-redesign protocol: a staged, human-checkpointed campaign on `hpc`.

Pure helpers only. Nothing here reaches the outside -- the stage machine in
`graph/nodes/protocol.py` holds the `Deps` and does the submitting, and these
modules turn validated inputs into job specs, notebook entries and residue
arithmetic. That split is what lets the offline suite prove the risky parts: a
job spec is a dict, so it can be asserted on without an endpoint, and the
generated shell can be run with `bash` against a `tmp_path`.
"""
