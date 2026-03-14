# ShadowChord: Decentralized Privacy-Preserving Messaging Architecture

## Abstract
ShadowChord is a zero-trust, privacy-preserving messaging protocol operating over a bifurcated Distributed Hash Table (DHT). Designed to resist surveillance by global passive adversaries and active network exploitation, the protocol completely abstracts away IP-based routing in favor of cryptographic identities. By combining 3-hop onion routing, Signal-style perfect forward secrecy (Double Ratchet), Hashcash-style Sybil resistance, and chronological offline swarm storage, ShadowChord guarantees secure, anonymous, and asynchronous communication across hostile network environments (e.g., strict firewalls and 5G NATs) without reliance on centralized infrastructure.

---

## 1. Cryptographic Identity & Primitives
To guarantee user anonymity and secure transport, ShadowChord strictly removes IP addresses from the application layer. Node and user identities are defined entirely by their cryptographic keys, enforcing a zero-trust architecture.

* **Identity Protocol (Ed25519):** User sessions and node addresses are derived from Ed25519 public key pairs. A node's position on the Chord ring (its Node ID) is no longer a hash of its IP address, but rather the hash of its permanent Ed25519 Public Key. This decouples identity from physical location.
* **Hashing (BLAKE3):** The protocol standardizes on BLAKE3 for all internal hashing operations due to its extreme performance and resistance to length-extension attacks. The Chord ring operates in a $2^{256}$ keyspace. 
* **Key Exchange (X25519):** Ephemeral and static key agreements utilize X25519. When two nodes establish a TCP connection, they perform an immediate Diffie-Hellman handshake to generate a shared session key, ensuring Perfect Forward Secrecy (PFS) for all transport links.
* **Symmetric Encryption (XChaCha20-Poly1305):** All payload data is end-to-end encrypted and authenticated using XChaCha20-Poly1305. This cipher is chosen over AES-GCM to prevent hardware-dependent side-channel attacks on low-end mobile devices and to safely accommodate the random nonces required by the stateless UDP/Onion routing layers.

## 2. Network Topology & Architecture
The protocol utilizes a modified, Two-Tiered (Bifurcated) Chord DHT to handle peer discovery and routing while accommodating standard consumer network constraints (e.g., Carrier-Grade NAT, strict firewalls, and battery-limited mobile devices).

### 2.1 Full Nodes (Supernodes)
* **Role:** Operate on devices with globally routable IPs or explicitly forwarded ports (e.g., cloud servers, home servers, or overlay-connected laptops).
* **Responsibilities:** Actively participate in the DHT ring, maintain Finger Tables, stabilize the network, and store key-value data. 
* **Proxying:** Supernodes maintain a registry of persistent TCP sockets for connected Light Clients, acting as inbound message relays for NAT-bound devices.

### 2.2 Light Clients (Edge Nodes)
* **Role:** Operate behind strict NATs (e.g., 5G mobile devices, restricted corporate networks) without joining the core routing ring.
* **Responsibilities:** Maintain a single, persistent, outbound TCP connection to a random or geographically close Full Node (Supernode).
* **Resource Management:** Do not perform periodic `stabilize` or `fix_fingers` operations, preserving battery and bandwidth.

## 3. Routing & Peer Discovery
* **Logarithmic Routing:** Full Nodes implement standard Chord Finger Tables, allowing requests to skip mathematically across the ring, reducing routing complexity to $O(\log N)$ and minimizing message latency.
* **Stateless Edge Routing:** Light Clients push routing queries (e.g., `find_successor`) up their persistent TCP connection. The attached Supernode resolves the query within the DHT on behalf of the client and returns the result.

## 4. NAT Traversal & Connectivity Strategy
To achieve global connectivity across hostile network environments, the system employs a cascading NAT traversal strategy:
1.  **Direct Connection:** Attempt standard TCP connections.
2.  **UPnP Negotiation:** Programmatically request port forwarding from local Internet Gateway Devices (routers).
3.  **STUN/ICE Hole-Punching (Future UDP Layer):** Utilize STUN to discover external IPs and attempt UDP hole-punching.
4.  **Persistent Relay (The Core Fallback):** If Symmetric NAT (e.g., mobile 5G CGNAT) blocks direct traversal, Light Clients fall back to using their persistent outbound TCP socket. Because the client initiated the connection, the NAT firewall leaves the channel open for the Supernode to push asynchronous messages down to the client.

