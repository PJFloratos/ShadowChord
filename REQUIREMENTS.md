# ShadowChord: A Bifurcated, Zero-Trust Messaging Overlay

ShadowChord is a bifurcated, zero-trust messaging overlay engineered to survive hostile network environments and Carrier-Grade NAT. By partitioning the swarm into structural Supernode pillars and invisible Light Client guests, the protocol enforces a strict $O(N)$ hop-by-hop topology that prioritizes absolute topological anonymity over routing speed. It integrates asynchronous DAG-based storage with memory-hard cryptographic friction to ensure secure, store-and-forward delivery and traffic analysis resistance within a completely decentralized, peer-to-peer environment.


---


## 0. Network Topology & Architecture (The Bifurcated Model)

The protocol utilizes a Two-Tiered (Bifurcated) Chord DHT to handle peer discovery and routing. This architecture explicitly accepts the reality of modern consumer network constraints—specifically Carrier-Grade NAT (CGNAT), strict mobile firewalls, and battery limitations—by dividing the network into two distinct node classes based on automated environment detection.

### 0.1. Full Nodes (Supernodes / The Pillars)
**Role:** Operate on hardware profiles like Cloud VPS, Home Laptops, or Desktops. They possess a globally routable IPv4 address or explicit router-level port forwarding.
**Responsibilities:** Because they are "Inbound-Open" their role is to bind to listening ports (e.g., `0.0.0.0:5000`), actively route traffic across the ring, and store the DAG mailboxes for offline users.
**Proxying:** Supernodes maintain a registry of persistent TCP sockets for connected Light Clients, acting as the static, reachable infrastructure that NAT-bound devices can securely latch onto.

### 0.2. Light Clients (Edge Nodes / The Guests)
**Role:** Operate on battery-constrained or heavily firewalled hardware, such as 5G Mobile Phones (Termux) or restricted corporate networks. They are "Inbound-Blind" and trapped behind strict Carrier-Grade NATs (CGNAT) or symmetric firewalls.
**Responsibilities:** They never bind to a local listening port. Instead, they maintain a single, persistent, outbound TCP connection to a known Supernode.
**Resource Management:** They do not participate in storing external data or running periodic background stabilization loops, heavily preserving mobile battery, bandwidth, and CPU cycles.

### 0.3. Routing & Peer Discovery
* Standard logarithmic routing (Finger Tables) has been completely removed. By stripping the network of long-distance shortcuts, routing efficiency drops to $O(N)$. This is an explicit architectural trade-off: sacrificing speed to guarantee mathematical topological anonymity. A Supernode only ever opens TCP connections to its immediate predecessor and successor on the ring. To the rest of the swarm, a node's physical IP address is entirely invisible.
* Light Clients push their routing queries (e.g., `find_successor`) up their persistent TCP connection. The attached Supernode resolves the query within the broader DHT on behalf of the client, fetches the requested data, and pushes it back down the open pipe.

### 0.4. NAT Traversal & Connectivity Strategy
In the post-IPv4 exhaustion era, legacy traversal methods like UPnP, STUN, and UDP hole-punching are fundamentally broken on cellular networks. ShadowChord discards complex hole-punching in favor of deterministic Outbound-Initiation.

#### 0.4.1. The Carrier-Grade NAT (CGNAT) Barrier
Modern mobile ISPs use Carrier-Grade NAT to share a single public IPv4 address among thousands of mobile handsets. To the outside internet, a 5G phone is completely invisible. Furthermore, cellular NATs are *Symmetric*, meaning they randomize external ports for every new connection, rendering traditional inbound "door-propping" techniques mathematically impossible.

#### 0.4.2. Bypassing CGNAT via Outbound-Initiation (The Persistent Bridge)
Rather than attempting to punch inbound holes through an industrial ISP firewall, Light Clients exploit the fundamental rule of all stateful firewalls: **Outbound traffic is always trusted.**
1. **The Outbound Handshake:** The Light Client initiates a standard TCP connection outward to a Supernode's fixed Public IP and Port.
2. **Stateful Firewall Tracking:** When the TCP SYN packet leaves the mobile network, the ISP's NAT dynamically creates a temporary mapping in its routing table, leaving this specific connection open so the Supernode can reply.
3. **The Persistent TCP Relay:** The Light Client keeps this TCP socket alive indefinitely. The Supernode now has a guaranteed, bidirectional, open pipe directly through the CGNAT.
4. **Asynchronous Push:** When a message arrives for the mobile user, the Supernode does not search for the phone's IP; it simply pushes the encrypted block directly down the already-established outbound socket.

### 0.5. Supernode Deployment & Practical Networking
Running a Supernode requires explicitly opening the host network to the public internet via Port Forwarding. This exposes the node to the reality of the global internet, requiring strict operational security and network diagnosis.

