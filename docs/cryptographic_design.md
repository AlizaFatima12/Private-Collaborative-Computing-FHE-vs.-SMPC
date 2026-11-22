# Cryptographic Design: OpenFHE Threshold BFV-RNS and MP-SPDZ Secret-Sharing SMPC

## 1. Threat Model Alignment

A frequent methodological flaw in FHE vs. SMPC comparisons is contrasting single-key FHE against multi-party computation without aligning corruption thresholds. In a single-key FHE setting, whoever holds the secret key $sk$ can unilaterally decrypt any participant's input ciphertext unless protected by a separate hardware enclave or non-colluding decryptor.

To establish a rigorous apples-to-apples cryptographic comparison across three mutually untrusting organizations ($M = 3$), our primary experimental configuration aligns both technologies in the **semi-honest (passive) adversary model with dishonest majority ($t \le M - 1 = 2$ corrupted parties)**:

| Property | Primary FHE (`threshold_bfv_128`) | Primary SMPC (`semi_dishonest_majority_3p`) |
|---|---|---|
| **Library & Version** | OpenFHE v1.6.1.0 (`CCParamsBFVRNS`) | MP-SPDZ v0.4.4 (`semi-party.x`) |
| **Number of Parties** | $M = 3$ (`participant_a`, `participant_b`, `participant_c`) | $M = 3$ (`Player 0`, `Player 1`, `Player 2`) |
| **Adversary Model** | Semi-honest with `NOISE_FLOODING_MULTIPARTY` decryption | Semi-honest (passive) |
| **Corruption Threshold** | Dishonest majority ($t \le 2$ of $3$; 3-out-of-3 threshold decryption) | Dishonest majority ($t \le 2$ of $3$; additive shares over $\mathbb{F}_p$) |
| **Input Protection** | RLWE Encryption under Joint Public Key $pk_{ABC}$ | Additive Secret Sharing $[x] = x_0 + x_1 + x_2 \bmod p$ |
| **Multiplication Mechanism** | Homomorphic Tensor Product + Collective Relinearization (`EvalMult`) | Beaver Multiplication Triples $([a],[b],[c])$ via Oblivious Transfer |

In addition to the primary aligned configuration, the framework includes clearly separated secondary profiles:
- `single_key_bfv_128`: Single-key BFV-RNS baseline (explicitly labeled `NON_EQUIVALENT_TRUST_MODEL`).
- `shamir_honest_majority_3p`: 3-party Shamir polynomial secret sharing (`shamir-party.x`, honest majority $t \le 1$ of $3$).
- `malicious_shamir_3p`: 3-party maliciously secure Shamir secret sharing with Beaver triple sacrifice (`malicious-shamir-party.x`).
- `bootstrapping_ckks_exp`: Isolated experimental CKKS bootstrapping benchmark (`EvalBootstrapSetup`, `EvalBootstrapKeyGen`, `EvalBootstrap`).

---

## 2. FHE Parameter Selection and Noise Growth Management

### 2.1 Ring Dimension, Plaintext Modulus, and SIMD Packing
In OpenFHE BFV-RNS (`threshold_bfv_128`):
- **Security Level**: `HEStd_128_classic` ($\ge 128$-bit classical lattice security per the HomomorphicEncryption.org standard).
- **Plaintext Modulus**: $p = 536,903,681$, a 29-bit NTT-friendly prime satisfying $p \equiv 1 \pmod{65536}$.
  - Signed plaintext range: $[-\lfloor p/2 \rfloor, \lfloor p/2 \rfloor] = [-268,451,840, +268,451,840]$.
  - Even for the largest stress-test dataset ($N = 100,000$ transactions with $x_i \in [10, 200]$ and $w_i \in [1, 10]$), the maximum possible inner product is $100,000 \times 200 \times 10 = 200,000,000 < 268,451,840$, guaranteeing exact integer arithmetic with zero modular overflow.
- **Ring Dimension & Moduli**: With `SetMultiplicativeDepth(2)`, `SetThresholdNumOfParties(3)`, and `SetMultipartyMode(NOISE_FLOODING_MULTIPARTY)`, OpenFHE selects ring dimension $n = 16,384$ (cyclotomic order $M = 32,768$) and composite RNS ciphertext modulus $\log_2 Q \approx 240$ bits across 60-bit CRT primes (`HPSPOVERQLEVELED`).
- **SIMD Batch Size**: $4096$ integers per ciphertext.

### 2.2 Noise Budget and Collective Decryption
Fresh RLWE encryption adds Gaussian noise $e_0$ ($\sigma = 3.2$). Homomorphic addition increases noise norm additively, while depth-1 homomorphic multiplication (`EvalMult`) scales noise by $\approx O(p \sqrt{n})$. Under `NOISE_FLOODING_MULTIPARTY`, Party A (`MultipartyDecryptLead`) injects a statistically large flooding noise term during partial decryption so that partial decryptions do not leak secret key information via residual circuit noise. Across both workloads, over $150$ bits of decryption noise margin remain after evaluation.

---

## 3. SMPC Secret Sharing and Beaver Triple Multiplication

### 3.1 Additive and Shamir Secret Sharing over $\mathbb{F}_p$
In `semi-party.x` compiled with `-F 64`, MP-SPDZ operates over a 128-bit prime field $\mathbb{F}_p$ ($p = 170141183460469231731687303715885907969$, 16 bytes per field element). Each secret integer $x$ is represented across the 3 parties as $([x]_0, [x]_1, [x]_2) \in \mathbb{F}_p^3$ such that $[x]_0 + [x]_1 + [x]_2 \equiv x \pmod p$. Any subset of up to 2 parties holds uniformly random field elements independent of $x$.

### 3.2 Separated Preprocessing and Online Beaver Multiplication
For Workload 2 (`risk_multiplication`), our compiled `.mpc` program explicitly separates the four protocol phases using MP-SPDZ timers:
1. **Timer 1 (Preprocessing Phase)**: Provisions $N$ fresh Beaver multiplication triples $([a_k], [b_k], [c_k])$ via `sint.get_random_triple(size=...)` where $c_k = a_k \cdot b_k \bmod p$ is generated using pairwise Oblivious Transfer (OT).
2. **Timer 2 (Input Sharing Phase)**: Each participant secret-shares its transaction amounts $x_k$ and risk weights $w_k$ via `sint.get_input_from(p_idx, size=...)`.
3. **Timer 3 (Online Beaver Multiplication & Summation Phase)**:
   Opens the masked differences $d_k = \text{reveal}([x_k] - [a_k])$ and $e_k = \text{reveal}([w_k] - [b_k])$ and evaluates the linear Beaver identity:
   $$[x_k \cdot w_k] = [c_k] + d_k \cdot [b_k] + e_k \cdot [a_k] + d_k \cdot e_k \pmod p$$
   followed by local share summation $\sum_{k=1}^N [x_k \cdot w_k]$.
4. **Timer 4 (Output Reconstruction Phase)**: Reconstructs only the authorized final scalar aggregate via `reveal()`.

Before every execution, `SMPCParticipantManager.purge_preprocessing_material()` wipes all prior preprocessing files in `Player-Data/`, strictly enforcing one-time Beaver triple consumption.