## 5. Swarm Storage & State Management (The Offline Mailbox)
Given the inherent churn and unreliability of volunteer P2P networks, ShadowChord operates as a temporary, asynchronous offline mailbox system rather than a permanent ledger. If a user is offline, the DHT securely holds their encrypted messages until they reconnect, ensuring guaranteed delivery without centralized databases.

### 5.1 Chronological Data Structures (Hash Chaining)
* **Mechanism:** The DHT storage engine appends incoming encrypted JSON message blocks to sequential lists stored under the receiver's hashed cryptographic identity.
* **Execution:** To prevent malicious nodes from reordering, omitting, or replaying messages, each block explicitly references the cryptographic hash (BLAKE3) of the preceding message. This creates a localized, tamper-proof hash chain. When the recipient downloads their mailbox, their client mathematically verifies the chain to guarantee the absolute chronological integrity of the conversation.

### 5.2 Automated Data Purging (TTL Daemons)
* **Mechanism:** To prevent memory and disk exhaustion across the swarm, and to limit the cryptographic exposure of stored ciphertexts, all nodes enforce a strict Time-To-Live (TTL) policy on stored data.
* **Execution:** A background garbage collection daemon autonomously sweeps the local storage dictionary and purges message blocks after a predefined lifecycle (e.g., 14 days). This ensures the DHT remains lightweight and functions purely as a transit and temporary holding layer, not an archival database.

## 6. Privacy & Anonymity Engine (ShadowChord)
To protect against metadata analysis, ISP surveillance, and traffic correlation, the network enforces strict transport-layer obfuscation natively within the routing logic.

### 6.1 3-Hop Onion Routing
* **Mechanism:** Payloads are wrapped in three distinct layers of encryption corresponding to three randomly selected Supernodes (Entry, Middle, and Exit).
* **Execution:** Light Clients perform the CPU-intensive encryption locally. Intermediate Supernodes act as blind relays, stripping a single cryptographic layer before forwarding the payload. 
* **Security Guarantee:** No single node in the routing path possesses both the sender's IP address and the final destination/plaintext payload.

### 6.2 Uniform Packet Sizing
* **Mechanism:** To mitigate heuristic traffic analysis, the message dispatcher pads all network payloads with cryptographically secure random bytes to a strict, fixed size (e.g., 4KB blocks).
* **Execution:** Large files are fragmented into uniform blocks. Tiny text messages are padded up to the block limit. Observers viewing the wire data see only identical, monolithic data blocks moving between nodes.

### 6.3 Covert Chaff Traffic
* **Mechanism:** Idle Light Clients and Supernodes continuously generate and route dummy, heavily encrypted onion packets at a constant, randomized rate.
* **Execution:** This creates a permanent baseline of background noise across the entire Chord ring. When a real message is dispatched, it replaces a scheduled chaff packet. 
* **Security Guarantee:** Prevents adversaries from executing timing attacks (correlating a burst of outbound traffic from Alice with a burst of inbound traffic to Bob).

## 7. Perfect Forward Secrecy (The Double Ratchet)
To ensure that the long-term compromise of a static Ed25519 identity key does not expose historical ciphertexts, the protocol enforces strict, dynamic key rotation for every single message cycle.

### 7.1 The Double Ratchet Algorithm
* **Mechanism:** ShadowChord implements a Signal-style Double Ratchet algorithm managed entirely client-side. The shared symmetric key used for XChaCha20-Poly1305 encryption is never used more than once.
* **Execution:** After every message sent or received, the client's internal state mathematically "ratchets" forward through a one-way Key Derivation Function (KDF). Because the KDF is a one-way cryptographic hash, an attacker who steals the current key cannot run the math backward to discover yesterday's key.

### 7.2 Ephemeral Rotation in Swarm Storage
* **Mechanism:** Every encrypted JSON block stored in the swarm includes a new, plaintext ephemeral public key attached to the header.
* **Execution:** When Alice sends a message to Bob, she includes a newly generated ephemeral public key. When Bob replies, he uses Alice's new ephemeral key to perform a fresh Diffie-Hellman handshake, generating a completely new root key for the next ratchet cycle. This ensures that encryption keys are continuously and mathematically rotated with every back-and-forth communication cycle, self-healing the encryption channel even if a temporary compromise occurs.