#### 0.5.1 Port Forwarding Security Posture
Opening a port is like unlocking a specific window on a house while keeping the front door locked. Security rests entirely on the application listening behind that port.
* **The Internet Background Noise:** The moment Port 5000 is opened, the node will be hit by automated scanner bots (Shodan, Censys). The application must cleanly handle, drop, or ignore malformed packets without crashing to prevent accidental Denial of Service (DoS).
* **Application-Level Exploits:** To prevent Remote Code Execution (RCE) or Buffer Overflows, the node must never use unsafe deserialization (e.g., `pickle.loads()`, `eval()`). Strict `json.loads()` inside `try/except` blocks is mandatory, alongside hard limits on incoming payload sizes.
* **Execution Privileges:** The Supernode daemon must never run as Admin/Root. It must operate as an unprivileged user to prevent lateral movement across the host network in the event of an application compromise.
* **Rule Management:** When active testing or hosting concludes, port forwarding rules should be managed carefully. A "Soft Disable" (unchecking the rule in the router without deleting it) is recommended to safely close the perimeter while preserving configurations for future use.

#### 0.5.2 Discovering the True IPv4 Address & CGNAT Detection
To successfully route traffic to a Supernode, the Light Client must connect to the correct protocol address. Due to ISP configurations, relying on standard lookup tools can yield false positives.

* **The IPv6 Mix-Up:** Many modern ISPs dual-stack IPv6 and IPv4. Standard IP lookup commands (like `curl ifconfig.me`) may return an IPv6 address (e.g., `2a02:586...`), which will fail to connect over an IPv4-configured port forward. Supernode operators must force IPv4 resolution using: `curl -4 ifconfig.me`
* **Detecting CGNAT:** If a Supernode is hosted on a residential connection, it may be trapped behind an ISP-level CGNAT. To verify, the operator must compare the IP returned by `curl -4 ifconfig.me` with the "WAN IP" or "IPv4 Address" listed on their router's status page. If the router's IP starts with `100.x.x.x` or does not perfectly match the terminal output, the host is behind CGNAT and cannot act as a Supernode until they opt-out via their ISP.

#### 0.5.3 Defeating Network "Silent Killers"
If a Supernode's port forwarding is configured correctly but connections still timeout, operators must verify two invisible firewalls:
1. **The Host OS Firewall:** The operating system (Windows Defender or Linux `ufw`) will natively drop unrecognized internet traffic. Operators must explicitly allow the port (e.g., `sudo ufw allow 5000/tcp` on Linux).
2. **The ISP Enhanced Security Firewall:** Certain ISPs (such as Cosmote in Greece) enforce mandatory, account-level network firewalls (e.g.,  Enhanced Security) that silently drop incoming TCP packets to protect residential users. This must be manually toggled off in the ISP's customer portal to allow the Supernode to receive connections.


### 0.6 Threat Model & Architectural Defenses
To maintain a true Zero-Trust Architecture, ShadowChord assumes that any given Supernode may be actively malicious, compromised by an adversary, or monitored by a global passive adversary (like an ISP). The protocol natively integrates specific defensive mechanisms to counter known P2P routing and transport layer exploits.

#### 0.6.1 Route Severing in the $O(N)$ Ring (Critical Topology Defense)
* **The Vulnerability:** Because the network explicitly removes $O(\log N)$ logarithmic Finger Tables to preserve anonymity, it relies on strict hop-by-hop $O(N)$ routing. If it takes 50 hops for a message to traverse the ring, an attacker only needs to knock one node offline in that chain to completely sever the network. A malicious actor could join the ring, act as a valid Supernode, and simply refuse to forward packets to its successor, effectively breaking the ring and isolating 50% of the network.
* **The Architectural Fix (Backup Successors):** Supernodes must never rely on a single localized point of failure. Every Supernode maintains an active connection to its immediate successor, but also a persistent connection to a "backup successor" (the node two steps ahead on the ring). If the immediate successor dies, times out, or maliciously drops packets, the routing engine automatically bypasses the compromised node by pushing the packet through the backup link, seamlessly stitching the ring back together.

#### 0.6.2 Malicious Proxying & Censorship (Blackholing)
* **The Vulnerability:** Because Light Clients are inbound-blind and rely entirely on Supernodes to proxy their outbound traffic into the DHT, a malicious Supernode can easily execute a targeted censorship attack. The Supernode could simply receive a Light Client's message and silently drop it (Blackholing) without the Light Client ever knowing the transmission failed.
* **The Architectural Fix (Multi-Homing):** A Light Client must never trust a single Supernode with its traffic. Instead of maintaining one persistent TCP connection, the Light Client maintains simultaneous outbound connections to three random Supernodes. When dispatching a message, the client broadcasts the encrypted block to all three proxies. If one Supernode is actively malicious and drops the packet, the other two will reliably propagate it into the swarm.

