"""MP-SPDZ Secure Multi-Party Computation Engine for Private Collaborative Computing."""

from smpc.protocol_config import SMPCProtocolConfig, SMPCProtocolSpec
from smpc.participant import SMPCParticipantManager, ParticipantInputFiles
from smpc.secret_sharing import SecretSharingInspector
from smpc.beaver_multiplication import BeaverTripleAnalyzer
from smpc.computation import SMPCProgramCompiler, SMPCExecutionMetrics
from smpc.benchmark import SMPCBenchmarkEngine

__all__ = [
    "SMPCProtocolConfig",
    "SMPCProtocolSpec",
    "SMPCParticipantManager",
    "ParticipantInputFiles",
    "SecretSharingInspector",
    "BeaverTripleAnalyzer",
    "SMPCProgramCompiler",
    "SMPCExecutionMetrics",
    "SMPCBenchmarkEngine",
]
