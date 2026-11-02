"""CRYPTUS isolated signer.

Runs as a separate process / container with its own encrypted keystore and its
own policy file. It never connects to the gateway database and never talks to
blockchains: it only validates structured signing requests against policy and
returns signatures.
"""