#### 0.6.3 TCP Socket Exhaustion (The Slowloris Attack)
* **The Vulnerability:** Supernodes maintain a registry of persistent TCP sockets for connected Light Clients. An attacker can exploit this by spinning up a script that opens thousands of outbound TCP connections to the Supernode but sends absolutely no data. The Supernode's operating system holds these sockets open, waiting for payloads. Within seconds, the Supernode exhausts its File Descriptors (memory limits for open sockets) and crashes, locking out all legitimate users.
* **The Architectural Fix (Aggressive Timeouts):** The transport layer implements strict, aggressive timeouts at the socket level. Upon accepting a new TCP connection, the Supernode expects a valid cryptographic handshake (X25519) immediately. If the handshake is not completed and mathematically verified within 3 seconds, the Supernode forcefully drops the connection. Additionally, Supernodes enforce strict rate-limiting on incoming connection attempts per IP address.

#### 0.6.4 The Eclipse Attack on Light Clients
* **The Vulnerability:** A Light Client relies entirely on its attached Supernode to resolve DHT queries (such as `find_successor`). If an attacker controls the Supernode, they control the Light Client's reality. When the client asks where to route a specific payload, the malicious Supernode can lie, claiming itself or another attacker-controlled node is the true successor. This isolates the Light Client in a fake, empty network (an Eclipse) where all their outgoing traffic is intercepted.
* **The Architectural Fix (Cryptographic DHT Verification):** Light Clients do not blindly trust the proxy node's routing responses. All DHT routing responses returned to the Light Client must be cryptographically signed by the destination node's Ed25519 private key. The Light Client mathematically verifies the signature against the requested BLAKE3 hash before dispatching any encrypted payloads, making it impossible for the Supernode to spoof routing paths.

#### 0.6.5 Traffic Correlation & Timing Analysis
* **The Vulnerability:** Even though all ShadowChord messages are strictly padded to a fixed 64KB size, an ISP or a malicious Supernode can utilize timing analysis to deanonymize users. If the Supernode receives a packet from Bob's mobile IP and exactly 0.01 seconds later forwards a 64KB packet to the ring, the observer mathematically correlates the ingress and egress timings to prove Bob is the original sender.
* **The Architectural Fix (Packet Batching and Delay):** To break temporal correlation, Supernodes implement a localized mixnet strategy. When a Supernode receives a message from a Light Client, it does not immediately route it. Instead, the Supernode places the message into a holding queue for a randomized duration. It mixes the genuine message with other incoming payloads and locally generated Chaff/Dummy packets, eventually flushing the queue and sending the blocks out in a uniform batch. The timing disconnect mathematically destroys the ISP's ability to correlate the sender to the outgoing packet.

#### 0.6.6 Targeted Storage Exhaustion (The "Drop Box" Attack)
* **The Vulnerability**: Your Supernodes act as offline mailboxes, storing 64KB blocks for offline users for up to 14 days. Even with the Hashcash PoW slowing down the sender, a dedicated attacker with a decent GPU could generate a few thousand valid messages a day. If they target one specific offline user's BLAKE3 ID, they can force the Supernodes responsible for that keyspace to store gigabytes of junk data. If the Supernode runs out of hard drive space, the OS crashes (Disk Exhaustion DoS).
* **The Architectural Fix (Per-Target Quotas)**: Storage nodes must implement a localized disk-quota per BLAKE3 destination hash. For example, a Supernode will only store a maximum of 50MB of data for any single offline user. Once the mailbox hits 50MB, the Supernode begins rejecting new messages for that specific ID with a QUOTA_EXCEEDED error, protecting the node's hard drive while isolating the DoS to a single target.

#### 0.6.7 The Vampire Attack (Radio Wake-Lock Exhaustion
* **The Vulnerability**: Light Clients (5G phones) keep a persistent TCP socket open. To save battery, mobile OSes put the cellular radio to "sleep" when no data is moving. An attacker who controls a malicious Supernode can exploit this by "drip-feeding" tiny, cryptographically invalid packets down the TCP pipe to the Light Client (e.g., one byte every 10 seconds). This keeps the phone's 5G antenna in a high-power "wake-lock" state, draining the user's battery from 100% to dead in a few hours without them ever opening the app.
* **The Architectural Fix (Inbound Rate-Limiting & Radio Sleeping)**: The Light Client must monitor inbound activity. If a Supernode sends a statistically anomalous amount of garbage data, or continuously wakes the radio without delivering valid $64KB$ blocks, the Light Client's daemon must sever the TCP connection, ban that Supernode's IP locally, and connect to a different proxy.

