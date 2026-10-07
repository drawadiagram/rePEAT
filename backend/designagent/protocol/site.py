"""Where the protocol's files and tools live at the far end.

Every path here names something on the cluster that only the site knows: a
project root under an allocation, a conda environment, a sequence database, a
container module. They arrive as environment-only settings for the same reason
`mpnn_command` does -- each one names a path or a shell word the server will run
on the endpoint, under the site's allocation -- so none of them is remotely
writable (`tests/test_settings.py::NOT_REMOTELY_WRITABLE`).

Kept as a value object rather than passed as a `Settings` so the spec builders
in `protocol/specs.py` stay pure functions of small inputs: a builder that took
the whole `Settings` would be awkward to test and would invite reading fields
that have nothing to do with the far end.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .inputs import InvalidInput, ProtocolInputs

# The subdirectories the protocol expects under `$PROJ`, in the layout the
# skill's own scripts assume. `conservation/` holds the HHblits scripts and
# their output, `mpnn/pdb/` the backbone ProteinMPNN reads, and so on.
PROJECT_SUBDIRS = (
    "conservation",
    "mpnn",
    "mpnn/pdb",
    "analysis",
    "af3",
    "alignments",
)


@dataclass(frozen=True)
class SiteLayout:
    """Absolute paths and module lines for one cluster."""

    proj_root: str
    scratch_root: str = "/scratch"
    conda_aifold: str = ""
    conda_analysis: str = ""
    mpnn_path: str = ""
    mpnn_weights: str = ""
    uniref_db: str = ""
    # Lines run before the AlphaFold3 container. The module sets $CONTAINERDIR,
    # $ALPHAFOLD_MODELWEIGHTS and $ALPHAFOLD_DATA_PATH, which the job then binds.
    af3_modules: tuple[str, ...] = ()
    af3_image: str = "alphafold3.sif"
    # Scheduler words with no PSI/J field; they ride in `custom_attributes`.
    gpu_queue: str = "gpu"
    gpu_constraint: str = ""
    required: tuple[str, ...] = field(
        default=(
            "proj_root",
            "scratch_root",
            "conda_aifold",
            "conda_analysis",
            "mpnn_path",
            "uniref_db",
        ),
        repr=False,
    )

    def __post_init__(self) -> None:
        # A relative project root would make a job's `--chdir` resolve against
        # the endpoint process's own working directory, which nothing here can
        # know -- the same class of bug as `LocalOrbitStack`'s doubled `--cert`
        # path. Absolute or nothing.
        for name in ("proj_root", "scratch_root"):
            value = getattr(self, name)
            if value and not value.startswith("/"):
                raise InvalidInput(
                    f"{name} must be an absolute path on the cluster, not {value!r}: "
                    f"a relative one resolves against the endpoint's own working "
                    f"directory"
                )

    def missing(self) -> tuple[str, ...]:
        """Required settings that are still empty.

        Checked at stage entry so the refusal names what to set, rather than a
        job failing at the far end with a path that was never configured.
        """
        return tuple(name for name in self.required if not getattr(self, name))

    def proj(self, inputs: ProtocolInputs) -> str:
        """The campaign's project directory, `$PROJ` in the skill's terms."""
        return f"{self.proj_root.rstrip('/')}/{inputs.name}"

    def sub(self, inputs: ProtocolInputs, *parts: str) -> str:
        return "/".join([self.proj(inputs), *parts])

    @classmethod
    def from_settings(cls, settings: Any) -> "SiteLayout":
        """Read the far end out of `Settings`, without importing it.

        Untyped on purpose: `protocol/` sits below the app's configuration and
        has no business importing it, and the only thing this needs is attribute
        access. The field names are the contract, and `CREDENTIAL_FIELDS`
        guarantees every one of them is reported.
        """
        return cls(
            proj_root=settings.protocol_proj_root,
            scratch_root=settings.protocol_scratch_root,
            conda_aifold=settings.protocol_conda_aifold,
            conda_analysis=settings.protocol_conda_analysis,
            mpnn_path=settings.protocol_mpnn_path,
            mpnn_weights=settings.protocol_mpnn_weights,
            uniref_db=settings.protocol_uniref_db,
            af3_modules=settings.af3_modules,
            af3_image=settings.protocol_af3_image,
            gpu_queue=settings.protocol_gpu_queue,
            gpu_constraint=settings.protocol_gpu_constraint,
        )

    def af3_scratch(self, inputs: ProtocolInputs) -> str:
        """Where AlphaFold3's inputs and outputs live.

        On scratch, not in the project: AF3 writes gigabytes per design, and the
        skill is explicit that large outputs stay off `/projects`.
        """
        return f"{self.scratch_root.rstrip('/')}/{inputs.netid}/af3/{inputs.name}"
