# System Architecture: Private Collaborative Computing (FHE vs. SMPC)

## 1. Architectural Overview

The system models three mutually untrusting financial institutions (**Participant A: Retail Banking**, **Participant B: Commercial Treasury**, and **Participant C: Cross-Border Settlement**) that want to jointly evaluate aggregate financial analytics and multiplicative risk exposure over their private transaction partitions without disclosing raw datasets to one another or to an external third party.

The architecture strictly decouples the **OpenFHE Engine** and the **MP-SPDZ SMPC Engine** behind a unified **Benchmark Orchestrator**, **Network Simulation Engine**, and **Statistical Crossover Analysis Pipeline**.

```mermaid
flowchart TB
    subgraph Config["Configuration Layer (config/)"]
        EXP_CFG["experiment.yaml"]
        FHE_CFG["fhe.yaml"]
        SMPC_CFG["smpc.yaml"]
        NET_CFG["network.yaml"]
    end

    subgraph DataGen["1. Dataset Generator (dataset/)"]
        GEN["DatasetGenerator (PCG64 Seed=42)"]
        PA["Participant A Partition"]
        PB["Participant B Partition"]
        PC["Participant C Partition"]
        REF["PlaintextReferenceResult (64-bit Exact)"]
        GEN --> PA & PB & PC & REF
    end

    subgraph NetSim["4. Network Simulation Engine (networking/)"]
        TC["Linux tc / netem Qdisc Controller"]
        RTT["Live TCP_NODELAY RTT Verifier"]
        TMON["Kernel /proc/net/dev Traffic Monitor"]
        CLEAN["Deterministic Teardown & Reset"]
    end

    subgraph FHE_Pipe["2. OpenFHE Engine (fhe/)"]
        FHE_CTX["FHEContextManager (BFV-RNS / CKKS)"]
        FHE_KG["3-Party Collective Threshold KeyGen"]
        FHE_ENC["SIMD Packer (4096 slots) & TCP Transport"]
        FHE_EVAL["Homomorphic Evaluator (EvalAdd / EvalMult)"]
        FHE_DEC["3-Party Noise-Flooding Threshold Decryption"]
        FHE_NOISE["Noise Budget & Bootstrapping Analyzer"]
        FHE_CTX --> FHE_KG --> FHE_ENC --> FHE_EVAL --> FHE_DEC --> FHE_NOISE
    end

    subgraph SMPC_Pipe["3. MP-SPDZ Engine (smpc/)"]
        SMPC_COMP["Vectorized .mpc Bytecode Compiler"]
        SMPC_PREP["Timer 1: OT Beaver Triple Preprocessing"]
        SMPC_SHARE["Timer 2: 3-Party Secret Input Sharing"]
        SMPC_ONLINE["Timer 3: Online Beaver Multiplication & Sum"]
        SMPC_RECON["Timer 4: Authorized Output Reconstruction"]
        SMPC_COMP --> SMPC_PREP --> SMPC_SHARE --> SMPC_ONLINE --> SMPC_RECON
    end

    subgraph Orch["5-7. Benchmark Orchestrator & Storage (orchestrator/)"]
        RUN["BenchmarkRunner (Warm-up + 5 Repetitions)"]
        VAL["CrossEngineValidator & Trust Model Labeler"]
        MCOL["MetricsCollector (CSV / JSON Exporter)"]
        RUN --> VAL --> MCOL
    end

    subgraph Analytics["8-9. Analysis, Graphs & IEEE Report (analysis/ & reports/)"]
        STAT["Welch t-Test & Mann-Whitney U Comparator"]
        CX["Monte Carlo Crossover Threshold Detector"]
        GRAPHS["AcademicGraphGenerator (PNG + Vector PDF)"]
        IEEE["IEEEReportGenerator (LaTeX IEEEtran -> PDF)"]
        STAT --> CX --> GRAPHS --> IEEE
    end

    Config --> RUN
    PA & PB & PC --> FHE_ENC
    PA & PB & PC --> SMPC_SHARE
    NetSim <--> FHE_Pipe
    NetSim <--> SMPC_Pipe
    FHE_Pipe --> VAL
    SMPC_Pipe --> VAL
    REF --> VAL
    MCOL --> STAT
```

---

## 2. Execution Flow

1. **Configuration & Schedule Construction (`orchestrator/experiment_manager.py`)**:
   Loads `config/experiment.yaml`, `config/fhe.yaml`, `config/smpc.yaml`, and `config/network.yaml`, generating a deduplicated schedule across dataset sizes ($N \in [100, 100000]$), latency tiers ($1\text{ ms}$ to $200\text{ ms}$), bandwidth caps ($1\text{ Gbps}$ down to $1\text{ Mbps}$), and computational workloads (`aggregation` and `risk_multiplication`).
2. **Deterministic Dataset Partitioning (`dataset/generator.py`)**:
   Uses a seeded NumPy PCG64 generator (`seed: 42`) to synthesize $N$ financial transactions, partitions them disjointly across `participant_a`, `participant_b`, and `participant_c`, and computes exact 64-bit integer reference values.
3. **Kernel Network Shaping & RTT Verification (`networking/network_emulator.py`)**:
   Applies `tc qdisc replace dev <iface> root netem delay <D>ms rate <R>mbit limit <P>`, verifies qdisc activation, and measures live application-level TCP round-trip time prior to running benchmarks.
4. **OpenFHE Execution (`fhe/benchmark.py`)**:
   Initializes the BFV-RNS context ($p = 536903681, n = 16384$), runs 3-party interactive collective public key and evaluation key generation, packs each party's records into 4096-slot SIMD plaintexts, encrypts and transmits serialized ciphertexts over TCP sockets, executes `EvalAdd` and `EvalMult`, and performs 3-party distributed partial decryption with noise flooding (`MultipartyDecryptLead`, `MultipartyDecryptMain`, `MultipartyDecryptFusion`).
5. **MP-SPDZ Execution (`smpc/benchmark.py`)**:
   Purges any prior preprocessing files (preventing Beaver triple reuse), writes private participant inputs to `Player-Data/Input-P{0,1,2}-0`, compiles the vectorized `.mpc` program, and spawns three concurrent MP-SPDZ virtual machine processes (`P0`, `P1`, `P2`) over TLS sockets with four phase timers.
6. **Cross-Engine Validation & Statistical Persistence (`orchestrator/validation.py`, `orchestrator/metrics_collector.py`)**:
   Verifies that FHE, SMPC, and the plaintext reference agree identically, labels trust-assumption equivalence, and exports raw and aggregated statistics to `results/raw/` and `results/processed/`.
7. **Automated Crossover Analysis, Graphing, and Report Compilation (`analysis/`, `reports/`)**:
   Detects measured and interpolated crossover thresholds with 95% confidence bounds, generates all publication charts in PNG and PDF formats, and compiles the 5–6 page IEEE research paper via `pdflatex`.