#### 0.6.8 Time Dilation (NTP Poisoning)
* **The Vulnerability**: Your PoW defense relies on a strict timestamp window (e.g., +/- 10 minutes) to prevent attackers from pre-computing spam. But how does a Supernode know what the "real" time is? It asks its operating system, which asks an NTP server (like time.windows.com). If an attacker intercepts the Supernode's unencrypted NTP traffic (Man-in-the-Middle) or compromises the host's DNS, they can spoof the time. By making the Supernode think it is tomorrow, the node will instantly drop all legitimate incoming messages as "expired" or prematurely execute the 14-day Garbage Collection, deleting everyone's mailboxes.
* **The Architectural Fix (Decentralized Median Time)**: A Supernode must never blindly trust the host OS clock for critical cryptographic enforcement. The node should sample the timestamps attached to the latest incoming messages from various connected peers. It calculates a "Network Median Time" and flags a critical security warning if the host OS clock deviates significantly from the swarm's collective consensus.

#### 0.6.9 Identity Epochs (The Rolling Rehash)
* **The Vulnerability:** If a Supernode's ID is permanent, an attacker can spend months using high-end hardware to "grind" a vanity Node ID that sits directly next to a target (Vanity Sybil). Since the ring is $O(N)$, being the immediate neighbor is the ultimate power.
* **The Architectural Fix (Epoch-Based Hashing):** To prevent an attacker from ever feeling "settled" next to a target, Supernode IDs are not just `Hash(PublicKey)`. They are `Hash(PublicKey + Current_Epoch)`.
* **Execution:** An "Epoch" could be a 30-day window. When the epoch rolls over, every Supernode's position on the $2^{256}$ ring shifts to a new random location.
* **Why this works:** The Ed25519 identity stays the same (so your phone can always recognize your laptop), but the laptop's *location* on the ring changes. This forces any attacker who "Eclipsed" you to throw away their hard work and start grinding for a new position. It turns a permanent attack into a temporary, expensive annoyance.

#### 0.6.10 Blind IP Discovery (Recursive Peer Exchange)
* **The Vulnerability:** If nodes don't share their Public IPs, the ring cannot form. If they share them on a public "Bootstrap List," an ISP or state actor can simply block every IP on that list and kill the network.
* **The Architectural Fix (Recursive Discovery):** ShadowChord utilizes a "Need-to-Know" IP sharing model.
1. **The Initial Seed:** A new Supernode joins using a single "Bootstrap" IP (provided by the dev or a trusted friend).
2. **The Handshake:** Once connected, the node asks its Peer: *"Who are your neighbors?"*
3. **The Successor Search:** The node "walks" the ring. It only learns the IP addresses of the nodes it is physically connected to (Predecessor, Successor, and Backup Successor).
4. **IP Obfuscation:** A node's Public IP is never stored in the DHT. Instead, the DHT stores **"Signed Locators."** If Node A needs to find Node B, it sends a "Where are you?" packet around the ring. When it hits Node B, Node B replies with its current IP, encrypted so only Node A can see it.

#### 0.6.11 Route Severing & The Dead-Drop Recovery
* **The Vulnerability:** In a strict $O(N)$ ring, if one node goes offline, the chain breaks. If you don't have a public IP list, you can't "find" the next person to jump to.
* **The Architectural Fix (The Successor List):** Every Supernode maintains a small, private "Successor List" (e.g., the IPs of the next 4 nodes). If the immediate Successor (Node +1) fails, the node attempts to connect to Node +2.
* **The Public IP "Dead-Drop":** For nodes with dynamic IPs (like your home laptop), the node can periodically post its current IP to an encrypted "Dead-Drop" location on a standard web service (like a specific GitHub Gist or a Mastodon post) that only its paired Light Client knows how to find and decrypt.

#### 0.6.12 The "Personal Bridge" Isolation
* **The Vulnerability:** You want the user to use their own laptop as their Supernode. If that laptop is hacked, the attacker could try to "impersonate" the phone to the rest of the network.
* **The Architectural Fix (Hardware-Bound Identity):** The Light Client (Phone) and Supernode (Laptop) share a transport link, but **not** a cryptographic identity. The Phone has its own Ed25519 keypair. Even if the Laptop is compromised, the attacker cannot sign messages as the "Phone" because the Phone's private key never leaves the mobile device's Secure Enclave. The Laptop is merely a "Blind Postman."

#### 0.6.13 Socket Exhaustion (The Slowloris Defense)
* **The Vulnerability:** An attacker opens 10,000 TCP connections to your laptop and just sits there, sending nothing, until your RAM and File Descriptors are exhausted.
* **The Architectural Fix (Aggressive Proof-of-Work Handshake):** The Supernode refuses to "allocate" a persistent socket until the connecting device proves it is a real node.
* **Execution:** When a phone connects to your laptop, the laptop sends a small "Challenge" (a random string). The phone must solve a quick PoW and sign it with its Ed25519 key. If the phone doesn't provide this within 2 seconds, the laptop kills the TCP connection instantly. This makes it computationally impossible for a single attacker to "exhaust" your laptop, as they would need more CPU power than you have RAM.

---


## 1. Node Anonymity
## 1.1. Cryptographic Identity & Transport (The Dark Tunnel)
To guarantee user anonymity, IP addresses have been strictly scrubbed from the application layer. Identity is mathematically enforced, and all transport links are blind.

