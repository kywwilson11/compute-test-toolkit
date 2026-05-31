## Networking: Fundamentals to the Manufacturing Floor

Networking shows up in this role in four concrete ways, each pulling the material in a
different direction:

1. **The test station talks to instruments and dashboards over TCP/IP.** The ACT3
   power-supply client, `equipment_rpc` JSON-over-TCP, the heartbeat HTTP POST, and the
   FastAPI results dashboard are all ordinary network programs. When one "hangs" or
   reports "Disconnected," you debug it as a network problem.
2. **You test Network Interface Cards (NICs) on compute boards as a manufacturing step.** Enumerate the card,
   confirm link/speed/duplex, run a self-test, push traffic to a partner with `iperf3`,
   and gate on error counters. A Network Interface Card (NIC) that links but quietly drops 0.1% of frames is a
   reject — catch it on the line, not in the field.
3. **The Zoox compute platform uses automotive Ethernet between modules** —
   single-pair 100/1000BASE-T1 — which behaves differently enough from office RJ45 that
   it gets its own section. See also the Automotive chapter for the full 100/1000BASE-T1
   context; this chapter focuses on the test and diagnostic angle.
4. **You debug failures with command-line tools.** `ip`, `ethtool`, `ping`, `ss`,
   `tcpdump`, `iperf3`, `nmap`, `mtr`, `dig`, `nc` — used with a layered methodology
   so you isolate the fault fast.

The throughline of this chapter: **think in layers**. Almost every networking failure,
whether on the bench or in a test script, is answered fastest by asking "which layer is
broken?" and starting from the bottom.

---

## The OSI and TCP/IP Models

### Two Models, One Reality

