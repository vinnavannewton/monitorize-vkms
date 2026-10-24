# Passwordless display creation

The installed system D-Bus service supports `GetAuthorizationState` and
`CreateDisplayUnattended`. The service derives the caller UID from the unique
D-Bus sender; no caller-supplied UID is accepted. Silent creation requires an
explicit administrator-approved grant matching the local account's UID,
username, home directory and home inode, plus a verified active local desktop session.
Explicit manual creation can use Polkit when no valid grant exists.
Revoked grants retain a disabled record so existing displays can still clean up.

Monitorize Settings enables or disables a grant through the fixed
`/usr/libexec/monitorize-vkms/monitorize-vkms-authorization` helper. Its only
arguments are `enable` and `disable`; Polkit supplies the invoking account.
Grants are root-owned files under `/var/lib/monitorize-vkms/authorization`.
Missing, malformed, writable, symlinked or account-mismatched grants are never
accepted. Disabling applies to future requests and does not remove a live
virtual display. An already authorized operation may finish during revocation.

The helper and its compositor worker can execute from both installed layouts:
`/usr/libexec/monitorize-vkms/monitorize-vkms-helper` and
`/usr/lib/monitorize-vkms/monitorize_vkms/helper.py`. Each explicitly sets the
package import root before imports, so isolated Python (`-I`) works when an
imported helper starts its own package copy as the session user.

Boot leases and protected remote-login output checks remain part of the host
package. Ordinary unattended creation cannot replace a protected login output.
The Python host update does not rebuild or reload the kernel module.