* **Identity Protocol (Ed25519):** Node identities are no longer hashes of their `IP:PORT` strings. A node's position on the ring is the hash of its permanent Ed25519 Public Key, completely decoupling logical identity from physical location.
* **Hashing (BLAKE3):** The protocol standardizes on BLAKE3, expanding the ring to a $2^{256}$ keyspace and replacing the legacy SHA-1 `get_sha1_hash` implementation.
* **Key Exchange (X25519 PFS):** Before any application data is exchanged or JSON is parsed, establishing a TCP socket triggers an immediate X25519 Diffie-Hellman handshake within the connection loop. This ensures Perfect Forward Secrecy (PFS) for all point-to-point links.
* **Symmetric Encryption (XChaCha20-Poly1305):** All traffic is end-to-end encrypted and authenticated. The raw networking wrappers (`__send_framed_msg` and `__recv_framed_msg`) now prepend a cryptographic nonce and authenticate the ciphertext before deserialization, instantly dropping tampered packets.

## 1.2. Topology: The "Invisible" Node
We have deliberately crippled the standard Chord routing engine to isolate network exposure and prevent malicious nodes from mapping the entire network.

* **No Finger Tables:** The O(log N) logarithmic routing table and its background `fix_fingers_worker` synchronization daemon have been completely removed.
* **Strict Hop-by-Hop Routing:** A node only ever opens TCP connections to its immediate predecessor and successor. To the rest of the $2^{256}$ ring, the node's physical IP address is completely invisible.
* **Anonymous Return Paths:** Because nodes no longer embed their physical `requester_ip` in `insert_request` or `query_request` payloads, responses must ripple backward through the successor chain using temporary circuit IDs or symmetric routing state held in memory.

## 1.3. The Data Plane: Fixed-Cell Messaging
The legacy out-of-band data plane and direct TCP file streaming (`FileStreamer`) have been deprecated. Data now travels directly within the ring's encrypted routing payloads.

* **Fixed-Cell Architecture:** To defeat global passive adversaries (ISPs performing traffic analysis), all network payloads are padded or truncated to a strict, immutable size (e.g., exactly 64KB). An observer viewing the wire data cannot distinguish between a basic network ping and a piece of a larger secret message.
* **Blind Decryption:** As fixed-size message cells ripple around the ring, every node attempts to decrypt the payload using their Ed25519 private key. If decryption succeeds, they are the intended recipient. If it fails, they silently forward the cell to their successor. This introduces significant CPU overhead but absolute sender/receiver deniability.
* **Time-To-Live (TTL):** To prevent orphaned messages from looping infinitely, every encrypted header includes a TTL counter that degrades at each hop. This mathematically enforces packet death, replacing the legacy stateful `visited_hops` tracking limit.

## 1.4. Cryptographic Friction (Sybil & Spam Defenses)
Because the network relies on strictly linear successor chains and embedded payloads, it is highly vulnerable to Eclipse Attacks (surrounding a node) and Bandwidth Exhaustion (DoS). We mitigate this via computational economics.

* **Node Generation (Argon2id PoW):** To join the ring, a user must compute a memory-hard Argon2id Proof of Work to generate a valid Node ID. This prevents an attacker from instantly spinning up millions of nodes to hijack the `successor` and `predecessor` pointers of a target.
* **Message Transmission (Hashcash PoW):** To prevent a single node from overwhelming its successor with gigabytes of data, every individual message cell must include a lightweight CPU Proof of Work. This throttles spam and enforces network limits without requiring IP bans, while remaining feasible for low-end mobile environments.


---


## 2. Swarm Storage & State Management (The Offline Mailbox)
Given the inherent churn and unreliability of volunteer P2P networks, ShadowChord operates as a temporary, asynchronous offline mailbox system rather than a permanent ledger. If a user is offline, the DHT securely holds their encrypted messages until they reconnect, ensuring guaranteed delivery without centralized databases. The legacy `DataStore` dictionary is completely replaced by a decentralized dead-drop mechanism.

### 2.1 Directed Acyclic Graph (DAG) Mailboxes
* **Mechanism:** The DHT storage engine appends incoming encrypted JSON message blocks to a cryptographic Directed Acyclic Graph (DAG) stored under the receiver's BLAKE3 identity hash.
* **Execution:** To prevent race conditions when multiple users message the same offline recipient simultaneously, strict Last-Write-Wins (LWW) mechanisms and linear hash-chaining are abandoned. Instead, the storage architecture embraces branching and client-side merging.

#### 2.1.1 Resolving the Asynchronous Race Condition
In a strict linear hash chain, if Alice and Charlie simultaneously attempt to message an offline Bob, they will both query the network, see the same "latest" message hash, and link their new messages to it. A rigid storage layer would reject one of these branches, resulting in dropped messages.
The DAG resolves this by maintaining a set of "tips" (the absolute newest messages that have not yet been replied to or followed up on). When Alice and Charlie simultaneously push messages linking to the same parent, the storage node accepts both, and Bob's mailbox temporarily branches into a fork. Subsequent messages sent to Bob will explicitly reference the cryptographic hashes of *all* currently known tips in their `parent_hashes` array, weaving the branches back together.