The Open Systems Interconnection (OSI) 7-layer model is the teaching model and the shared vocabulary ("that's a Layer 2
problem"). The TCP/IP 4-layer model is what actually runs on every machine you touch.
Know both; **think in TCP/IP, speak in OSI layer numbers**.

| TCP/IP layer | OSI layer(s) | Example protocols | What it does | Address |
|---|---|---|---|---|
| Application | 5-7 (Session, Presentation, Application) | HTTP, DNS, DHCP, SSH, MQTT, Modbus/TCP, your RPC | Meaning of the bytes; your code lives here | URL / hostname |
| Transport | 4 (Transport) | TCP, UDP | End-to-end delivery; reliability (TCP) or speed (UDP); multiplexing by port | Port number |
| Internet | 3 (Network) | IP, ICMP, ARP* | Logical addressing and routing between networks | IP address |
| Link | 1-2 (Data Link, Physical) | Ethernet, Wi-Fi, PPP, 1000BASE-T1 | Framing + physical transmission on the local link | MAC address |

\* Address Resolution Protocol (ARP) straddles the boundary — it maps L3 IP to L2 MAC and is often called "L2.5."

A quick mnemonic for OSI bottom-to-top: **P**lease **D**o **N**ot **T**hrow **S**ausage
**P**izza **A**way (Physical, Data Link, Network, Transport, Session, Presentation,
Application).

### What Each Layer Actually Does

- **L1 Physical** — voltages, light, modulation, connectors, cable. "Is there a signal
  on the wire?" Bits only. (PAM3 on 1000BASE-T1 lives here.)
- **L2 Data Link** — frames with MAC addresses; error detection via CRC/Frame Check Sequence (FCS); media
  access (who talks when). Switches operate here. A CRC error or a duplex mismatch is L2.
- **L3 Network** — IP addressing, subnets, routing between networks. Routers live here.
  ICMP (ping) is L3. "Wrong subnet / no route to host" is L3.
- **L4 Transport** — segments the byte stream, multiplexes apps via ports, and for TCP
  adds reliability, ordering, flow and congestion control. "Connection refused" is L4.
- **L5 Session / L6 Presentation** — establishing/resuming sessions (TLS session
  resumption), encoding, serialization (JSON/UTF-8), encryption/compression. Your
  `json.dumps(...).encode()` is presentation work.
- **L7 Application** — the protocol your code speaks: HTTP verbs, DNS queries, your
  custom RPC framing.

### Encapsulation: How a Packet Is Built and Torn Down

When your Python script calls `socket.sendall(data)`, each layer wraps the layer above
in its own header (L2 also appends a trailing CRC):

```text
APP   |                                  [ JSON payload ]
L4    |                    [ TCP hdr | JSON payload ]            <- src/dst PORT, seq/ack
L3    |          [ IP hdr | TCP hdr | JSON payload ]             <- src/dst IP, TTL
L2    | [ Eth hdr | IP hdr | TCP hdr | JSON payload | FCS ]      <- src/dst MAC, CRC-32
L1    |  ...serialized as PAM/NRZ symbols on the wire...
```

The receiver does the reverse — decapsulation — stripping headers bottom-up and handing
the payload to the next layer, until the peer's `recv()` returns the JSON. Each layer
reads only its own header; the payload is opaque. This is why a switch can forward a
frame without understanding HTTP, and why an L4 firewall that filters on ports does not
need to parse your application protocol.

Precise terminology that comes up in packet captures:

- **Frame** = L2 unit (Ethernet frame).
- **Packet** = L3 unit (IP packet, also called IP datagram).
- **Segment** = L4 TCP unit; **datagram** = L4 UDP unit.

### Frame, MTU, and Jumbo Frames

A standard Ethernet frame: 6-byte dst MAC, 6-byte src MAC, 2-byte EtherType (0x0800 =
IPv4, 0x0806 = ARP, 0x86DD = IPv6), 46–1500-byte payload, 4-byte FCS (CRC-32). The
**Maximum Transmission Unit (MTU)** is the largest L3 payload — **1500 bytes** by
default on Ethernet. **Jumbo frames** raise this to ~9000 bytes; they reduce per-packet
overhead and CPU load and are common on storage/10GbE test links — but **every device in
the path (NIC, switch, partner) must agree**, or you get silent black-holing of large
packets ("ping works, big transfers hang"). That mismatch is a classic test-station
gotcha: small control packets pass, bulk `iperf3` stalls. Check it:

```bash
ping -M do -s 1472 192.168.1.50     # don't-fragment, 1472B payload -> total IP pkt 1500B
ping -M do -s 8972 192.168.1.50     # tests jumbo (8972+28 = 9000B)
```

If the 8972-byte ping fails but the 1472-byte ping succeeds, an intermediate device is
not jumbo-capable.

---

## Ethernet, MAC Addresses, and VLANs

### MAC Addresses

A **MAC address** is a 48-bit (6-byte) Layer-2 hardware address, written as six hex
pairs: `00:1b:21:3c:4d:5e`. The **first 3 bytes are the OUI** (Organizationally Unique
Identifier) — the vendor. The last 3 bytes are vendor-assigned. `ff:ff:ff:ff:ff:ff` is
the **broadcast** MAC; frames sent to it are delivered to every device on the L2 segment.

MAC addresses are **link-local** — they only matter within one broadcast domain. As a
packet crosses routers, the IP addresses stay the same end-to-end but **the src/dst MAC
is rewritten at every hop**. On each link, the destination MAC is the *next hop's* MAC
(the router's interface), not the final destination's MAC. This is the most commonly
missed point: if you capture traffic leaving a router, the dst MAC is the next router's
interface, while the dst IP is still the original target.

### ARP — Mapping IP to MAC

To send an IP packet on the local link, the OS needs the destination's MAC. ARP resolves it:

```text
Host A wants to send to 192.168.1.50 (same subnet) but only knows its IP.
A broadcasts:  "ARP: who has 192.168.1.50? Tell 192.168.1.10"  (dst MAC = broadcast)
B replies (unicast): "192.168.1.50 is at 00:1b:21:aa:bb:cc"
A caches that mapping in its ARP table and sends the IP packet in an Ethernet frame.
```

If the destination is on a different subnet, A ARPs for the **gateway's** MAC and sends
the frame there; the gateway routes onward. Inspect and manage the table:

```bash
ip neigh show                   # ARP / neighbor table (modern)
arp -n                          # legacy equivalent
ip neigh flush dev eth0         # clear stale entries (useful after re-cabling or IP change)
```

ARP entry states: **REACHABLE** (recently confirmed), **STALE** (cached, unverified),
**FAILED** (no answer — host down, wrong subnet, or wrong Virtual Local Area Network (VLAN)), **INCOMPLETE**
(resolution in progress). A neighbor stuck `INCOMPLETE` or `FAILED` for an IP you expect
to reach is an L2/L3 problem: wrong VLAN, wrong subnet, dead link, or the device is off.

**Gratuitous ARP**: a host announces its own IP→MAC mapping unsolicited, typically on
boot or after an IP change, so switches and peers update their caches. Relevant on the
floor when a station is re-imaged and the old MAC lingers in a switch's forwarding table.

### VLANs and Factory Network Segmentation

A **VLAN** (IEEE 802.1Q) logically partitions one physical switch into
multiple isolated broadcast domains. Frames carry a 4-byte 802.1Q **tag** containing a
12-bit **VLAN ID** (1–4094) and a 3-bit PCP priority field. Devices in different VLANs
cannot communicate at L2 — they must pass through a **router or L3 switch**
("inter-VLAN routing"), which is exactly where you enforce policy between test stations
and the corporate network.

- **Access port** — belongs to one VLAN; the connected device is unaware of tags (the
  switch adds/removes the tag transparently).
- **Trunk port** — carries multiple VLANs tagged between switches or between a switch
  and a server. A test server hosting several VLAN sub-interfaces sits on a trunk.

Typical segmentation on a Zoox-style test floor:

- **Test-station VLAN** — controllers running your software, isolated from corporate.
- **Instrument VLAN** — power supplies, DMMs, BERTs at known static IPs.
- **DUT VLAN** — the board under test; a misbehaving DUT cannot disrupt other stations.
- **Monitoring/dashboard VLAN** — FastAPI dashboard, logging, results database.
- **Corporate VLAN** — tightly firewalled from the others.

Benefits: broadcast/fault isolation (a DUT broadcast storm stays in its VLAN),
security (a buggy device is contained), determinism (test traffic is not competing with
office traffic), and clean per-VLAN subnets. This is also why a station that "can't
reach the dashboard" sometimes just landed on the wrong VLAN after re-cabling.

**Configuring a VLAN sub-interface on Linux:**

```bash
# Create a tagged sub-interface for VLAN 100 on eth0
ip link add link eth0 name eth0.100 type vlan id 100
ip addr add 10.10.100.1/24 dev eth0.100
ip link set eth0.100 up

# Inspect (shows the VLAN ID and protocol):
ip -d link show eth0.100

# Capture only VLAN-tagged traffic for that VLAN:
tcpdump -i eth0 vlan 100
```

The switch port feeding `eth0` must be a trunk that permits VLAN 100. A frequent miss:
configuring the Linux sub-interface correctly while the switch port remains an access
port for a different VLAN — result is complete silence with no obvious error.

**Switch-config basics a test engineer touches:**

Most managed switches are configured via a web UI, a proprietary CLI (Cisco IOS-style or
similar), or a REST API. The operations you'll actually perform:

- Assign a port to an access VLAN: `switchport mode access; switchport access vlan 100`
  (IOS syntax).
- Configure a trunk: `switchport mode trunk; switchport trunk allowed vlan 100,200`.
- Check a port's operational state and speed: `show interfaces GigabitEthernet0/1` — look
  for "connected," the negotiated speed, and any input/output error counts.
- Check the MAC address table to see which device is learned on which port:
  `show mac address-table` (useful when a DUT is unresponsive and you want to confirm it
  is even communicating at L2).
- Enable/disable spanning-tree PortFast on test ports that connect directly to end
  devices (avoids the 30-second Spanning Tree Protocol (STP) listening/learning delay that makes newly connected
  DUTs appear dead on boot).

---

## IP Addressing and Subnetting

### The Basics

IPv4 is 32 bits, written as four 8-bit **octets** in dotted-decimal: `192.168.1.100`.
A **subnet mask** (or CIDR prefix) splits the address into a **network portion** (left,
the 1-bits of the mask) and a **host portion** (right, the 0-bits).

- CIDR `/24` means "the first 24 bits are network."
- The mask `255.255.255.0` is the same `/24` (24 leading 1-bits = `11111111.11111111.11111111.00000000`).

In every subnet, two addresses are reserved:

- **Network address** — all host bits 0 (names the subnet).
- **Broadcast address** — all host bits 1 (reaches every host on the subnet).

So a subnet with $H$ host bits has $2^H$ total addresses and $2^H - 2$ usable host
addresses. Exception: `/31` point-to-point links (RFC 3021) have no network/broadcast
reserved — both addresses are usable. A `/32` is a single-host route.

### Reference Table

| CIDR | Mask | Host bits | Usable hosts | Block size | Typical use |
|---|---|---|---|---|---|
| /8  | 255.0.0.0       | 24 | 16,777,214 | -- | `10.0.0.0/8` large private |
| /16 | 255.255.0.0     | 16 | 65,534 | -- | `172.16.0.0/16` medium site |
| /24 | 255.255.255.0   | 8  | 254 | 256 | typical LAN / VLAN |
| /25 | 255.255.255.128 | 7  | 126 | 128 | half a /24 |
| /26 | 255.255.255.192 | 6  | 62  | 64  | quarter /24, lab segment |
| /27 | 255.255.255.224 | 5  | 30  | 32  | small rack |
| /28 | 255.255.255.240 | 4  | 14  | 16  | tiny segment, mgmt |
| /29 | 255.255.255.248 | 3  | 6   | 8   | handful of instruments |
| /30 | 255.255.255.252 | 2  | 2   | 4   | point-to-point link |
| /31 | 255.255.255.254 | 1  | 2*  | 2   | P2P (RFC 3021, no net/bcast) |
| /32 | 255.255.255.255 | 0  | 1   | 1   | single-host route |

\* `/31`: both addresses are usable as the two ends of a point-to-point link.

**Private ranges (RFC 1918)** — never routed on the public Internet:

- `10.0.0.0/8` — 10.0.0.0 through 10.255.255.255
- `172.16.0.0/12` — 172.16.0.0 through 172.31.255.255 (note: /12, not /16)
- `192.168.0.0/16` — 192.168.0.0 through 192.168.255.255

Also: `127.0.0.0/8` loopback (`127.0.0.1` = localhost); `169.254.0.0/16` link-local /
APIPA — an interface that requested Dynamic Host Configuration Protocol (DHCP) and got no answer self-assigns here. Seeing a
`169.254.x.x` address is a dead giveaway that **DHCP failed**.

### The Fast Method (Block Size / "Magic Number")

You almost never need to go to binary. For any subnet:

1. Find the octet where the mask is not 0 and not 255 — the "interesting octet."
2. **Block size = 256 - (mask value in that octet).** This is the increment between
   subnet network addresses.
3. Subnet boundaries are multiples of the block size: 0, block, 2*block, ...
4. The given IP falls in whichever block bracket contains it.
5. **Network address** = lower boundary. **Broadcast** = next boundary - 1. **Usable** =
   network+1 through broadcast-1.

### Worked Example A — /26

**Given:** `192.168.1.100/26`. Find mask, network, broadcast, usable range, host count.

- /26 → 6 host bits → $2^6 = 64$ total, **62 usable**.
- Mask: `255.255.255.192` (4th octet = `11000000` = 192).
- Block size = 256 - 192 = **64**. Boundaries in 4th octet: 0, 64, 128, 192. `100` falls
  in the **64–127** block.
- **Network = 192.168.1.64**, **Broadcast = 192.168.1.127**.
- **Usable = 192.168.1.65 – 192.168.1.126** (62 addresses).

The four /26 subnets of `192.168.1.0/24`:

| Subnet | Network | Usable range | Broadcast |
|---|---|---|---|
| 1 | 192.168.1.0   | .1   – .62   | 192.168.1.63  |
| 2 | 192.168.1.64  | .65  – .126  | 192.168.1.127 |
| 3 | 192.168.1.128 | .129 – .190  | 192.168.1.191 |
| 4 | 192.168.1.192 | .193 – .254  | 192.168.1.255 |

### Worked Example B — /28 (Smaller Blocks)

**Given:** `10.10.50.37/28`.

- /28 → 4 host bits → 16 total, **14 usable**. Mask `255.255.255.240` (4th octet = 240).
- Block size = 256 - 240 = **16**. Boundaries: 0, 16, 32, 48, 64… `37` is in **32–47**.
- **Network = 10.10.50.32**, **Broadcast = 10.10.50.47**.
- **Usable = 10.10.50.33 – 10.10.50.46.**

A /28 is a natural fit for a small test cell: 14 usable addresses cover a controller, a
few instruments, a NIC-under-test, and the partner box.

### Worked Example C — Interesting Octet in the Third Octet (/22)

**Given:** `172.16.20.5/22`. People stumble here because the boundary is not in the last
octet.

- /22 → mask `255.255.252.0`. Interesting octet = **3rd** (`11111100` = 252).
- Block size in the 3rd octet = 256 - 252 = **4**. Boundaries: 0, 4, 8, 12, 16, **20**, 24…
  The 3rd octet is 20, which is exactly a boundary — the subnet starts there.
- **Network = 172.16.20.0**. It spans 4 values of the 3rd octet: 20, 21, 22, 23.
- **Broadcast = 172.16.23.255** (3rd octet 23, 4th octet 255).
- **Usable = 172.16.20.1 – 172.16.23.254.**
- Host count: 10 host bits → $2^{10} - 2 = 1022$ usable.

### Worked Example D — "How Many /27s Fit, and What Is the 5th One?"

**Given:** Subnet `192.168.1.0/24` into /27s.

- Block size 32 → `256 / 32 = 8` subnets, starting at 0, 32, 64, 96, 128, 160, 192, 224.
- **5th subnet** (1-indexed): start = `4 x 32 = 128` → `192.168.1.128/27`.
  - Network `192.168.1.128`, broadcast `192.168.1.159`, usable .129–.158 (30 hosts).

### "Are These Two Hosts on the Same Subnet?" (The AND Trick)

Two hosts can communicate directly (L2, no router) only if they share the same network
address. Compute `IP AND mask` for each and compare.

**Given:** A = `10.0.5.10/22`, B = `10.0.7.200/22`. Same subnet?

- /22, interesting octet = 3rd, block = 4. For A: 3rd octet 5 → block **4–7** → net
  `10.0.4.0`. For B: 3rd octet 7 → block **4–7** → net `10.0.4.0`. **Same — direct L2.**

Now B = `10.0.8.200/22`: 3rd octet 8 → block **8–11** → net `10.0.8.0` ≠ `10.0.4.0` →
**different subnets, traffic must go through the gateway**. This is the bug behind "I
set a static IP and now I can't reach the instrument" — the host and instrument ended up
in different subnets, or the mask is wrong.

### Routing Basics

The host's kernel routing table decides where to send each packet. Each entry is
essentially: "for packets destined to network X/Y, send them to next-hop Z on interface
W." The kernel matches the **most specific (longest prefix)** route first. If no specific
route matches, the **default route** (`0.0.0.0/0`) is used, which points to the gateway.

```bash
ip route show
# typical output:
# default via 10.10.100.1 dev eth0 proto dhcp
# 10.10.100.0/24 dev eth0 proto kernel scope link src 10.10.100.50
# 172.16.0.0/16 via 10.10.100.254 dev eth0
```

The `proto kernel` route is auto-added for the interface's own subnet (direct, no next
hop needed). The `default via` line is what you check when an instrument on a different
subnet is unreachable — if there is no route to that subnet and no default, packets are
silently discarded with "No route to host."

Add/delete routes temporarily (lost on reboot unless persisted via NetworkManager or
`/etc/network/interfaces`):

```bash
ip route add 192.168.50.0/24 via 10.10.100.1     # add a static route
ip route del 192.168.50.0/24                       # remove it
ip route add default via 10.10.100.1               # set the default gateway
```

### IPv6 at Awareness Level

You will mostly live in IPv4 on the factory floor, but know the shape: **128 bits**,
written as eight groups of four hex digits, e.g.,
`2001:0db8:0000:0000:0000:ff00:0042:8329`. Rules: drop leading zeros in a group and
collapse one run of all-zero groups to `::` → `2001:db8::ff00:42:8329`. `::1` is
loopback (= IPv4 `127.0.0.1`); `fe80::/10` is link-local (every interface has one, used
by neighbor discovery). There is **no ARP** in IPv6 — it uses **NDP** (Neighbor
Discovery Protocol, Internet Control Message Protocol (ICMP) v6) instead. IPv6 link-local addresses are assigned
automatically, so you may see `fe80::` addresses even on networks with no IPv6
infrastructure — they are normal.

---

## ARP, DHCP, and DNS

### DHCP — Dynamic Address Assignment

DHCP hands out IP, mask, gateway, DNS server, and lease time. The exchange is **DORA**:

```text
Client (no IP)  --- DHCPDISCOVER (broadcast, UDP src=68 dst=67) ---> server(s)
Client          <-- DHCPOFFER   (here is an IP, lease time) --------- server
Client          --- DHCPREQUEST (I'll take that IP) ----------------> server
Client          <-- DHCPACK     (it's yours, lease=T) --------------- server
```

DHCP rides on **UDP**, ports 67 (server) and 68 (client). If a host self-assigns
`169.254.x.x`, no DHCPOFFER ever arrived — DHCP server down, wrong VLAN, or a
cable/link problem.

**Manufacturing practice:** instruments, the DUT-facing NIC, and the dashboard get
**static IPs** (or DHCP reservations keyed to MAC) so that "the power supply is always
at 192.168.10.5" is an invariant your test code can rely on. A station that grabs a new
DHCP lease mid-run and changes IP is a flaky-test root cause.

### DNS — Names to Addresses

DNS turns `dashboard.factory.local` into an IP. Resolution order on a typical Linux
host: **in-memory stub cache → `/etc/hosts` → configured DNS servers**. The order is
governed by `/etc/nsswitch.conf` (`hosts:` line).

Record types worth knowing: **A** (name → IPv4), **AAAA** (name → IPv6), **CNAME**
(alias), **PTR** (reverse: IP → name), **SRV** (service discovery), **TXT** (metadata).

```bash
dig +short dashboard.factory.local         # just the answer (scriptable)
dig dashboard.factory.local                # full answer with TTL and sections
dig -x 192.168.1.50                        # reverse lookup (PTR)
dig @8.8.8.8 example.com                   # query a specific resolver directly
nslookup dashboard.factory.local           # interactive / legacy
getent hosts dashboard.factory.local       # resolve the way the OS would (honors /etc/hosts)
cat /etc/resolv.conf                       # which DNS servers + search domains configured
cat /etc/hosts                             # static local overrides
```

Factory-relevant gotchas:

- "Can ping the IP but not the hostname" → **DNS** problem, not connectivity. Start at
  `/etc/resolv.conf` and `/etc/hosts`.
- "ping-by-name works but the app fails" → not DNS. Look at the application level.
- **Best practice for test stations:** pin critical instrument and dashboard names in
  `/etc/hosts` so the line never depends on a DNS server being up during a production run.

---

## TCP vs UDP

### Side-by-Side

| Feature | TCP | UDP |
|---|---|---|
| Connection | Connection-oriented (3-way handshake) | Connectionless |
| Reliability | Guaranteed delivery + ordering + retransmit | Best-effort; no retransmit |
| Ordering | In-order byte stream | None (app must handle reordering) |
| Flow control | Yes (receiver window) | No |
| Congestion control | Yes (slow start, cwnd) | No |
| Message boundaries | None — byte stream | Preserved per datagram |
| Header size | 20-60 bytes | 8 bytes |
| Typical use | HTTP(S), SSH, instrument sockets, `equipment_rpc`, ACT3 | DNS, DHCP, NTP, video, gPTP, `iperf3 -u` |

The deepest practical difference for your code: **TCP is a byte stream**. A single
`recv(4096)` gives you "up to 4096 bytes that have arrived so far," which may be a part
of one logical message, one whole message, or pieces of several. A UDP `recvfrom()` gives
you exactly one datagram per call. This is why the RPC/ACT3 recv loop (see the Sockets
section) must frame messages itself.

### The TCP 3-Way Handshake (Connection Setup)

```text
Client                          Server
  | ---- SYN  (seq=x) --------->|     "I want to talk; my starting seq is x"
  | <--- SYN-ACK (seq=y,ack=x+1)|     "OK; my seq is y; I received your x"
  | ---- ACK  (ack=y+1) ------->|     "Got it; connection established"
  |======== data flows =========|
```

The handshake exchanges initial sequence numbers and negotiates options (MSS, window
scaling, SACK). Two failure signatures that instantly localize a problem:

- **"Connection refused"** — the SYN reached the host but nothing was listening on that
  port; the host sent a RST. Network is fine; the service is not running or is bound to
  the wrong port/interface.
- **"Connection timed out"** — the SYN got no answer at all. Host is down, wrong IP, or
  a firewall is silently dropping it. That single distinction (refused vs timeout) narrows
  a large fraction of test-station faults without touching a cable.

### Teardown and the State Machine

```text
Active closer                   Peer
  | ---- FIN ------------------>|
  | <--- ACK -------------------|     (peer now in CLOSE_WAIT until it close()s)
  | <--- FIN -------------------|
  | ---- ACK ------------------>|
  | (TIME_WAIT ~2*MSL, ~60s)    | (CLOSED)
```

Two states that come up as operational symptoms:

- **`TIME_WAIT`** — on the side that closed first. It lingers ~60s to absorb delayed/
  duplicate packets. Normal, but if a server is restarted rapidly, the listening port
  may be briefly unavailable → "Address already in use." Fix: `SO_REUSEADDR` in the
  server socket setup.
- **`CLOSE_WAIT`** — the remote end closed but your code never called `close()`. These
  accumulate and are a **file-descriptor and socket leak** — a real bug, not a transient
  state. Many `CLOSE_WAIT` sockets piling up on a service is the signature of a missing
  `close()` or an unclosed context manager in a server loop.

```bash
ss -tan                           # all TCP sockets with state
ss -tan state time-wait           # just TIME_WAIT
ss -tan state close-wait          # CLOSE_WAIT -- socket leak hunting
ss -s                             # summary counts by state
```

### Flow Control vs Congestion Control (Do Not Conflate Them)

- **Flow control** protects the *receiver*: the receiver advertises a window (how much it
  can buffer); the sender never has more unacknowledged data in flight than that. Prevents
  a fast sender from overrunning a slow receiver's buffers.
- **Congestion control** protects the *network*: the sender maintains a congestion window
  (cwnd) that grows on success and shrinks on detected loss. Phases: **slow start**
  (exponential cwnd growth), **congestion avoidance** (linear growth), and on loss,
  fast retransmit/recovery or (on timeout) back to slow start. Linux defaults to the CUBIC
  algorithm; BBR is an alternative optimized for high-BDP links.

Why a test engineer cares: if `iperf3` throughput is below spec but stable, suspect the
physical link or a receiver window too small; if it sawtooths (climbs, collapses, climbs),
that is congestion control reacting to packet loss — chase the loss (errors, a flaky
switch port, duplex mismatch) rather than the NIC itself.

### When to Use Which Protocol

- **TCP** when correctness matters and a little latency is acceptable: controlling an
  instrument, submitting a test result, an HTTP API, file transfer. Almost all
  station-to-service traffic is TCP.
- **UDP** when timeliness beats completeness or you need to *see* loss rather than have
  TCP hide it: telemetry, time sync (gPTP/PTP), streaming sensor data, Controller Area Network (CAN)-over-IP, and
  `iperf3 -u` to measure loss/jitter on a link.

---

## Sockets and the Equipment RPC Pattern

### The 5-Tuple

Every active connection is uniquely identified by a **5-tuple**:
`(protocol, local_IP, local_port, remote_IP, remote_port)`. That is why one server port
(e.g., :5000) can simultaneously serve many clients — each connection differs in the
remote IP/port pair.

### TCP Server — the `InstrumentServer` / `equipment_rpc` Pattern

```python
import socket, json

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
# AF_INET = IPv4. SOCK_STREAM = TCP (reliable ordered byte stream).
# For UDP: SOCK_DGRAM.

server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
# SO_REUSEADDR: allow binding a port still in TIME_WAIT from a prior run.
# Without it, restarting the server within ~60s fails:
#   "OSError: [Errno 98] Address already in use"

server.bind(("0.0.0.0", 5000))
# 0.0.0.0 = listen on ALL interfaces.
# 127.0.0.1 would accept only connections from localhost --
# a common mistake that makes the service invisible to remote clients.

server.listen(5)      # backlog: connections queued before accept() runs

while True:
    conn, addr = server.accept()    # blocks until a client connects
    with conn:                       # context manager guarantees close()
        data = recv_message(conn)    # frame-aware receive (see below)
        request = json.loads(data)
        response = handle(request)
        conn.sendall(json.dumps(response).encode())
```

The `with conn:` block is not cosmetic — it is what prevents the `CLOSE_WAIT` leak
described above. A server loop that forgets to close per-connection sockets leaks file
descriptors until it hits `EMFILE` ("too many open files") and stops accepting new ones.

### TCP Client — the `act3_api_client` Pattern with the recv Fix

```python
import socket, json

def rpc_call(host, port, cmd, timeout=5.0):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        # settimeout: if no data arrives within timeout seconds, raise socket.timeout.
        # ALWAYS set a timeout on instrument sockets. Without it, a hung instrument
        # blocks the program forever -- the #1 cause of a "frozen" test station.
        sock.connect((host, port))
        sock.sendall(json.dumps(cmd).encode())

        # The recv() gotcha: TCP is a byte STREAM, not a message stream.
        # One recv(4096) may return only PART of a large response.
        # Loop and accumulate until the peer closes the connection.
        chunks = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:       # b'' means the peer closed the connection
                break
            chunks.append(chunk)
        return json.loads(b"".join(chunks))
```

This pattern fixes a real bug: a ~4 KB instrument response arrived in two TCP segments;
a single `recv(4096)` returned ~1500 bytes; `json.loads` raised `JSONDecodeError` and
the call "randomly" failed under load. Accumulating until close (or until a
length/delimiter is satisfied) is mandatory for any TCP recv loop.

### Message Framing (Because the Stream Has No Boundaries)

"Read until the peer closes" works for one-shot request/response, but a long-lived
connection that sends many messages needs explicit framing. Two standard approaches:

```python
# (a) Newline-delimited JSON -- simple, human-debuggable:
def recv_line(sock):
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf

# (b) Length-prefixed -- robust for binary payloads:
#   send 4-byte big-endian length, then the payload.
import struct

def send_message(sock, payload):
    sock.sendall(struct.pack(">I", len(payload)) + payload)

def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed mid-message")
        buf += chunk
    return buf

def recv_message(sock):
    (length,) = struct.unpack(">I", recv_exact(sock, 4))
    return recv_exact(sock, length)
```

### UDP — for Telemetry and Measurement

UDP preserves message boundaries — one `sendto` = one `recvfrom` — so no framing loop
is needed, but delivery is not guaranteed. Your app must tolerate loss and reordering.

```python
# UDP server
srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
srv.bind(("0.0.0.0", 9000))
while True:
    data, addr = srv.recvfrom(65535)    # one complete datagram per call
    srv.sendto(handle(data), addr)

# UDP client
cli = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
cli.settimeout(2.0)                     # still set a timeout; recvfrom blocks
cli.sendto(b"PING", ("192.168.1.50", 9000))
try:
    reply, _ = cli.recvfrom(65535)
except socket.timeout:
    reply = None                        # datagram lost; handle or retry
```

### The Linux Networking Stack (What Happens Between `sendall` and the Wire)

When `sendall()` is called, the kernel TCP stack:

1. Appends data to the send buffer (size controlled by `SO_SNDBUF` / `net.core.wmem_max`).
2. Packetizes into segments no larger than the MSS (maximum segment size, negotiated
   at handshake, typically MTU - 40 bytes for TCP/IP headers = 1460 bytes).
3. Applies any offloads configured on the NIC: **TSO** (TCP Segmentation Offload, the NIC
   segments rather than the CPU), **GSO** (Generic Segmentation Offload, SW equivalent),
   **GRO** (Generic Receive Offload, coalesces incoming segments).
4. Passes through the Netfilter/iptables/nftables hooks for firewall rules.
5. Hands the packet to the NIC driver ring buffer; the NIC Direct Memory Access (DMA)-fetches and transmits.

On receive, the path reverses: NIC DMA into ring buffer → driver interrupt/NAPI poll →
IP reassembly → TCP reorder buffer → application `recv()`. Tunable receive buffers
(`net.core.rmem_max`, `net.ipv4.tcp_rmem`) matter for high-throughput links. The
`ethtool -k` command shows which offloads are active; disabling them can be useful when
debugging whether a throughput problem is in hardware or software.

---

## Linux Networking Tools — Command-First Reference

### `ip` — Interfaces, Addresses, Routes, Neighbors (L1–L3)

```bash
ip link show                   # all interfaces: admin/oper state, MAC, MTU
ip link show eth0              # one interface
ip addr show                   # IP addresses per interface (alias: ip a)
ip addr show eth0
ip route show                  # routing table (alias: ip r)
ip neigh show                  # ARP / neighbor table (IP <-> MAC)
ip -s link show eth0           # interface counters: RX/TX packets, errors, dropped
ip -br addr                    # brief one-line-per-interface summary
ip -d link show eth0.100       # detailed: shows VLAN ID and protocol
```

```bash
# Temporary configuration (lost on reboot):
ip link set eth0 up
ip addr add 192.168.10.20/24 dev eth0
ip route add default via 192.168.10.1
ip neigh flush dev eth0        # clear stale ARP entries
```

`ip link` state semantics:

- `UP` — interface is administratively enabled.
- `NO-CARRIER` — interface is up but there is **no physical link signal** (cable unplugged
  or PHY problem). Pure L1 symptom.
- `DOWN` — interface is administratively disabled.
- `LOWER_UP` — physical layer is up (carrier present).

```bash
# Representative output of ip addr show eth0:
# 2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc mq state UP group default
#     link/ether 00:1b:21:3c:4d:5e brd ff:ff:ff:ff:ff:ff
#     inet 10.10.100.50/24 brd 10.10.100.255 scope global dynamic eth0
#        valid_lft 86391sec preferred_lft 86391sec
```

The `LOWER_UP` flag confirms physical carrier; its absence while `UP` is set means
`NO-CARRIER` — cable, SFP, or PHY issue.

### `ethtool` — NIC and PHY Details (L1–L2, Critical for NIC Test)

```bash
ethtool eth0                   # link, speed, duplex, autoneg, supported modes
ethtool -S eth0                # per-driver stats: rx_errors, tx_errors, rx_crc_errors, drops
ethtool -i eth0                # driver name + version, firmware version, bus-info (PCIe addr)
ethtool -k eth0                # offload features (TSO, GSO, GRO, checksum offload)
ethtool -t eth0 online         # NIC built-in self-test PASS/FAIL (online = non-disruptive)
ethtool -m eth0                # SFP/optics DDM diagnostics: temperature, Tx/Rx power (dBm)
ethtool --cable-test eth0      # pass/fail per twisted pair
ethtool --cable-test-tdr eth0  # TDR: fault type + distance to fault (meters)
ethtool -p eth0 5              # blink port LED for 5 seconds (physically identify the port)
```

```bash
# Representative ethtool eth0 output for a 1GbE link:
# Settings for eth0:
#   Supported ports: [ TP ]
#   Supported link modes: 10baseT/Half 10baseT/Full 100baseT/Full 1000baseT/Full
#   Speed: 1000Mb/s
#   Duplex: Full
#   Auto-negotiation: on
#   Link detected: yes
```

Speed is not always a clean number. Parsing `ethtool` output for speed requires handling
`2.5Gbit/s`, `10000Mb/s`, and `Unknown!`. A naive "strip all non-digits" parse turns
`2.5G` into `25` — a real bug that has caused a test to falsely pass a 2.5G NIC when
10G was expected. Always capture the value and unit separately and multiply G by 1000.

```bash
# ethtool -S eth0 output excerpt:
#     rx_packets: 1234567
#     rx_errors: 0
#     rx_crc_errors: 0
#     rx_dropped: 0
#     tx_packets: 987654
#     tx_errors: 0
#     collisions: 0
```

All error/drop counters should be zero (or zero-delta over a run). Any non-zero value
after a clean iperf3 run is a manufacturing reject trigger.

### `ss` and `netstat` — Sockets and Listening Services (L4)

```bash
ss -tuln                        # TCP+UDP, listening only, numeric (what is serving?)
ss -tan                         # all TCP sockets with state
ss -tan state time-wait         # TIME_WAIT sockets
ss -tan state close-wait        # CLOSE_WAIT (socket leak hunting)
ss -tnp                         # show owning process/PID (needs root)
ss -s                           # summary counts by state
ss -tan '( dport = :8080 or sport = :8080 )'  # filter by port
```

`ss -tuln | grep 8080` answers "is my dashboard actually listening?" If it is not in the
output, either the service is not running or it is bound to `127.0.0.1` only (loopback),
so remote stations on the VLAN cannot reach it. Fix: bind to `0.0.0.0`.

```bash
# Representative ss -tuln output:
# Netid  State   Recv-Q  Send-Q   Local Address:Port   Peer Address:Port
# tcp    LISTEN  0       5          0.0.0.0:5000         0.0.0.0:*
# tcp    LISTEN  0       128        0.0.0.0:8080         0.0.0.0:*
# udp    UNCONN  0       0          0.0.0.0:67           0.0.0.0:*
```

### `tcpdump` — Packet Capture (All Layers)

```bash
tcpdump -i eth0 -c 100 -w capture.pcap       # save 100 packets to file (open in Wireshark)
tcpdump -i eth0 -nn host 192.168.1.100       # traffic to/from one host, no name resolution
tcpdump -i eth0 -nn port 502                 # Modbus/TCP traffic
tcpdump -i eth0 -A port 8080                 # print ASCII payload (read HTTP inline)
tcpdump -i eth0 -nn 'tcp[tcpflags] & tcp-syn != 0'  # only SYN packets (watch conn attempts)
tcpdump -i eth0 -nn arp                      # watch ARP: who-has / is-at
tcpdump -i eth0 vlan 100                     # only VLAN 100 tagged frames
tcpdump -i eth0 -nn 'tcp port 5000 and (tcp-syn or tcp-fin or tcp-rst)'  # connection lifecycle
```

`-nn` disables name and port resolution (faster, unambiguous output). Capture to `.pcap`
when you need to hand it off or open Wireshark; print inline with `-A` for quick
eyeballing of HTTP or ASCII protocols. Watching ARP (`-nn arp`) is how you confirm an L2
problem: if you never see an "is-at" reply, the peer is not answering on this segment
(wrong VLAN, wrong subnet, or the device is off).

**Useful Wireshark display filters** (apply after loading a `.pcap`):

```text
ip.addr == 192.168.1.100               -- all traffic to/from a host
tcp.port == 8080                       -- filter by port
tcp.flags.syn == 1 && tcp.flags.ack == 0  -- SYN packets only (connection attempts)
tcp.analysis.retransmission            -- retransmissions (network trouble)
http.response.code >= 400             -- HTTP errors
arp                                    -- ARP traffic
```

### `ping` — Reachability and RTT (L3, ICMP)

```bash
ping -c 4 192.168.1.1                  # 4 echoes then stop
ping -c 4 -I eth0 192.168.1.50         # force out a specific interface
ping -c 100 -i 0.2 192.168.1.50        # 100 pings at 0.2s spacing (loss/jitter sample)
ping -M do -s 1472 192.168.1.50        # MTU probe: don't-fragment, 1472B payload
```

Interpreting results:

- **0% loss, steady RTT** → healthy L3 path.
- **Some loss** → marginal link or congestion.
- **100% loss, "Destination Host Unreachable"** → ARP or routing failure (L2/L3).
- **100% loss, silent timeout** → firewall dropping ICMP, or host is down.
- **MTU probe** (`-M do -s 1472`): if 1472B succeeds but 1473B fails with "frag needed,"
  path MTU is exactly 1500 (1472 payload + 28 IP/ICMP headers). If 8972B fails, jumbo is
  not working somewhere in the path.

### `mtr` — Path Analysis, Hop by Hop (L3)

```bash
mtr 10.20.30.40                   # live per-hop RTT and loss% (interactive)
mtr -n 10.20.30.40                # skip DNS resolution
mtr -rwc 100 10.20.30.40          # report mode: 100 cycles, printable summary
```

`mtr` is the best single tool for intermittent path problems: it shows which hop first
starts dropping packets, at what rate. On a flat factory subnet you will see one or two
hops; on a routed multi-VLAN network it pinpoints the offending router or link.

### `nc` (netcat) — Fastest "Can I Reach This Port?" Test (L4)

```bash
nc -zv 192.168.1.100 8080          # -z scan (no data), -v verbose
nc -zv 192.168.1.100 5000-5010     # scan a port range
nc -l 5000                          # listen on a port (throwaway test server)
echo '{"cmd":"ping"}' | nc 192.168.1.50 5000  # send a line, read the reply
```

Result interpretation:

- **"succeeded"** → TCP handshake completed; service is up and firewall allows it.
- **"refused"** → host reachable but nothing listening on that port (RST received).
- **"timed out"** → host unreachable or firewall dropping packets.

This single command disambiguates a large fraction of "can't connect" tickets. "Refused"
vs "timed out" is the same distinction as "connection refused" vs "connection timed out"
in Section TCP vs UDP — it immediately localizes the problem to L4 (service) vs L3/firewall.

### `iperf3` — Throughput and Bandwidth (L4, the NIC-Test Workhorse)

```bash
# On the partner / golden unit:
iperf3 -s                                   # start server (listens on port 5201)

# On the DUT:
iperf3 -c 192.168.1.100 -t 30 -P 4          # TCP, 30s, 4 parallel streams
# -P 4: parallel streams often needed to saturate a high-speed link
iperf3 -c 192.168.1.100 -u -b 950M -t 30    # UDP at 950 Mbps -> reports loss% + jitter
iperf3 -c 192.168.1.100 -R                  # reverse: server sends to DUT
iperf3 -c 192.168.1.100 --get-server-output # pull server-side numbers too
```

```bash
# Representative iperf3 output (TCP, 10 GbE):
# [ ID] Interval        Transfer     Bitrate
# [  5]  0.00-30.00 sec  33.3 GBytes  9.54 Gbits/sec
# - - - - - - - - - - - - - - - - - - - - -
# [ ID] Interval        Transfer     Bitrate         Retr
# [  5]  0.00-30.01 sec  33.3 GBytes  9.54 Gbits/sec    0   sender
```

Expected results: 10 GbE → >9.5 Gbps with parallel streams; 1000BASE-T1 → ~940 Mbps.
`Retr` (retransmit count) should be 0 or near-zero on a good link. UDP mode reports
**loss percentage and jitter** directly — use it when you suspect a marginal link,
because TCP hides loss by retransmitting (you'd see low throughput but not the loss
count).

Throughput interpretation:

- **Near line rate, Retr=0, errors 0** → pass.
- **Below spec, stable, error counters rising** → physical link marginal (cable/connector/
  PHY signal integrity). Confirm with `ethtool -S` diff and `--cable-test-tdr`.
- **Below spec, sawtoothing** → packet loss triggering TCP congestion control. Chase the
  loss (flaky port, duplex mismatch, MTU mismatch) rather than tuning TCP.
- **Below spec, errors clean, no jitter** → single TCP stream not saturating the link
  (add `-P 4`), or CPU/IRQ bottleneck, or offloads disabled.

### `nmap` — Discovery and Port Scanning (L3–L4)

```bash
nmap -sn 192.168.1.0/24             # ping sweep: which hosts are alive on the subnet
nmap -p 5000-5010 192.168.1.100     # which ports are open on this host
nmap -sV -p 8080 192.168.1.100      # probe service/version on a port
```

On a test floor, `nmap -sn` inventories what is actually on a VLAN — useful during
station bring-up to confirm instruments have their expected static IPs. The port scan
confirms an instrument's control port is open before writing an RPC client.

### Quick "Which Tool for Which Symptom" Map

| Symptom | First tool(s) |
|---|---|
| Is the cable / link physically up? | `ip link show`, `ethtool eth0` |
| Correct IP / subnet / gateway? | `ip addr show`, `ip route` |
| Can I reach the host at all? | `ping -c4` |
| Where in the path does it drop? | `mtr -rwc 100`, `traceroute -n` |
| Is the service listening? | `ss -tuln` |
| Can the client open the port? | `nc -zv host port` |
| Is the app returning the right thing? | `curl -v` |
| Name does not resolve? | `dig +short`, `getent hosts`, `/etc/resolv.conf` |
| Throughput below spec? | `iperf3 -c partner -P 4`, then `ethtool -S` |
| What is actually on the wire? | `tcpdump`, Wireshark |
| Socket leak / wrong TCP state? | `ss -tan state close-wait` |
| ARP not resolving? | `ip neigh show`, `tcpdump -nn arp` |
| Which NIC is which physical port? | `ethtool -p eth0 5` (blink LED) |
| Cable integrity / fault location? | `ethtool --cable-test-tdr eth0` |

---

## PXE, TFTP, NFS, and HTTP — Test-Station Provisioning and DUT Netboot

In a manufacturing environment, DUTs often boot from the network rather than from
on-board flash, and test stations themselves are re-imaged from a central provisioning
server. Understanding the provisioning stack is essential because a DUT that will not
netboot is a connectivity problem, not a firmware problem, until proven otherwise.

### The PXE Boot Chain

**PXE** (Preboot eXecution Environment) allows a machine with a network-bootable NIC to
get an OS or bootloader image from the network. The chain:

1. **DHCP** — the booting machine sends a DHCP request; the DHCP server responds with the
   standard IP/gateway/DNS assignment plus **option 66** (TFTP server address) and
   **option 67** (bootfile name, e.g., `pxelinux.0` or `grubx64.efi`).
2. **TFTP** (Trivial File Transfer Protocol, UDP port 69) — the client fetches the
   bootloader from the TFTP server. TFTP is intentionally simple and unauthenticated —
   it is transport for bootstrap only.
3. **Bootloader** — retrieves its configuration (e.g., `pxelinux.cfg/default`) via TFTP,
   then chains to the kernel (`vmlinuz`) and initial ramdisk (`initrd.img`), also via TFTP
   (or HTTP in modern iPXE setups).
4. **Root filesystem** — the booted kernel mounts its root over **NFS** (Network File
   System) for a live/diskless environment, or downloads a disk image via **HTTP/HTTPS**
   to flash.

```text
DUT (booting)          DHCP server              TFTP / HTTP server
  |-- DHCPDISCOVER -->|                         |
  |<- DHCPOFFER ------|  (IP + option 66/67)    |
  |-- DHCPREQUEST --> |                         |
  |<- DHCPACK --------|                         |
  |---------- TFTP RRQ pxelinux.0 ------------>|
  |<--------- TFTP DATA (bootloader) ----------|
  |---------- TFTP RRQ pxelinux.cfg/default -->|
  |<--------- TFTP DATA (menu config) ---------|
  |---------- TFTP/HTTP vmlinuz + initrd ------>|
  |  [kernel boots, mounts NFS or fetches img] |
```

### Diagnosing a Failing Netboot

Symptoms and root causes:

| Symptom | Layer | Likely cause |
|---|---|---|
| DUT says "PXE-E11: ARP timeout" | L2/L3 | DHCP server not reachable (wrong VLAN, no DHCP relay, server down) |
| DUT gets DHCP IP but "TFTP timeout" | L3/UDP | TFTP server IP wrong (option 66), firewall blocking UDP 69, TFTP service down |
| TFTP starts but stalls / times out | L4/app | File not found on server, wrong filename in option 67, TFTP blocksize negotiation failure |
| Kernel loads but root mount fails | L3/NFS | NFS export not configured for the DUT's IP, NFS ports blocked, wrong mount path |
| Works from test VLAN, not DUT VLAN | VLAN/routing | PXE broadcast not relayed across VLANs (need DHCP relay / IP helper) |

Debugging steps:

```bash
# On the provisioning server: is TFTP responding?
ss -tuln | grep 69            # TFTP listens on UDP 69

# From a test machine on the same VLAN as the DUT:
tftp <server-ip>
# tftp> get pxelinux.0
# Confirms TFTP is accessible and the file exists

# Watch DHCP and TFTP traffic while the DUT boots:
tcpdump -i eth0 -nn '(port 67 or port 68 or port 69)'

# Is NFS exported?
showmount -e <nfs-server-ip>   # lists NFS exports

# Check NFS mounts are accessible:
mount -t nfs <server>:/exports/rootfs /mnt/test
```

**DHCP relay (IP helper):** PXE DHCPDISCOVER is a broadcast — it will not cross a VLAN
boundary unless a **DHCP relay agent** (Cisco: `ip helper-address`, most switches have
an equivalent) is configured on the DUT's VLAN to forward DHCP broadcasts to the DHCP
server's unicast IP. A DUT on a new VLAN that gets `169.254.x.x` while the provisioning
server is on a different VLAN is almost always a missing helper-address, not a broken
DUT.

**Watching a netboot live from the provisioning server** is the fastest way to see
exactly where the chain breaks — the packet capture catches each step:

```bash
# Run on the provisioning server's interface (or any machine on the DUT's VLAN):
tcpdump -i eth0 -nn '(port 67 or port 68 or port 69 or port 4011)'
# port 4011 = ProxyDHCP (used by some UEFI/iPXE environments)

# What a healthy boot looks like in the capture:
# 0.000s  DHCP Discover  (DUT, broadcast)
# 0.003s  DHCP Offer     (server -> DUT: 10.20.30.50, next-server 10.20.1.5)
# 0.004s  DHCP Request   (DUT -> server)
# 0.005s  DHCP Ack       (server -> DUT: confirmed)
# 0.010s  TFTP RRQ "pxelinux.0"  (DUT -> 10.20.1.5 :69)
# 0.012s  TFTP DATA block 1      (512 bytes, old mode -- or 1468 bytes, negotiated blksize)
# ...

# If you only see Discover and no Offer: DHCP server is unreachable on this VLAN.
# Check: is the DHCP server running? Is the DUT VLAN in its scope?
# Is the relay agent (ip helper-address) configured on the DUT VLAN's SVI?

# If Offer appears but no TFTP RRQ: DUT did not get option 66 (next-server).
# Check: DHCP server is sending siaddr / next-server field and option 67 (filename).
ss -tuln | grep ':69'         # is tftpd listening?
ls /var/lib/tftpboot/          # is pxelinux.0 actually there?
```

**TFTP blocksize and timeouts:** Classic TFTP (RFC 1350) transfers in 512-byte blocks
and requires an ACK after every block — at 50 ms RTT this limits throughput to ~10 KB/s.
The TFTP options extension (RFC 2347/2348) allows negotiating larger blocksizes (up to
65464 bytes). Some DUT boot ROMs or TFTP servers silently reject the negotiation and fall
back to 512 bytes; in this case a ~30 MB kernel takes minutes rather than seconds. If
netboot is "working but slow," check whether blocksize negotiation is occurring (it
appears in the packet capture as TFTP OACK). iPXE avoids this entirely by using HTTP
after the initial chainload.

### HTTP-Based Provisioning

Modern provisioning tools (iPXE, Foreman, MAAS, custom scripts) fetch images via HTTP
rather than TFTP for reliability (TCP vs UDP) and bandwidth. The pattern:

```bash
# DUT iPXE script fetches kernel/initrd over HTTP:
# kernel http://provision.factory.local/images/vmlinuz
# initrd http://provision.factory.local/images/initrd.gz
# boot

# Debug: can the DUT reach the HTTP server?
curl -v http://provision.factory.local/images/vmlinuz --output /dev/null

# Watch HTTP requests while DUT boots (from provisioning server):
tcpdump -i eth0 -A port 80 | grep -E 'GET|POST|HTTP'
```

---

## Time Synchronization: NTP and PTP/IEEE 1588

Time sync is not a luxury feature — it is a functional requirement for any system that
correlates events across nodes. On the Zoox compute platform, every sensor and compute
module must share a common time base so that camera, LiDAR, and radar data can be
correctly fused. An offset of even a few hundred microseconds between modules produces
ghost objects or missed detections in the sensor fusion stack.

### NTP (Network Time Protocol)

NTP (RFC 5905) synchronizes system clocks using UDP port 123. A **stratum 0** source is
a hardware reference clock (GPS, atomic); **stratum 1** servers are directly connected to
stratum 0; clients are stratum 2+. NTP achieves **1–10 ms accuracy** on a LAN under
normal conditions.

```bash
timedatectl status                     # system time, NTP sync status, timezone
timedatectl timesync-status            # detailed systemd-timesyncd info
chronyc tracking                       # chrony: offset, RMS jitter, stratum, sources
chronyc sources -v                     # all NTP sources and their health
ntpq -p                                # NTPd peers and their offsets
```

```bash
# chronyc tracking representative output:
# Reference ID    : C0A8 0101 (192.168.1.1)
# Stratum         : 2
# Ref time (UTC)  : Thu May 21 18:22:10 2026
# System time     : 0.000004231 seconds slow of NTP time
# Last offset     : +0.000003812 seconds
# RMS offset      : 0.000004109 seconds
# Frequency       : 15.232 ppm slow
# Residual freq   : +0.003 ppm
# Skew            : 0.031 ppm
# Root delay      : 0.001234567 seconds
# Root dispersion : 0.000876543 seconds
# Update interval : 64.2 seconds
# Leap status     : Normal
```

NTP is adequate for test station timestamps, log correlation, and result timestamping.
It is **not adequate** for sensor fusion timestamps in an autonomous vehicle — that
requires PTP.

### PTP / IEEE 1588 — Precision Time Protocol

**IEEE 1588 PTP** synchronizes clocks over a local network to **sub-microsecond
accuracy** by using hardware timestamps at the NIC level (or switch level) to cancel
out software jitter. **IEEE 802.1AS (gPTP)** is a constrained profile of IEEE 1588
designed for bridged LANs and used in automotive networks as part of the TSN suite.
The original 802.1AS-2011 was based on IEEE 1588-2008; the current 802.1AS-2020
revision references IEEE 1588-2019 and adds support for multiple domains and hot-standby
grandmasters. In practice: when you see "gPTP" on the floor, expect 802.1AS-2020.

#### How PTP Works

```text
Grandmaster (GM)          Slave (boundary/ordinary clock)
  |-- Sync (t1) ---------->|    GM records t1 (transmit HW timestamp)
  |-- Follow_Up (t1) ----->|    delivers t1 to slave (2-step mode)
  |<-- Delay_Req (t3) -----|    slave sends Delay_Req, records t3 (HW tx timestamp)
  |-- Delay_Resp (t4) ---->|    GM records t4 (HW rx timestamp), sends to slave

  Slave computes:
    mean path delay = ((t2 - t1) + (t4 - t3)) / 2
    offset from master = (t2 - t1) - mean path delay
```

The slave then applies this offset to slew its clock (gradually adjusting the clock rate
rather than stepping, to avoid timestamp discontinuities).

> **gPTP uses *peer* delay, and the rate-ratio scales the responder's turnaround.** The
> formula above is the end-to-end (Delay_Request) mechanism. **802.1AS** instead uses the
> **peer-delay (P2P)** mechanism between adjacent ports: the requestor sends Pdelay_Req
> ($t_1$), the responder timestamps receive ($t_2$) and transmit ($t_3$), the requestor
> receives the response ($t_4$), and
> $\text{meanLinkDelay} = \big[(t_4-t_1) - r\,(t_3-t_2)\big]/2$, where $r$ is the
> **neighborRateRatio**. The subtlety that's easy to get backwards: $r$ multiplies the
> *responder's* turnaround $(t_3-t_2)$ — which is measured in the neighbor's clock — to
> convert it into the local timebase; it is **not** applied to the round-trip $(t_4-t_1)$.
> At a non-unity ratio the two placements give different delays, so this is a real
> correctness trap (an automated audit even "corrected" the right formula to the wrong one).
> When in doubt the reference is `linuxptp`'s `tsproc`: `delay = ((t2-t3)*rr + (t4-t1))/2`. Hardware timestamping is
critical: software timestamps have jitter of tens of microseconds; hardware timestamps
(taken at the moment the SFD crosses the wire at the NIC's MAC layer) have jitter of
nanoseconds.

#### Why PTP Matters for Autonomous Vehicles

- **Camera / LiDAR / radar fusion** requires knowing precisely when each sensor frame was
  captured. A 200 µs timing error at 100 km/h corresponds to ~5.6 mm of vehicle motion —
  meaningful for sensor alignment.
- **Event correlation** across compute modules (e.g., matching a CAN event to a camera
  frame) requires all nodes to share the same time domain.
- **TSN traffic shaping** (IEEE 802.1Qbv, time-aware gating) relies on every switch
  knowing the same time to open and close transmission windows at the right instant.
- **Log forensics:** after an incident, correlating logs from multiple ECUs requires
  synchronized timestamps — NTP-level accuracy makes millisecond event ordering
  ambiguous; PTP-level accuracy does not.

#### linuxptp — Running PTP on Linux

**`ptp4l`** runs the PTP state machine (Best Master Clock Algorithm selects the
grandmaster, then ordinary or boundary clock slaves synchronize to it). **`phc2sys`**
synchronizes the system clock (`CLOCK_REALTIME`) to the NIC's hardware clock (PHC,
PTP Hardware Clock).

```bash
# Start ptp4l as a slave on eth0 (using hardware timestamps):
ptp4l -i eth0 -m -f /etc/ptp4l.conf
# -i eth0: interface     -m: print to stdout     -f: config file

# Sync the system clock to the PHC (wait for ptp4l to lock, then auto-fetch TAI-UTC offset):
phc2sys -s eth0 -c CLOCK_REALTIME -w -m
# -w: wait for ptp4l to be synchronized before starting, then retrieve the TAI-to-UTC
#     offset from ptp4l automatically. Omitting -w and using -O 0 manually forces a
#     zero offset, which is wrong: PTP operates in TAI, the system clock in UTC, and
#     the current TAI-UTC difference is 37 seconds. Always use -w in production.

# Check PHC capability on a NIC:
ethtool -T eth0
# Should show: hardware-transmit, hardware-receive, hardware-raw-clock

# Monitor offset from master (ptp4l log lines):
# ptp4l[123.456]: master offset  -1234 s2 freq  +5678 path delay  12345
# master offset: nanoseconds of clock difference (converges toward 0)
# freq: current clock rate correction in ppb
# path delay: one-way network delay in nanoseconds
```

#### PTP Health Metrics

| Metric | Healthy | Warning |
|---|---|---|
| `master offset` | Converging to 0, then < +-100 ns steady | > +-1000 ns or diverging |
| `path delay` | Stable (constant switch latency) | Large variation = switch overloaded or asymmetric path |
| `phc2sys offset` | < +-1 us | > +-10 us = system clock / PHC drift |
| Clock state | s2 (synchronized) | s1 (uncertain) or FAULTY |

#### PTP Bring-Up on the Floor — Practical Sequence

When a new compute board lands on the test bench, this is the sequence to verify its
PTP subsystem before it ever runs sensor fusion:

```bash
# Step 1: confirm the NIC has a hardware PHC (PTP Hardware Clock)
ethtool -T eth0
# Must show: hardware-transmit, hardware-receive, hardware-raw-clock
# If only software-transmit/receive appear, the driver lacks HW timestamp support
# -- you will only get microsecond-class accuracy, not sub-microsecond.

# Step 2: start ptp4l as slave, hardware timestamping, verbose so you can watch it
ptp4l -i eth0 -m -s --summary_interval -1 -f /etc/ptp4l.conf
# -s: slave-only mode (do not try to become grandmaster)
# --summary_interval -1: log every second for rapid debugging

# Step 3: watch the log and check for these stages:
# ptp4l[0.000]: selected /dev/ptp0 as PTP clock
# ptp4l[2.134]: port 1: LISTENING       (no grandmaster yet seen)
# ptp4l[2.960]: port 1: UNCALIBRATED    (grandmaster found, measuring delay)
# ptp4l[4.123]: port 1: SLAVE           (s2: synchronized)
# ptp4l[5.456]: master offset   -234 s2 freq  +1234 path delay  4567
#               ^^^  must be s2; offset converges toward 0 within 30-60 seconds

# Step 4: sync the system clock to the PHC after ptp4l locks
phc2sys -s eth0 -c CLOCK_REALTIME -w -m
# -w waits until ptp4l is in s2 before starting, and reads the TAI-UTC
# offset automatically. The phc2sys log should show:
# phc2sys[10.234]: CLOCK_REALTIME phc offset  -456 s2 freq  +789

# Step 5: automated pass/fail in a test script
ptp4l_log=$(journalctl -u ptp4l --since "2 minutes ago" --no-pager)
# Pass criterion: s2 state reached, master offset < 1000 ns sustained for 30 s
```

**Common floor failures and their signatures:**

| Symptom in log | Cause | Fix |
|---|---|---|
| Stays in LISTENING forever | No grandmaster visible | VLAN isolation, wrong domain, PTP multicast blocked |
| Oscillates UNCALIBRATED -> SLAVE | Path delay unstable | Switch not transparent clock / boundary clock; excessive queue variation |
| `offset` > 10000 ns, not converging | SW timestamps only (no HW) | `ethtool -T` -- driver does not support hardware-transmit/receive |
| `phc2sys` reports wrong time of day | TAI/UTC offset wrong | Use `-w` not `-O 0`; current TAI-UTC = 37 s |
| ptp4l crashes or errors on `-T` | Config file missing or wrong interface | Verify `-i` matches actual interface name with `ip link show` |

If `ptp4l` never reaches s2 (synchronized state): check that the NIC supports hardware
timestamping (`ethtool -T eth0`), that the grandmaster is reachable and its priority/
domain matches, that the switch in the path supports transparent clock or boundary clock
operation (a switch that does not handle PTP will introduce variable queuing delay that
prevents convergence), and that no firewall is blocking PTP multicast packets. PTP uses:

- **UDP ports 319** (event messages: Sync, Delay_Req, Pdelay_Req, Pdelay_Resp) and
  **320** (general messages: Announce, Follow_Up, Delay_Resp, management)
- **IPv4 multicast** `224.0.1.129` (end-to-end delay) and `224.0.0.107` (peer delay)
- **L2 multicast** `01:1b:19:00:00:00` (standard PTP multicast) and `01:80:c2:00:00:0e`
  (peer-delay / link-local, not forwarded by bridges unless configured)

gPTP (802.1AS) uses exclusively the **peer-delay** mechanism — each port independently
measures the delay to its directly attached neighbor, rather than measuring the full
end-to-end path. This distinction matters for switch configuration: a switch in the path
must run a boundary clock or transparent clock to prevent the variable queuing delay from
corrupting the peer-delay measurement.

```bash
# Confirm PTP multicast traffic is flowing (on the DUT or any node on the link):
tcpdump -i eth0 -nn '(udp port 319 or udp port 320) or ether proto 0x88f7'
# 0x88f7 is the PTP Ethertype for L2 PTP frames (used when not running over UDP/IP)

# Show which clock state the node is in and the current offset:
journalctl -u ptp4l -f
# or read the log directly if running in foreground
```

#### PTP vs NTP Summary

| | NTP | PTP / IEEE 1588 |
|---|---|---|
| Typical accuracy (LAN) | 1-10 ms | 100 ns - 1 us |
| Hardware timestamping needed | No | Yes (for sub-us) |
| Used for | Log timestamps, general sync | Sensor fusion, TSN, automotive |
| Linux tools | `chrony`, `ntpd`, `systemd-timesyncd` | `ptp4l`, `phc2sys` |
| Profile for automotive | -- | IEEE 802.1AS (gPTP) |

---

## NIC Manufacturing Test

This is the heart of the role: take a board off the line, prove its NIC(s) are good, and
**reject the marginal ones**. Gate on measurable thresholds, not on "it seems to work."
A link that comes up but drops frames, negotiates the wrong speed, or accumulates CRC
errors under load is a field failure waiting to happen.

### The Standard NIC Test Sequence

```bash
# 1. Enumerate -- is the NIC present on the PCIe bus?
lspci | grep -i ethernet
lspci -vvv -s <bus:dev.fn> | grep -iE 'LnkCap|LnkSta'
# LnkSta should match LnkCap: Speed and Width (e.g., 8GT/s x4)

# 2. Link / speed / duplex -- did it negotiate the expected rate?
ethtool eth0
# Want: "Link detected: yes", correct Speed (e.g., 10000), "Duplex: Full"

# 3. Driver / firmware -- correct, expected versions
ethtool -i eth0
# Captures driver name, version, firmware-version, bus-info

# 4. Self-test -- NIC's built-in diagnostics
ethtool -t eth0 online
# online = non-disruptive; result must be PASS

# 5. Throughput to a partner / golden unit
iperf3 -c <partner-ip> -t 30 -P 4
# 10 GbE -> >9.5 Gbps; 1000BASE-T1 -> ~940 Mbps

# 6. Error counters -- must be ~0 after the throughput run
ethtool -S eth0 | grep -iE 'err|drop|crc|collision'

# 7. Cable integrity (copper / automotive)
ethtool --cable-test-tdr eth0
# OK, or reports: fault type (open/short/impedance mismatch) + distance in meters
```

### What "Good" Looks Like and the Gates

The test gates, expressed as boolean checks in code:

```python
checks = {
    "link_up":       link_detected,               # ethtool "Link detected: yes"
    "speed_ok":      negotiated_mbps >= expected_mbps,  # e.g., >= 1000 for 1GbE
    "duplex_full":   duplex == "Full",
    "self_test":     self_test_result == "PASS",
    "low_errors":    rx_errors + tx_errors < 10,   # combined threshold after iperf3
    "throughput_ok": measured_mbps >= min_mbps,    # e.g., >= 900 for a 1G link
    # for automotive / copper:
    "cable_ok":      cable_test_status != "fault",
}
# Unit passes only if ALL checks pass.
```

Design points that prevent false results:

- **Speed parsing**: `2.5G` must be parsed as 2500, not 25 (strip non-digits gives
  the wrong answer). Capture value and unit separately; multiply G-suffix by 1000.
- **Master/slave role**: captured from `ethtool` `master-slave cfg/status:` line and
  surfaced in the test summary, because role misconfiguration is the top "no link" cause
  on automotive PHYs.
- **Cable test is best-effort**: PHY/driver Time-Domain Reflectometry (TDR) support varies. An unsupported result is
  reported as `skipped`, not `fault` — do not false-fail a good board.
- **Delta counters**: snapshot `ethtool -S` before and after `iperf3`, diff the values.
  A NIC that links and passes self-test but accumulates CRC errors *under load* is the
  marginal unit you exist to catch. A minimal Python implementation:

```python
import subprocess, re

def ethtool_stats(iface):
    out = subprocess.check_output(["ethtool", "-S", iface], text=True)
    stats = {}
    for line in out.splitlines():
        m = re.match(r"\s+(\S+):\s+(\d+)", line)
        if m:
            stats[m.group(1)] = int(m.group(2))
    return stats

def delta(before, after):
    return {k: after[k] - before.get(k, 0) for k in after}

ERROR_KEYS = re.compile(r"err|crc|drop|collision|miss", re.I)

def check_nic(iface, run_iperf3):
    before = ethtool_stats(iface)
    run_iperf3()        # call your iperf3 fixture here
    after  = ethtool_stats(iface)
    d = delta(before, after)
    bad = {k: v for k, v in d.items() if ERROR_KEYS.search(k) and v > 0}
    return bad          # empty dict = pass; any non-zero key = reject
```

### Error Counters: What Each Means

| Counter | Meaning | Non-zero implies |
|---|---|---|
| `rx_errors` / `tx_errors` | Total receive/transmit errors | Physical or driver problem |
| `rx_crc_errors` | Frames failing CRC check | Signal integrity: cable, connector, PHY |
| `rx_dropped` / `tx_dropped` | Frames dropped (no buffer or no link) | Ring buffer overrun, link flap, CPU can't keep up |
| `collisions` / `late_collision` | (Half-duplex) collisions | Duplex mismatch (force full-duplex) |
| `rx_missed_errors` | NIC ring overflow | Host not reading fast enough: CPU/IRQ, driver |

### Loopback Options When No Partner Is Available

- **Internal/PHY loopback** via the driver (`ethtool -t offline` on some drivers; some
  drivers expose a loopback mode) — exercises the MAC/PHY datapath without a cable.
- **Physical loopback plug** on copper — connects Tx pairs back to Rx for a self-link.
- **Two NICs on the same DUT cabled together** — run `iperf3` server on one and client on
  the other for a self-contained throughput test.

A partner/golden unit on a known-good link is still the gold standard, because it
exercises the actual cable and the real far-end PHY negotiation.

---

## Automotive Ethernet for Test Engineers

The Zoox compute platform connects modules with automotive Ethernet. This section
focuses on the test and diagnostic angle. For full 100/1000BASE-T1 architecture and
in-vehicle networking design, see the Automotive chapter.

### Physical Layer — Single Pair, Special Line Codes

| Standard | Rate | Medium / line code | IEEE |
|---|---|---|---|
| 100BASE-T1 | 100 Mb/s | single pair, PAM3 | 802.3bw |
| 1000BASE-T1 | 1 Gb/s | single pair, PAM3 | 802.3bp |
| 2.5/5/10GBASE-T1 | 2.5-10 Gb/s | single pair, PAM4 | 802.3ch |
| 10BASE-T1S | 10 Mb/s | short multidrop, half-duplex | 802.3cg |

Key differences from consumer BASE-T Ethernet:

- **One pair, full-duplex with echo cancellation.** 1000BASE-T uses 4 pairs (8 wires);
  `*BASE-T1` runs full-duplex over a **single twisted pair**. Both transmit and receive
  share the wire; the PHY cancels its own echo. Fewer wires means lighter, cheaper
  vehicle harness.
- **Line coding.** 1000BASE-T1 uses **PAM3** (three voltage levels: -1, 0, +1);
  802.3ch multi-Gig uses **Pulse Amplitude Modulation 4-level (PAM4)** (four levels). Eye diagram and Signal Integrity (SI) analysis
  concepts from SerDes carry over.
- The trailing `1` in `*BASE-T1` is the tell: single pair, automotive.

### Master/Slave Clock Roles — the Number-One "No Link" Cause

Each automotive PHY link is configured as **master** or **slave** (a PHY-level timing
role for clock recovery). **The two ends must be opposite** — one master, one slave.

> A "no link" between correctly cabled, otherwise-healthy automotive PHYs is very often
> a **master/master or slave/slave misconfiguration**, not a cable fault.

The first thing to check on a dead `*BASE-T1` link is the role on each end, before
suspecting the harness. This is different from consumer Ethernet where roles are
auto-negotiated and invisible.

```bash
ethtool eth0
# Look for:
#   master-slave cfg: master       (or slave, or preferred master/slave)
#   master-slave status: master    (the actual negotiated result)
```

If both sides show `master` or both show `slave`, the link will not come up regardless
of cable quality.

### Real PHYs — Recognize the Part Numbers

| Vendor | Part | Capability |
|---|---|---|
| Marvell | 88Q2112 | 100/1000BASE-T1; integrates MDI termination |
| Marvell | 88Q222x / 88Q42xx | newer multi-Gig automotive PHY/switch family |
| TI | DP83TG721-Q1 | 1000BASE-T1 with TSN/AVB + TC10 sleep/wake |
| Broadcom | BCM8989x / BCM8957x | automotive multi-Gig PHY/switch |

### MDIO, ethtool, and the Cable Test TDR

- **MDIO** (Management Data I/O, clause 22/45) is the sideband management bus for reading
  and writing PHY registers: link status, master/slave role, error counters. Linux exposes
  PHYs through the netdev + **phylib** layer; most register access is via ethtool.
- **Cable test (TDR)** is the highest-value automotive manufacturing test, because it
  pinpoints the bad segment of a harness:

```bash
ethtool --cable-test eth0           # pass/fail per pair
ethtool --cable-test-tdr eth0       # TDR: fault type + distance to fault (meters)
```

TDR sends a pulse and times the reflection. Result codes:

| Code | Meaning |
|---|---|
| OK | Cable is good |
| Open | Open circuit; distance shown is meters to the break |
| Short | Short circuit between pairs |
| Impedance mismatch | Reflection from discontinuity: bad connector or crimp |
| Noise | Test could not complete |

The **distance-to-fault** is what makes TDR powerful on a vehicle harness: it tells you
*which connector or segment* is bad, rather than "the cable is broken somewhere." On a
manufacturing line this eliminates guesswork in rework.

### The Combined Diagnostic Chain

> **Good link + low iperf3 throughput + rising CRC errors → marginal signal integrity**
> (cable / connector / PHY). Confirm with `ethtool --cable-test-tdr` to localize it.

The link is up (roles and basic negotiation are fine); throughput is below spec; and
`ethtool -S` shows CRC errors climbing under load. That combination points squarely at
the analog/physical path. TDR gives you the distance to the fault so a tech fixes the
right connector.

### TC10 — Coordinated Sleep and Wake

**OPEN Alliance TC10** standardizes coordinated sleep and wake-up over automotive
Ethernet: **local wake**, **remote wake**, **wake-forwarding**, and **sleep negotiation**,
so an entire in-vehicle network can power down and a single event can wake it back up. As
a test step: verify the PHY enters sleep, wakes on the defined event, and forwards wake
correctly to downstream nodes. The TI DP83TG721-Q1 supports TC10.

### TSN — Time-Sensitive Networking

TSN is the suite of IEEE 802.1 standards that adds:

- **IEEE 802.1Qbv** — time-aware shaping: scheduled transmission windows so
  safety-critical frames are never delayed by bulk traffic.
- **IEEE 802.1Qbu / 802.3br** — frame preemption: high-priority frames can interrupt a
  large low-priority frame mid-transmission.
- **IEEE 802.1CB** — frame replication and elimination for redundancy.
- **IEEE 802.1AS (gPTP)** — the time synchronization backbone (see the PTP section
  above).

The test engineer's TSN checklist: (1) PTP converges and holds offset < 100 ns;
(2) QBV gates open/close at the right times and high-priority traffic meets its latency
budget; (3) the switch's credit-based shaper does not starve low-priority streams.

---

## Layer-by-Layer Troubleshooting Methodology

The discipline: **start at Layer 1 and climb**. Confirm each layer is sound before
blaming the one above it. This turns "I can't reach the instrument" into a deterministic
procedure. You can also divide and conquer (ping first to cover L1–L3 at once), but when
stuck, the bottom-up sweep never lies.

### The Ladder

**L1 Physical — is there a link?**

```bash
ip link show eth0         # UP? NO-CARRIER? LOWER_UP flag set?
ethtool eth0              # "Link detected: yes"? Correct speed?
ethtool -p eth0 5         # blink LED to confirm which physical port
```

Cable seated? Link LED on? Right port? For automotive single-pair: check master/slave
roles before touching the harness.

**L2 Data Link — is the framing clean?**

```bash
ethtool eth0                                         # Duplex: Full?
ethtool -S eth0 | grep -iE 'err|drop|crc|collision' # all should be ~0 or zero-delta
ip neigh show                                        # ARP resolved? REACHABLE vs FAILED
```

Rising CRC/align errors = physical-layer signal integrity issue (bad cable/connector/
PHY). A **duplex mismatch** shows as late collisions and poor throughput on an apparently
good link.

**L3 Network — addressing and routing correct?**

```bash
ip addr show eth0      # correct IP and mask? Not a stray 169.254.x.x?
ip route               # default gateway present? Route to target subnet?
ping -c3 <gateway>     # gateway reachable?
ping -c3 <target>      # target reachable?
```

Same-subnet check: if the target is on a different subnet and there is no matching route,
packets are dropped with "No route to host."

**L4 Transport — is the service up and reachable?**

```bash
ss -tuln | grep 8080      # on the server: is anything LISTENING?
nc -zv <host> 8080        # from the client: does the TCP handshake complete?
```

"Refused" → service not running, or wrong port, or bound to `127.0.0.1`. "Timed out" →
firewall or L3 problem (go back down a rung).

**Firewall — is traffic being blocked?**

```bash
sudo iptables -L -n -v      # legacy firewall rules with hit counters
sudo nft list ruleset        # nftables (modern)
```

A silent drop rule looks identical to a dead link from the client's side — `nc` "timed
out" with the host clearly up via ping is the tell. Check firewall on both ends and any
host in the path.

**L7 Application — is the protocol behaving?**

```bash
curl -v http://<host>:8080/api/stations    # correct status code and body?
```

`422`/`400` → client request shape is wrong (Pydantic validation failure: wrong
fields or types in the JSON body). `500` → server-side exception (read the server logs).
Hostname-only failure while IP works → DNS.

### Full Diagnostic Sequence — Paste and Run

```bash
ip link show eth0                        # L1: interface up? carrier?
ethtool eth0                             # L1/L2: link detected, speed, duplex
ethtool -S eth0 | grep -iE 'err|drop'   # L2: error counters (should be 0)
ip addr show eth0                        # L3: correct IP/mask?
ip route                                 # L3: default gateway / route to target?
ping -c 3 192.168.1.1                    # L3: gateway reachable?
ping -c 3 192.168.1.100                  # L3: target reachable?
nc -zv 192.168.1.100 8080                # L4: port open?
curl -v http://192.168.1.100:8080/       # L7: app responding correctly?
```

If step N is the first to fail, the fault is at that layer. Worked example: steps 1–7
pass, step 8 returns **"refused"** → network is perfect; the dashboard process is not
listening (crashed, or bound to `127.0.0.1`). No need to touch a cable.

**Automating the L3/L4 check from a test script** — useful for a pre-run connectivity
gate before the station starts a test sequence:

```python
import socket, subprocess, sys

def check_l3(host, count=3, timeout=1.0):
    """Returns True if host responds to ICMP echo (ping)."""
    rc = subprocess.call(
        ["ping", "-c", str(count), "-W", str(int(timeout)), host],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return rc == 0

def check_l4(host, port, timeout=2.0):
    """Returns ('open'|'refused'|'timeout') for the TCP port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "open"
    except ConnectionRefusedError:
        return "refused"
    except (socket.timeout, OSError):
        return "timeout"

# Pre-run gate: fail fast with a meaningful message
for host, port, label in [
    ("192.168.10.5", 5000, "power supply RPC"),
    ("10.10.100.50", 8080, "results dashboard"),
]:
    if not check_l3(host):
        sys.exit(f"PRE-RUN FAIL: {label} at {host} not reachable at L3 (ping failed)")
    state = check_l4(host, port)
    if state != "open":
        sys.exit(f"PRE-RUN FAIL: {label}:{port} TCP {state} -- service may be down")
```

### Common Failure Signatures

| Symptom | Root cause | Diagnostic step |
|---|---|---|
| `NO-CARRIER` on `ip link` | Cable unplugged, PHY dead, SFP missing | Seat cable, check LED, `ethtool eth0` |
| `169.254.x.x` address | DHCP failed | Wrong VLAN, DHCP server down, or link problem |
| `ping` works, name fails | DNS broken | `/etc/resolv.conf`, `/etc/hosts`, `dig @server name` |
| `nc` refused, `ping` works | Service not listening | `ss -tuln`, check process is running and bind address |
| `nc` timed out, `ping` works | Firewall dropping packets | `iptables -L -n -v` on both hosts |
| `iperf3` sawtooths | Packet loss -> congestion control | `ethtool -S` CRC errors, duplex check, MTU check |
| `iperf3` stable but low | PHY signal integrity marginal | `ethtool --cable-test-tdr`, check connector/cable |
| Many `CLOSE_WAIT` sockets | Missing `close()` in server loop | Code audit for unclosed context managers |
| "Address already in use" on restart | `TIME_WAIT` from prior run | `SO_REUSEADDR` before `bind()` |
| Duplex mismatch | Autoneg failure, forced mismatch | `ethtool -S` late collisions, force both to full |
| Automotive PHY no link | Master/slave misconfiguration | `ethtool eth0` master-slave status, reconfigure |
| Big transfers fail, small work | MTU/jumbo mismatch | `ping -M do -s 8972 host`, check switch MTU config |
| `ptp4l` stuck in s1 (uncertain) | No HW timestamps, switch no transparent clock, firewall blocking PTP multicast | `ethtool -T eth0`, switch config, `tcpdump` PTP ports 319/320 |

---

## HTTP, REST, and the FastAPI Dashboard

### HTTP Request/Response Anatomy

HTTP is a text-based request/response protocol over TCP (port 80; HTTPS = HTTP over TLS,
port 443). A request is `METHOD path HTTP/1.1` followed by headers and an optional body;
a response is `HTTP/1.1 status reason` followed by headers and a body.

### Methods and Idempotency

| Method | Meaning | Idempotent | Dashboard example |
|---|---|---|---|
| GET | Retrieve a resource | Yes | `GET /api/stations` -- list stations |
| POST | Create / submit / trigger | No | `POST /api/heartbeat` -- station heartbeat |
| PUT | Replace a resource at a known URI | Yes | `PUT /api/stations/s1` -- overwrite config |
| PATCH | Partial update | No | `PATCH /api/stations/s1` -- change one field |
| DELETE | Remove a resource | Yes | `DELETE /api/runs/42` |

**Idempotent** = doing it twice has the same effect as once — matters for safe retries.
A heartbeat POST that is retried on network error must not corrupt state by recording
duplicate events.

### Status Codes

- **2xx success** — `200 OK`, `201 Created` (after POST), `204 No Content`.
- **4xx client error** — `400 Bad Request`, `401 Unauthorized`, `403 Forbidden`,
  `404 Not Found`, `422 Unprocessable Entity` (FastAPI/Pydantic validation failure —
  wrong field types or missing required fields), `429 Too Many Requests`.
- **5xx server error** — `500 Internal Server Error` (unhandled exception), `502 Bad
  Gateway`, `503 Service Unavailable`, `504 Gateway Timeout`.

Rule: **4xx = the client sent something wrong; 5xx = the server blew up.** A `422` from
FastAPI almost always means the JSON body does not match the Pydantic model — fix the
request, not the server.

### `curl` as an HTTP Multimeter

```bash
# GET
curl http://dashboard:8080/api/stations

# POST with JSON body
curl -X POST http://dashboard:8080/api/heartbeat \
     -H "Content-Type: application/json" \
     -d '{"hostname":"station1","fat_state":"RUNNING"}'

# Verbose: see both request and response headers
curl -v http://dashboard:8080/api/stations

# Print only the HTTP status code (scripting-friendly)
curl -s -o /dev/null -w "%{http_code}\n" http://dashboard:8080/

# Timing breakdown: separate DNS, connect, and total time
curl -w "dns:%{time_namelookup}s  connect:%{time_connect}s  total:%{time_total}s\n" \
     -s -o /dev/null http://dashboard:8080/
```

The timing breakdown (`-w`) is useful for "the dashboard is slow" reports: it separates
DNS resolution time, TCP connect time, and total time, pinpointing whether to blame name
resolution, the network, or the server-side handler.

---

## Pointer to the Automotive Chapter

100/1000BASE-T1 physical layer design, vehicle Ethernet topology, the switch fabric
architecture, domain controller interconnects, and the full in-vehicle networking stack
are covered in the Automotive chapter. This chapter's NIC test sequence, master/slave
diagnostics, TDR cable test, and PTP/TSN sections are the manufacturing-floor view of
that same technology.
