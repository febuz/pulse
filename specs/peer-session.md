# Peer Session

A peer session is the first conversation between two Pulse peers.

Minimum exchange:

1. protocol version
2. peer identity
3. supported transports
4. world profile
5. capabilities
6. requested objects or routes

Capabilities should be explicit and narrow. A peer can be a browser, host, provider, relay,
directory, or development tool.