#### 2.1.2 The Outer Envelope (Blind Storage)
To preserve anonymity, the volunteer storage nodes must remain entirely blind to the sender's identity and the true sequence of the conversation. The network interacts only with an unauthenticated "Outer Envelope" (the `EncryptedBlock`), which contains:
* **`block_hash`:** The BLAKE3 hash of the block, acting as its unique ID.
* **`parent_hashes`:** An array of previous block hashes this message is appending to (for DAG linking).
* **`pow_nonce`:** The Hashcash Proof of Work proving the payload is not spam.
* **`outer_timestamp`:** An untrusted, network-level timestamp used *only* by the background garbage collector to enforce the 14-day TTL.
* **`ciphertext`:** The fixed-size 64KB payload encrypted via XChaCha20-Poly1305.

#### 2.1.3 The Inner Payload & Cryptographic Timestamps
Because an adversarial storage node could manipulate the `outer_timestamp` to artificially reorder a conversation, the application layer never trusts the network for chronological sorting. The true context is buried inside the `ciphertext` as the "Inner Payload," which contains:
* **`sender_pubkey`:** The permanent Ed25519 public key of the person who sent the message.
* **`inner_timestamp`:** The actual device time generated by the sender.
* **`signature`:** A cryptographic signature of the entire inner payload, signed by the sender's Ed25519 private key.

#### 2.1.4 Client-Side Flattening and Verification
When the recipient connects to the network, their client downloads the entire web of encrypted blocks. The client attempts to decrypt each block using their established symmetric keys. Upon successful decryption, the client mathematically verifies the Ed25519 `signature` against the `sender_pubkey` to guarantee the payload was not forged or altered in transit. Finally, the client extracts the trusted, verified `inner_timestamp`s from the payloads and uses them to flatten the multi-branched DAG into a single, chronologically accurate timeline for the user interface.

### 2.2 Propagated Chain Replication (The Write Path)
* **Mechanism:** To achieve high availability without exposing the sender's physical IP address to multiple nodes, ShadowChord completely deprecates the standard `successor_list` array. Replication is strictly handled via hop-by-hop chain propagation.
* **Execution:** When an encrypted message cell enters its designated keyspace, the initial storage node writes the block to disk, attaches a `replication_ttl = k` counter to the outer envelope, and forwards it solely to its immediate successor. Each subsequent node stores the block and decrements the TTL until it reaches zero.
* **Security Guarantee:** The sender's physical IP is only ever exposed to their immediate Entry Node. The network achieves a target replication factor (e.g., $k=5$ or $k=7$) sequentially, ensuring high Byzantine Fault Tolerance against node churn without multiplying the sender's attack surface. Background stabilization daemons autonomously repair the chain if a node crashes mid-propagation.

### 2.3 Merkle-Snowball Quorums (Rollback Defense & The Read Path)
* **Mechanism:** To prevent a malicious storage node from silently omitting or deleting recent messages (a Chain Truncation Attack), clients must never trust a single node's version of their DAG mailbox.
* **Execution:** When a client comes online, they do not establish direct TCP connections to $k$ nodes (which would unmask their IP). Instead, the client dispatches a "Snowball Read Request" to their successor. This request ripples sequentially through the $k$ successors responsible for the keyspace. Rather than attaching the massive 64KB message blocks, each node strictly appends its locally calculated Merkle Tree Root Hash of the user's mailbox to the payload.
* **Evaluation:** The $k$th node turns the packet around, sending the array of Merkle Roots backward through an anonymous return path. The client evaluates the roots to identify which nodes possess the most comprehensive, untampered DAG. The client then issues a targeted download request to fetch only the missing cryptographic blocks, completely neutralizing adversarial censorship with minimal bandwidth bloat.

### 2.4 Cryptographically Bound Storage TTLs
* **Mechanism:** To prevent memory exhaustion across the swarm, all nodes enforce a strict Time-To-Live (TTL) policy on stored data. A background garbage collection daemon autonomously sweeps the local storage and purges message blocks after a predefined lifecycle (e.g., 14 days).
* **Execution:** To prevent adversarial nodes from artificially fast-forwarding their local system clocks to instantly delete a user's mailbox, the storage TTL is tied directly to the timestamp cryptographically bound within the sender's Proof-of-Work (PoW). Because the PoW requires computational effort for a specific time window, a malicious node cannot forge an older timestamp to justify premature deletion.