## 8. Sybil & Abuse Resistance (Cryptographic Proof-of-Work)
To protect the volunteer network from distributed denial-of-service (DDoS) attacks, storage spam, and Sybil node generation, ShadowChord imposes a strict computational cost on network write operations.

### 8.1 Hashcash-Style Proof-of-Work (PoW)
* **Mechanism:** Before broadcasting an `insert` request to the DHT, the originating Light Client must compute a Hashcash-style Proof-of-Work. 
* **Execution:** The client must repeatedly hash the payload alongside a random nonce and a current timestamp until the resulting hash falls below a network-defined difficulty threshold. While this computation takes only a few seconds on a standard smartphone (an acceptable delay for human messaging), it makes automated mass-spamming computationally bankrupting for an attacker.

### 8.2 The Validation Standard
* **Mechanism:** The cryptographic validation equation is defined as:
  $H(\text{nonce} \parallel \text{timestamp} \parallel \text{payload}) < \text{target}$
* **Execution:** When a Supernode receives a request to store a message block, it acts as a cryptographic bouncer. It performs a single, $O(1)$ hash execution using the provided nonce. If the resulting hash does not satisfy the target difficulty equation, or if the timestamp is too far skewed from the current network time (preventing pre-computation attacks), the Supernode instantly drops the packet before it consumes local disk space or network bandwidth.

## 9. Execution Flow: Anonymous P2P Messaging
The system enables seamless, darknet-style messaging between two Light Clients (Alice and Bob) separated by strict NATs, even if they are not online at the same time:

1.  **Identity Derivation & Registration:** Bob derives his logical network address by computing `BLAKE3(Bob_PubKey)`. He connects to Supernode B and inserts a rendezvous record into the DHT, linking his cryptographic identity to his active persistent TCP socket.
2.  **Payload Construction (Ratchet & Chain):** Alice decides to message Bob. Her client constructs a JSON payload containing her ciphertext, a fresh ephemeral public key (for the Double Ratchet), and the BLAKE3 hash of their previous message (Hash Chaining).
3.  **Proof-of-Work & Obfuscation:** Alice’s client computes the required Hashcash-style PoW. The validated block is padded to a strict 4KB size, wrapped in three layers of XChaCha20 encryption (Onion creation), and pushed up to her attached Supernode A.
4.  **Transit & Validation:** The payload hops blindly through the three designated Supernodes (Entry, Middle, Exit). The Exit Node executes a pure Chord $O(\log N)$ lookup to route the payload to the Supernode mathematically responsible for Bob's address. That target node cryptographically validates the PoW and timestamp before accepting the write.
5.  **Storage & Edge Delivery:** The valid 4KB block is appended to Bob's offline mailbox. If Bob is currently online, the DHT resolves his active rendezvous point and Supernode B instantly pushes the block through the NAT to his phone. 
6.  **Decryption & State Update:** Bob's device decrypts the payload, verifies the chronological hash chain to ensure no messages were dropped, and uses Alice's ephemeral key to ratchet his local encryption state forward.

## 10. Protocol Enhancements (Active Defense)
The following mechanisms are slated for integration into the core C-implementation to harden the network against active exploitation and expand media capabilities:

* **Transport Security:** Implementation of the Noise Protocol Framework for mutually authenticated, encrypted handshakes between connecting TCP nodes, ensuring transport-layer security before application data is ever exchanged.
* **Secure File Handling:** A cryptographic chunking mechanism to reconstruct large media files across multiple 4KB uniform packets, allowing users to share images and audio without compromising the uniform-sizing anonymity constraints.
* **Network Defense:** Hardening the routing logic to mitigate Eclipse attacks (malicious peer isolation) and DHT poisoning (garbage injection into user swarms) via strict peer validation and routing table diversity checks.
* **Onion Forward Secrecy:** Upgrading the 3-hop circuit creation to utilize ephemeral keys for the relay layers, ensuring that the compromise of a relay node's static key cannot unmask historical traffic circuits.
* **Reputation Mechanics:** A decentralized, zero-knowledge node reputation system to safely deprecate and bypass malicious, Byzantine, or underperforming relays without relying on a central authority.
