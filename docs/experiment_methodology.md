# Experimental Methodology and Metric Definitions

## 1. Independent and Joint Experimental Variables

The benchmark suite evaluates four structured experimental dimensions:

1. **Dataset Size Scaling (`dataset_scaling` & `dataset_scaling_wan`)**:
   - `small`: $100$ transactions ($34, 33, 33$ per participant)
   - `medium`: $1,000$ transactions ($334, 333, 333$ per participant)
   - `large`: $10,000$ transactions ($3334, 3333, 3333$ per participant)
   - `very_large`: $50,000$ transactions ($16667, 16667, 16666$ per participant)
   - `stress_test`: $100,000$ transactions ($33334, 33333, 33333$ per participant)
2. **Independent Network Latency Sweep (`latency_sweep`)**:
   - Bandwidth held constant at `high` ($1\text{ Gbps}$).
   - One-way propagation delay varied across:
     - `local`: $1\text{ ms}$ ($\text{RTT} \approx 2\text{ ms}$)
     - `fast_wan`: $10\text{ ms}$ ($\text{RTT} \approx 20\text{ ms}$)
     - `moderate_wan`: $50\text{ ms}$ ($\text{RTT} \approx 100\text{ ms}$)
     - `slow_wan`: $100\text{ ms}$ ($\text{RTT} \approx 200\text{ ms}$)
     - `high_latency`: $200\text{ ms}$ ($\text{RTT} \approx 400\text{ ms}$)
3. **Independent Network Bandwidth Sweep (`bandwidth_sweep`)**:
   - One-way delay held constant at `local` ($1\text{ ms}$) and `fast_wan` ($10\text{ ms}$).
   - Link capacity varied across:
     - `high`: $1000\text{ Mbps}$ ($1\text{ Gbps}$)
     - `medium`: $100\text{ Mbps}$
     - `restricted`: $10\text{ Mbps}$
     - `very_restricted`: $1\text{ Mbps}$
4. **Trust Model, Parameter Depth, and Isolated Bootstrapping Study**:
   - Compares `threshold_bfv_128` vs. `single_key_bfv_128` vs. `deep_bfv_128`, and `semi_dishonest_majority_3p` vs. `shamir_honest_majority_3p` vs. `malicious_shamir_3p`.
   - Executes an isolated CKKS bootstrapping benchmark (`bootstrapping_ckks_exp`) to measure `EvalBootstrapKeyGen` and `EvalBootstrap` latency independently.

---

## 2. Precise Metric Definitions

| Metric Column | Definition & Measurement Point |
|---|---|
| `setup_time_s` | **FHE**: `CryptoContext` initialization + 3-party collective public key and `EvalMultKey` generation (including TCP key exchange). **SMPC**: MP-SPDZ TLS session handshake and prime field parameter setup. |
| `preprocessing_time_s` | **FHE**: $0.0\text{ s}$ (no input-independent circuit triples required). **SMPC**: Timer 1 elapsed time to generate $N$ fresh Beaver multiplication triples via Oblivious Transfer. |
| `input_protect_time_s` | **FHE**: SIMD packing (`MakePackedPlaintext`), RLWE encryption (`Encrypt`), binary serialization (`Serialize`), and TCP upload of ciphertexts. **SMPC**: Timer 2 elapsed time for 3-party secret input sharing (`sint.get_input_from`). |
| `addition_time_s` | **FHE**: Cumulative time spent in `EvalAdd`. **SMPC**: Linear share summation time during Timer 3. |
| `multiplication_time_s` | **FHE**: Cumulative time spent in `EvalMult` (tensor product + relinearization). **SMPC**: Interactive Beaver triple opening ($d = x - a, e = w - b$) and linear evaluation during Timer 3. |
| `computation_time_s` | Total encrypted/secret-shared circuit evaluation time (`addition_time_s + multiplication_time_s`). |
| `output_recover_time_s` | **FHE**: 3-party distributed partial decryption (`MultipartyDecryptLead`, `MultipartyDecryptMain`), TCP transfer of partial ciphertexts, and fusion (`MultipartyDecryptFusion`). **SMPC**: Timer 4 final output share reconstruction (`reveal()`). |
| `online_time_s` | `input_protect_time_s + computation_time_s + output_recover_time_s`. |
| `total_execution_time_s` | End-to-end wall-clock execution time (`setup_time_s + preprocessing_time_s + online_time_s`). |
| `measured_network_traffic_bytes` | Actual kernel-level `tx_bytes` delta read from `/proc/net/dev` on the shaped interface (`lo`/`eth0`). Using `tx_bytes` (rather than `tx_bytes + rx_bytes`) avoids double-counting messages sent and received across the closed 3-party network. |
| `expansion_ratio` | Ratio of serialized cryptographic representation size (`crypto_payload_size_bytes`) to raw 64-bit integer input size (`plaintext_size_bytes`). |
| `peak_memory_mb` | Peak Resident Set Size (RSS) in MiB sampled across the executing process(es) via `psutil`. |
| `measured_rtt_ms` | Live application-level TCP round-trip time measured by a 64-byte `TCP_NODELAY` echo probe while `tc`/`netem` shaping is active. |

---

## 3. Statistical & Crossover Analysis Protocol

- **Warm-Up & Repetitions**: Each configuration performs warm-up run(s) followed by $R = 5$ measured repetitions by default. Warm-up runs are flagged with `is_warmup=True` and excluded from statistical summaries.
- **Summary Statistics**: For each metric, `MetricsCollector` computes the sample mean, median, sample standard deviation ($s$), minimum, maximum, and 95% Student-$t$ confidence interval ($\bar{x} \pm t_{0.975, R-1} \cdot s / \sqrt{R}$).
- **Crossover Detection**: Along each 1D sweep axis $x$, `CrossoverAnalyzer` evaluates $\Delta(x_k) = \bar{T}_{\text{FHE}}(x_k) - \bar{T}_{\text{SMPC}}(x_k)$. When adjacent measured points $x_k, x_{k+1}$ bracket a sign change ($\Delta(x_k) \cdot \Delta(x_{k+1}) \le 0$), the analyzer reports:
  1. The **Measured Bracketing Interval** $[x_k, x_{k+1}]$.
  2. The **Piecewise-Linearly Interpolated Threshold** $x^* = x_k - \Delta(x_k)(x_{k+1}-x_k)/(\Delta(x_{k+1})-\Delta(x_k))$.
  3. The **95% Uncertainty Interval** $[x^*_{\text{low}}, x^*_{\text{high}}]$ derived from 500 Monte Carlo resamples of the repeated trial distributions.
  4. When no sign change occurs within $[x_{\min}, x_{\max}]$, the analyzer explicitly reports `NO_CROSSOVER_OBSERVED_IN_TESTED_RANGE` without extrapolating.