### 2.5 Randomized Routing TTLs (The Anonymity Buffer)
* **Mechanism:** As message payloads travel hop-by-hop around the ring, they utilize a Time-To-Live (TTL) counter embedded in the encrypted header to prevent orphaned messages from looping eternally.
* **Execution:** To prevent intermediate routing nodes from deducing the sender's physical distance based on the TTL degradation, the initial TTL is heavily randomized. When a client dispatches a message, they set the starting TTL to a random integer between $R_{min}$ and $R_{max}$. An intermediate node receiving a packet with a TTL of 45 cannot mathematically determine if the packet originated 1 hop away or 20 hops away, preserving topological anonymity.


---


## 3. Privacy & Anonymity Engine (Traffic Obfuscation)
To protect against metadata analysis, ISP surveillance, and traffic correlation, the network enforces strict transport-layer obfuscation. Because ShadowChord v2.0 utilizes a hop-by-hop topology where nodes only communicate with their immediate successors, the entire ring naturally functions as a massive, decentralized mixnet.

### 3.1 Ring-Routed Blind Relays
* **Mechanism:** The legacy concept of constructing direct 3-hop onion circuits is incompatible with a strictly localized, "Invisible Node" topology. Instead, the network relies on Ring-Routed Blind Relays.
* **Execution:** A sender encrypts the payload using the destination's public key. The initial node passes it to its successor. Every intermediate node is effectively a blind relay: it attempts to decrypt the cell, fails, decrements the routing TTL, and forwards it along the ring.
* **Security Guarantee:** Intermediate nodes have mathematically zero knowledge of the sender's identity, the destination's identity, or the payload contents.

### 3.2 Uniform Packet Sizing
* **Mechanism:** To mitigate heuristic traffic analysis and size-correlation attacks by global passive adversaries (like ISPs), the message dispatcher strictly pads or fragments all network payloads to a monolithic size.
* **Execution:** All transmissions on the wire—whether a tiny network ping or a piece of a larger secret message—are enforced to be exactly 64KB. Observers viewing the wire data see only identical blocks moving between nodes, rendering control traffic indistinguishable from data traffic.

### 3.3 Adaptive Covert Chaffing
If a mobile phone is completely silent and suddenly fires a single 64KB packet when a user hits "Send," an observing ISP instantly knows an action occurred (Timing Attack). To prevent this without destroying mobile batteries, ShadowChord utilizes Role-Based, Adaptive Chaffing.

* **Role-Based Generation (Supernodes vs. Light Clients):**
  * Leveraging environment detection natively, the network differentiates hardware. **Supernodes** (Desktops, VMs, devices on AC power) act as "Noise Generators," continuously firing dummy packets to create a baseline hum across the network.
  * **Light Clients** (Mobile phones/Termux on battery) remain completely silent in the background to preserve battery life and metered bandwidth.
* **Burst Chaffing (The Mobile Cover-Up):** When a mobile user opens the app and begins typing, the client wakes up and immediately begins firing dummy packets into the ring. The real message is seamlessly slipped into this outgoing stream. The phone continues sending dummy packets for a randomized duration (e.g., 30 to 90 seconds) after the real message leaves before going back to sleep. The ISP sees only an ambiguous, uniform burst of activity.
* **Poisson Distribution Timing:** Rather than sending dummy packets at predictable, fixed intervals (which surveillance algorithms easily filter out), chaff generation utilizes a Poisson distribution. This mathematically mimics the organic, irregular, and bursty nature of real human internet traffic.
* **Indistinguishable Dummy Packets:** A chaff packet is not simply a block of zeros. It is structurally identical to a genuine `EncryptedBlock`. It contains a valid BLAKE3 hash, a valid Argon2id PoW, and 64KB of cryptographically secure random bytes encrypted with XChaCha20-Poly1305. Crucially, it includes a **Randomized Routing TTL**. When an intermediate node decrements the dummy packet's TTL to zero, it silently drops it. To an observer, the packet's "death" looks exactly like a real message successfully reaching its final destination.


---


## 4. Perfect Forward Secrecy (The Double Ratchet)
To ensure that the long-term compromise of a static Ed25519 identity key does not expose historical ciphertexts, the protocol enforces strict, dynamic key rotation for every single message cycle.

To implement this natively, the ShadowChord client maintains three separate cryptographic chains simultaneously in memory: **The Root Chain**, **The Sending Chain**, and **The Receiving Chain**.

### 4.1 The Symmetric Ratchet (Perfect Forward Secrecy)
* **Mechanism:** When a user (Alice) sends multiple messages in a row before the recipient (Bob) replies, she cannot perform a new Diffie-Hellman exchange because Bob has not yet provided a new public key. Instead, the client relies on a one-way Key Derivation Function (KDF), such as HMAC-SHA256.
* **Execution:** Alice takes her current Sending Key, feeds it into the KDF, and generates two distinct outputs: a `Message_Key` (used to encrypt the actual JSON payload) and a `Next_Sending_Key`. She immediately and permanently deletes the old keys from memory.
* **Security Guarantee:** Because cryptographic hashes are irreversible, if an adversary steals Alice's `Next_Sending_Key` today, they cannot run the math backward to figure out the `Message_Key` used for the message she sent yesterday.

