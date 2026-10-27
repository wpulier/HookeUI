"""Separate local supported contracts from scientific distribution qualification.

Private preparation remains available to development workspaces. An installer
receipt or an author's proof cannot establish missing interactive qualification.
"""
from gpu_worker import manifests as catalog


def integration_problem(m: catalog.Manifest) -> str | None:
    if m.members:
        from gpu_worker.bundle_contract import parse_members

        if any(member.bridge == "scientific-host-v1" for member in parse_members(list(m.members), m)):
            return ("This is a private scientific-host candidate awaiting workspace and independent qualification. "
                    "Keep it in the development workspace until its intended workflow has been qualified. "
                    "Reinstalling cannot complete this integration.")
    return None


def scientific(m: catalog.Manifest) -> bool:
    """Classification only; never evidence of preparation, proof or consent."""
    if not m.members:
        return False
    from gpu_worker.bundle_contract import parse_members
    return any(member.bridge == 'scientific-host-v1' for member in parse_members(list(m.members), m))


def local_reader(m: catalog.Manifest) -> bool:
    """Only the complete supported reader contract; mixed/unchecked tools stay private."""
    if not m.members:
        return False
    from gpu_worker.bundle_contract import parse_members
    members = parse_members(list(m.members), m)
    return bool(members) and all(member.bridge == 'scientific-host-v1' and member.reader
                                and member.reader_checks for member in members)


def local_problem(m: catalog.Manifest) -> str | None:
    """Structural local eligibility. Existing trust, setup, proof and consent still apply."""
    return None if local_reader(m) or local_workflow(m) else integration_problem(m)


def local_workflow(m: catalog.Manifest) -> bool:
    """Only the existing bounded interactive scene contract; all members individually observed."""
    if not m.members:
        return False
    from gpu_worker.bundle_contract import parse_members
    from .scientific_tools import accepts
    members = parse_members(list(m.members), m)
    return bool(members) and all(accepts(member) and len(member.outputs) == 1
                                and member.outputs[0].type == 'project.scientific' for member in members)
