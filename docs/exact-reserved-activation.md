# Exact reserved display activation

A VM's existing screen may also be named Virtual-*. A protected remote-login
activation must use the exact connector already validated by the host's DRM
identity/EDID checks. It must not infer which monitor is new from its name or
resolution. GNOME activation with expected_output_name uses only that name,
rejects duplicates/missing targets, and supports reactivation of an existing
connector. Legacy ordinary creation without an exact target retains discovery.

Desktop preparation snapshots compositor identities before connecting the
reserved output. Existing non-target monitor identities must still match before
layout application and during activation confirmation. The layout builder retains
existing screen modes, positions, scales and primary status.

The desktop snapshot is bounded at 3 seconds. Reserved preparation is bounded at
12 seconds; the activation worker receives at most 10 seconds and leaves one
second of the remaining budget for final DRM confirmation. Exact-target discovery
waits at most 4 seconds. This fits within the 18-second D-Bus/control request and
20-second authenticated launch exchange. The separate display removal grace
period is unchanged.

Verification includes the two-Virtual-screen/same-mode regression, duplicate and
missing targets, reactivation, screen replacement rejection, snapshot ordering,
and worker deadline limits. The deployed host also performed a real activation
and cleanup of card2/Virtual-2 while preserving the QEMU screen.