### 4.2 The Diffie-Hellman Ratchet (Post-Compromise Security)
* **Mechanism:** To recover from a temporary key compromise, the encryption channel mathematically "self-heals" every time the conversation changes direction (the "ping-pong" reply).
* **Execution:** When Bob finally replies to Alice, his client generates a brand-new Ephemeral X25519 keypair. Crucially, he embeds his new *Public* Ephemeral Key strictly inside his encrypted message payload (the ciphertext), preventing storage nodes from tracking key rotations.
* **The Self-Healing:** When Alice decrypts Bob's reply, she finds his new public key and performs a fresh Diffie-Hellman mathematical exchange using her private key and his new public key. This generates a completely fresh Root Key, which spawns brand-new Sending and Receiving chains. If a hacker compromised Alice's phone an hour ago, this new DH math instantly locks them out because they do not possess Bob's new key.

### 4.3 DAG Integration (Out-of-Order Messages)
* **Mechanism:** Because ShadowChord utilizes an asynchronous Directed Acyclic Graph (DAG) for storage, network delays and churn mean Bob might receive Message #4 before Message #3.
* **Execution:** To prevent the ratchet from breaking when messages arrive out of sequence, Bob's client maintains a local "Skipped Message Keys" dictionary. When his client ratchets forward to decrypt Message #4, it securely derives and saves the `Message_Key` for Message #3 in local storage. When Message #3 finally arrives from the swarm, Bob's client uses the saved key to decrypt the delayed payload, and then safely deletes the key.


---


## 5. Sybil & Abuse Resistance (Cryptographic Friction)
Because ShadowChord is a decentralized, zero-trust network that explicitly obscures IP addresses, traditional abuse mitigations like IP-banning or rate-limiting by origin are impossible. Instead, the network imposes strict computational and localized threshold costs on all write operations to physically bottleneck attackers.

### 5.1 Identity Proof-of-Work (Sybil & Eclipse Defense)
* **Mechanism:** To prevent an attacker from instantly generating millions of cryptographic identities to mathematically surround a target (an Eclipse Attack), Node ID generation requires a memory-hard Proof-of-Work.
* **Execution:** ShadowChord utilizes **Argon2id** for identity generation. When a user creates an account, their device must compute an expensive hash to bind their Ed25519 Public Key to a valid $2^{256}$ ring ID. Because Argon2id is memory-bound rather than pure CPU-bound, it heavily throttles adversarial server farms (which rely on massive GPU/ASIC arrays with low memory-per-core) while remaining feasible for a mobile phone to compute once during initial setup.

### 5.2 Message Proof-of-Work (Spam & DDoS Defense)
* **Mechanism:** To prevent a malicious node from exhausting the network's bandwidth and storage with junk data, every single encrypted message block must carry a lightweight, CPU-bound Hashcash Proof-of-Work.
* **Execution:** Before dispatching a 64KB fixed-size cell, the sender's client (Light Client) must repeatedly hash the outer envelope's payload alongside a random nonce and the current timestamp until the output falls below a network-defined difficulty threshold.
* **The Economics:** This computation takes ~0.5 to 2 seconds on a standard smartphone—an imperceptible delay for asynchronous human messaging. However, it makes automated mass-spamming computationally bankrupting, as an attacker's CPU will physically bottleneck before they can overwhelm the swarm.

### 5.3 The Validation Standard & Time-Binding
* **Mechanism:** The cryptographic validation equation for all inbound messages is defined as:
  $H(\text{nonce} \parallel \text{timestamp} \parallel \text{payload}) < \text{target}$
* **Execution:** When an intermediate routing node or a final storage node receives a message cell, it acts as a cryptographic bouncer. It performs a single, $O(1)$ hash execution using the provided nonce. If the resulting hash does not satisfy the target difficulty, the node instantly drops the packet before it consumes local disk space or further network bandwidth.
* **Pre-computation Defense:** The attached `timestamp` must fall within a strict, recent time window (e.g., +/- 10 minutes of the network time). This mathematically prevents attackers from pre-computing millions of valid PoW nonces weeks in advance to execute a sudden burst attack.

### 5.4 Hop-by-Hop Circuit Breakers (The Predecessor Threshold)
* **Mechanism:** Because ShadowChord utilizes a strict linear routing topology where a node only accepts traffic from its immediate predecessor, nodes enforce a localized volume threshold.
* **Execution:** Even if incoming messages possess mathematically valid PoW, a node monitors the raw packet velocity coming from its predecessor. If the predecessor begins forwarding traffic at a rate that exceeds human capability or expected network routing volumes (e.g., >50 cells per second), the node temporarily drops the connection or silently discards the excess packets. This acts as a localized circuit breaker, physically isolating compromised or spamming segments of the ring from the broader network.
