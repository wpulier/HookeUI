"""Compatibility imports for the shared scientific resource contract."""
from gpu_worker.semantic_resources import (  # noqa: F401
    FORMAT, VERSION, MAX_BYTES, MAX_CONTRACT_BYTES, MAX_CONTRACT_NODES,
    MAX_CONTRACT_DEPTH, MAX_SAME_AS, SEMANTICS, FIELDS, IDENTITY, ROLE,
    InvalidDescriptor, Descriptor, SameAs, Contract, Mismatch,
    parse_descriptor, parse_contract, parse_reference, validate,
)
