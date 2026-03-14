# ShadowChord: Global P2P DHT & File Streamer

ShadowChord is a fully decentralized, mathematically rigorous Peer-to-Peer (P2P) network built entirely from scratch in Python. It implements a modified version of the **Chord Distributed Hash Table (DHT)** protocol, capable of O(log N) routing, self-healing topology management, fault-tolerant data replication, and BitTorrent-style out-of-band file streaming.

This project was built to explore the physical and adversarial limits of the internet, successfully bridging local VMs, Host machines, and 5G Mobile Networks behind Carrier-Grade NATs via a Tailscale SD-WAN overlay.

---

## 🌟 Core Features

* **Logarithmic O(log N) Routing Engine:** Implements mathematically rigorous Chord Finger Tables to guarantee rapid, scalable peer discovery and query resolution, ensuring low latency even as the network scales to thousands of nodes.
* **Resilient Self-Healing Topology:** Dedicated background daemons handle continuous ring stabilization, graceful data handoffs, dead-node eviction, and active Split-Brain partition merging to survive extreme network churn.
* **Tunable Distributed Consistency:** Features a robust replication engine supporting dynamic selection between Eventual Consistency (high availability), Chain Replication (strict linearizability), and Quorum Consensus (fault-tolerant voting).
* **Cryptographic Anti-Entropy (Merkle Sync):** Nodes run continuous background reconciliation, exchanging Merkle Tree root hashes to enable O(1) detection and automatic resolution of data drift or corruption across replicas.
* **Bifurcated Out-of-Band Data Plane:** Strictly decouples lightweight DHT control traffic (JSON) from heavy payload delivery. Massive assets (e.g., 50GB files) are streamed directly peer-to-peer via memory-safe 64KB chunks over dedicated TCP data sockets.
* **Global WAN & NAT Resilience:** Architected to survive the hostile realities of the public internet. Successfully deployed across VMs, strict home firewalls, and 5G Carrier-Grade Symmetric NATs using Tailscale SD-WAN overlay integration.

---

## 📂 Architecture & Directory Structure

The codebase is strictly modularized into distinct domain managers to prevent circular dependencies.

```text
├── chord/                      # The core application package
│   │
│   ├── common/                 # Cryptography, logging, and utilities
│   │   ├── log.py              # Context-injected asynchronous logging
│   │   ├── merkle.py           # Merkle tree for anti-entropy sync
│   │   └── utils.py            # SHA-1 hashing and OS IP discovery
│   │
│   ├── dht/                    # The mathematical Chord logic
│   │   ├── handoff.py          # Data migration during topology shifts
│   │   ├── replication.py      # Eventual/Chain/Quorum consistency rules
│   │   ├── routing.py          # O(log N) Finger Table jump logic
│   │   ├── storage.py          # Thread-safe CRDT data storage
│   │   └── topology.py         # Ring join, depart, and stabilization
│   │
│   ├── network/                # The TCP / Transport layer
│   │   ├── dispatcher.py       # Routes incoming network commands
│   │   ├── streamer.py         # The Data Plane: Raw binary file streaming
│   │   └── tcp.py              # Framed TCP socket handler with backoff retries
│   │
│   ├── file_io.py              # Sandboxed file management (Path Traversal protection)
│   ├── node.py                 # The Facade: Wires all sub-modules together
│   └── state.py                # Centralized state (IP, Port, Finger Table)
│  
├── logs/                       # Auto-generated runtime logs per node
├── shared_files_<id>/          # Auto-generated sandboxes for uploaded/downloaded files
├── test/                       # Deprecated raw UDP/STUN/TCP NAT-busting experiments
├── cli.py                      # The interactive command-line interface
├── tailscale_ip.sh             # Utility script to find the current active Overlay IP
└── README.md
```

---

## 🚀 Setup & Execution

### 1. Prerequisites

* **Python 3.8+**
* **Tailscale (For Global WAN Testing):** To test this application across different networks (e.g., a home Wi-Fi and a 5G mobile network), you must bypass Carrier-Grade NATs.
* Install Tailscale on your host machine, VMs, and mobile devices to generate a flat `100.x.x.x` Overlay IP address space.

---
### 2. Launching the Swarm

The network requires a Bootstrap Node (the first node in the ring) to initialize the topology. Subsequent nodes use the Bootstrap Node, or other nodes that have successfully joined, to find their place in the ring.

**Start the Bootstrap Node (Node 1):**
On a Laptop or VM, the CLI will automatically detect your active network interface (or Tailscale IP).
```bash
python3 cli.py 5000

```

**Join the Ring (Node 2+):**
Provide your local port, followed by the IP and Port of ANY node already in the network.

```bash
python3 cli.py 5001 <ENTRY_IP> <ENTRY_PORT>

```

**📱 Android / Termux Note (Manual IP Override):**
Due to how the Android `VpnService` aggressively hides VPN network interfaces from unprivileged apps, auto-detection inside Termux may fall back to your local Wi-Fi IP instead of the Tailscale `100.x.x.x` IP.

This is totally fine. Because the CLI is built to accept manual IP overrides, you simply enforce your identity using the `--ip` flag. Check your Tailscale app for your device's IP, and run:

```bash
# To start a node on Android
python3 cli.py 5000 --ip 100.X.Y.Z

# To join an existing ring from Android
python3 cli.py 5001 --ip 100.X.Y.Z <ENTRY_IP> <ENTRY_PORT>

```


---

## 💻 CLI Commands

Once the node is running, you will be dropped into the interactive `ShadowChord >` shell.

| Command | Description |
| --- | --- |
| `help` | Lists all available commands. |
| `info` | Prints a comprehensive snapshot of the node's local state, k-factor, active neighbors, and finger table. |
| `overlay` | Executes a ring traversal to print the global network topology and data distribution across all connected nodes. |
| `announce <filename>` | Hashes a file from your local `shared_files` sandbox and advertises to the DHT that you are actively seeding it. |
| `download <filename>` | Queries the DHT for seeders, connects directly to a seeder's Data Port, and streams the file to your local sandbox. |
| `query <key>` | Looks up the IP addresses of nodes currently holding a specific key/file. Use `query *` to dump all DHT records. |
| `delete <key>` | Propagates a cryptographic Tombstone through the network to soft-delete a record. |
| `depart` | Executes a 2-Phase Commit (2PC) to hand off primary data to the successor before gracefully leaving the ring. |

---

## 🔬 Experimental Network Tests (`/test`)

The `test/` directory contains deprecated experiments documenting the physical limitations of modern ISPs. It includes pure Python implementations of raw UDP hole-punching and STUN client requests, demonstrating the mathematical impossibility of piercing 5G Symmetric Carrier-Grade NATs without external Relays (which justifies the use of the Tailscale SD-WAN overlay).

